"""Collect, sanitize and archive diagnostics for one run (a new attempt on every call)."""

from __future__ import annotations

from pathlib import Path

from .collect import (
    FAILED,
    OK,
    TIMEOUT,
    UNAVAILABLE,
    Collector,
    collect,
    make_archive,
    sanitize_archive,
)
from .context import LabContext
from .runner import run_logged
from .stages import GUEST_DIR
from .store import atomic_write_json, ensure_private_dir, read_json

COLLECT_TIMEOUT = 10 * 60
INSPECT_TIMEOUT = 5 * 60


def collectors(ctx: LabContext, run_id: str, server_id: str | None) -> list[Collector]:
    rec, g = ctx.record, "guest"
    osh = f"{rec.home}/{GUEST_DIR}/os.sh"
    items = [
        Collector("terraform-version", ("terraform", "version"), "host", 30),
        Collector("lxd-version", ("lxc", "version"), "host", 30),
        Collector("lxd-instance", ("lxc", "info", rec.vm_name, "--project", rec.project),
                  "host", 60),
        Collector("lxd-instance-config",
                  ("lxc", "config", "show", rec.vm_name, "--project", rec.project), "host", 60),
        Collector("cloud-init-status", ("cloud-init", "status", "--long"), g, 60),
        Collector("cloud-init-log", ("sudo", "tail", "-n", "2000", "/var/log/cloud-init.log"),
                  g, 60),
        Collector("snap-list", ("snap", "list"), g, 60),
        Collector("snap-connections", ("snap", "connections", "openstack"), g, 60),
        Collector("snap-services", ("snap", "services"), g, 60),
        Collector("clusterd-journal", ("sudo", "journalctl", "-u", "snap.openstack.clusterd",
                                       "-n", "500", "--no-pager"), g, 120),
        Collector("sunbeam-cluster-list", ("sunbeam", "cluster", "list"), g, 120),
        Collector("juju-status", ("juju", "status", "--format", "yaml"), g, 120),
        Collector("stage-logs", ("bash", "-c", 'tail -n 300 "$HOME"/sandbed/stages/"$1"/*/out.log',
                                 "_", run_id), g, 60),
        Collector("openstack-servers", ("bash", osh, "server", "list", "-f", "json"), g, 120),
        Collector("openstack-networks", ("bash", osh, "network", "list", "-f", "json"), g, 120),
    ]
    if server_id:
        items.append(Collector("instance-console",
                               ("bash", osh, "console", "log", "show", server_id), g, 120))
    return items


def make_executor(ctx: LabContext, log_dir: Path):
    def execute(c: Collector) -> tuple[str, str, str]:
        if c.where == "guest" and not ctx.record.vm_address:
            return UNAVAILABLE, "", "the lab has no VM address"
        argv = list(c.argv) if c.where == "host" else ctx.target().ssh_argv(list(c.argv))
        result = run_logged(argv, log_path=log_dir / f"{c.name}.cmd.log", timeout=c.timeout,
                            tail_bytes=c.max_bytes)
        if result.timed_out:
            return TIMEOUT, result.tail, f"timed out after {c.timeout:g}s"
        if result.returncode == 0:
            return OK, result.tail, ""
        missing = result.returncode == 127 or "command not found" in result.tail
        return (UNAVAILABLE if missing else FAILED), result.tail, f"exit {result.returncode}"

    return execute


def collect_inspection(ctx: LabContext, out_dir: Path, raw_dir: Path) -> dict:
    """`sunbeam inspect` archive: pulled to the private dir, only sanitized members exported."""
    target, log = ctx.target(), raw_dir / "inspect.cmd.log"
    made = run_logged(target.ssh_argv(["sunbeam", "inspect"]), log_path=log,
                      timeout=INSPECT_TIMEOUT)
    newest = run_logged(target.ssh_argv([
        "bash", "-c", 'ls -t "$HOME"/sunbeam-inspection-report-*.tar.gz | head -1']),
        log_path=log, timeout=60)
    remote = newest.tail.strip().splitlines()[-1] if newest.ok and newest.tail.strip() else ""
    if not made.ok or not remote.startswith("/"):
        return {"name": "sunbeam-inspect", "status": FAILED, "omissions": []}
    local = raw_dir / "inspect.tar.gz"
    run_logged(target.scp_from_argv(remote, local), log_path=log, timeout=COLLECT_TIMEOUT,
               check=True)
    omissions = sanitize_archive(local, out_dir / "sunbeam-inspect")
    return {"name": "sunbeam-inspect", "status": OK, "omissions": omissions}


def collect_run(ctx: LabContext, run_id: str) -> dict:
    """One collection attempt. Never raises for collector problems: they are reported."""
    run_dir = ensure_private_dir(ctx.paths.run(run_id))
    attempt = 1 + sum(1 for p in run_dir.glob("diagnostics*") if p.is_dir())
    suffix = "" if attempt == 1 else f"-{attempt}"
    out_dir, archive = run_dir / f"diagnostics{suffix}", run_dir / f"diagnostics{suffix}.tar.gz"
    raw_dir = ensure_private_dir(ctx.paths.private / "raw" / run_id / str(attempt))
    evidence = read_json(run_dir / "scenario-evidence.json", default={}) or {}
    index = collect(collectors(ctx, run_id, evidence.get("server_id")),  # type: ignore[union-attr]
                    make_executor(ctx, raw_dir), raw_dir=raw_dir, out_dir=out_dir)
    if ctx.record.vm_address:
        try:
            inspect = collect_inspection(ctx, out_dir, raw_dir)
        except Exception as err:
            inspect = {"name": "sunbeam-inspect", "status": FAILED, "omissions": [],
                       "error": f"{type(err).__name__}: {err}"}
        index["entries"].append(inspect)
        index["complete"] = index["complete"] and inspect["status"] == OK
        atomic_write_json(out_dir / "index.json", index)
    make_archive(out_dir, archive)
    return {"status": "complete" if index["complete"] else "partial",
            "diagnostics_path": str(archive), "attempt": attempt, "index": index}