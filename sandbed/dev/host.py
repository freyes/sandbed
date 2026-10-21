"""Host prerequisites: a read-only check and an explicit, idempotent prepare.

The shared backend (MicroCeph, the RBD pool and the LXD pool wrapper over it) belongs to
`prepare`. Labs only reference the pool by name and their teardown never touches it.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path

from ..shell import run
from .errors import HostError
from .labs import STORAGE_POOL
from .paths import state_root
from .store import atomic_write_json

MIN_FREE_GIB = 300  # provisional; calibrate on the qualified host
PREP_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "prep-host.sh"
DEFAULT_OSD_SIZE = "100G"  # provisional: 3 sparse loop OSDs of this size


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str
    remediation: str = ""


PROBE_TIMEOUT = 30.0


def _probe(cmd: list[str]) -> tuple[int, str]:
    """Read-only check command: never prompts (stdin closed, `sudo -n`) and never hangs.
    Returns (returncode, stdout); 124 on timeout, 127 if the program is missing."""
    argv = ["sudo", "-n", *cmd[1:]] if cmd[0] == "sudo" else cmd
    try:
        done = subprocess.run(
            argv, stdin=subprocess.DEVNULL, capture_output=True, text=True,
            timeout=PROBE_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return 124, ""
    except FileNotFoundError:
        return 127, ""
    return done.returncode, done.stdout.strip()


def _ok(cmd: list[str]) -> bool:
    return _probe(cmd)[0] == 0


def _out(cmd: list[str]) -> str:
    code, out = _probe(cmd)
    return out if code == 0 else ""


def _run_ok(cmd: list[str]) -> bool:
    return run(cmd, check=False).returncode == 0


def _run_out(cmd: list[str]) -> str:
    result = run(cmd, check=False)
    return (result.stdout or "").strip() if result.returncode == 0 else ""


def _nested_kvm() -> tuple[bool, str]:
    for module in ("kvm_intel", "kvm_amd"):
        path = Path(f"/sys/module/{module}/parameters/nested")
        if path.exists():
            value = path.read_text().strip()
            return value in ("1", "Y", "y"), f"{module} nested={value}"
    return False, "no kvm_intel/kvm_amd module parameters"


def check_host() -> list[Check]:
    """Read-only. Never installs, creates or deletes anything."""
    nested_ok, nested_detail = _nested_kvm()
    free_gib = shutil.disk_usage("/var/snap" if os.path.isdir("/var/snap") else "/").free >> 30
    lxd_ok = _ok(["lxc", "query", "/1.0"])
    pool = _out(["lxc", "storage", "show", STORAGE_POOL])
    rbd = _out(["sudo", "microceph.rados", "lspools"]).split()
    osd = _out(["sudo", "microceph.ceph", "osd", "stat"])
    return [
        Check("sudo", _ok(["sudo", "true"]), "sudo -n true",
              "run `sudo -v` first or allow passwordless sudo for the MicroCeph commands"),
        Check("kvm", os.path.exists("/dev/kvm"), "/dev/kvm", "enable KVM on the host"),
        Check("nested-virt", nested_ok, nested_detail, "enable nested virtualization"),
        Check("lxd", lxd_ok, "lxc query /1.0", "run `sandbed dev host prepare`"),
        Check("microceph", _ok(["sudo", "microceph", "cluster", "list"]), "cluster list",
              "run `sandbed dev host prepare`"),
        Check("ceph-osd-up", " up " in osd, osd or "no output", "run `sandbed dev host prepare`"),
        Check("rbd-pool", STORAGE_POOL in rbd, f"rados pool {STORAGE_POOL}",
              "run `sandbed dev host prepare`"),
        Check("lxd-pool", "driver: ceph" in pool and f"ceph.osd.pool_name: {STORAGE_POOL}" in pool,
              f"lxc storage show {STORAGE_POOL}", "run `sandbed dev host prepare`"),
        Check("terraform", _ok(["terraform", "version"]), "terraform version",
              "snap install terraform --classic"),
        Check("snapcraft", _ok(["snapcraft", "--version"]), "snapcraft --version",
              "snap install snapcraft --classic"),
        Check("squashfs-tools", shutil.which("unsquashfs") is not None, "unsquashfs",
              "apt install squashfs-tools"),
        Check("disk", free_gib >= MIN_FREE_GIB, f"{free_gib} GiB free (need {MIN_FREE_GIB})",
              "free disk space"),
    ]


def prepare_host(osd_size: str = DEFAULT_OSD_SIZE) -> dict:
    """Install and bootstrap the shared backend, then create the dev RBD and LXD pools.

    Reuses scripts/prep-host.sh unchanged (Testflinger runs it too). Never replaces or wipes a
    healthy existing backend: the script skips bootstrap and disks that already exist.
    """
    if not PREP_SCRIPT.exists():
        raise HostError(f"missing {PREP_SCRIPT}; run from a source checkout")
    run(["sudo", str(PREP_SCRIPT), osd_size], capture_output=False)
    if STORAGE_POOL not in _run_out(["sudo", "microceph.rados", "lspools"]).split():
        run(["sudo", "microceph.ceph", "osd", "pool", "create", STORAGE_POOL])
        run(["sudo", "microceph.ceph", "osd", "pool", "application", "enable", STORAGE_POOL, "rbd"])
    if not _run_ok(["lxc", "storage", "show", STORAGE_POOL]):
        run([
            "lxc", "storage", "create", STORAGE_POOL, "ceph",
            f"ceph.osd.pool_name={STORAGE_POOL}", "ceph.cluster_name=ceph", "ceph.user.name=admin",
        ])
    record = {
        "osd_size": osd_size,
        "storage_pool": STORAGE_POOL,
        "lxd_version": _run_out(["lxc", "version"]),
        "microceph": _run_out(["snap", "list", "microceph"]),
    }
    atomic_write_json(state_root() / "host.json", record)
    failed = [c for c in check_host() if not c.ok]
    if failed:
        raise HostError("host still not ready: " + ", ".join(c.name for c in failed))
    return record


def checks_payload(checks: list[Check]) -> dict:
    return {"ok": all(c.ok for c in checks), "checks": [asdict(c) for c in checks]}