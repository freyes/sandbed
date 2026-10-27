"""Host prerequisites: a read-only check and an explicit, idempotent prepare.

The shared backend (MicroCeph, the RBD pool and the LXD pool wrapper over it) belongs to
`prepare`. Labs only reference the pool by name and their teardown never touches it.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path

from ..shell import run
from .errors import HostError, InputError
from .labs import IMAGE, STORAGE_POOL
from .paths import state_root
from .store import atomic_write_json

MIN_FREE_GIB = 50  # provisional; calibrate on the qualified host
PREP_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "prep-host.sh"
DEFAULT_OSD_SIZE = "100G"  # provisional: 3 sparse loop OSDs of this size

# Storage backends `sandbed dev` knows how to provision. `ceph` (MicroCeph) is the canonical
# choice on a bare host; `dir` is the only viable choice inside an unprivileged container.
BACKENDS = ("microceph", "dir")
_DEVLXD = Path("/dev/lxd/sock")  # present inside an LXD container; patchable in tests
LXD_DIR = "/var/snap/lxd/common/lxd"  # where LXD puts a dir pool created without `source`


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


def in_container() -> bool:
    """True inside an LXD/systemd container. Never raises."""
    code, out = _probe(["systemd-detect-virt", "--container"])
    if code == 0 and out and out != "none":
        return True
    try:
        return _DEVLXD.exists()
    except OSError:
        return False


def pool_driver() -> str | None:
    """Driver of the shared LXD pool, or None if the pool/command is absent."""
    out = _out(["lxc", "storage", "show", STORAGE_POOL])
    if not out:
        return None
    for line in out.splitlines():
        if line.strip().startswith("driver:"):
            return line.split(":", 1)[1].strip()
    return None


def _image_field(out: str, field: str) -> str | None:
    for line in out.splitlines():
        key, sep, value = line.strip().partition(":")
        if sep and key.lower() == field.lower():
            return value.strip()
    return None


def image_imported() -> bool:
    """Read-only: the pinned VM image resolves locally in LXD's default project.

    No network fetch: `lxc image show` resolves against the local daemon only.
    """
    return _ok(["lxc", "image", "show", IMAGE["alias"]])


def resolve_backend(requested: str, existing_driver: str | None, container: bool) -> str:
    """Pick the storage backend, refusing to silently replace an incompatible existing pool.

    `requested` may be `auto` (let the environment decide) or one of `BACKENDS`. An existing
    pool is never replaced: a driver we cannot map, or a mismatch with an explicit request,
    raises `HostError`. An invalid request raises `InputError`.
    """
    if requested not in ("auto", *BACKENDS):
        raise InputError(f"invalid storage backend: {requested}")
    if existing_driver is not None:
        mapped = {"ceph": "microceph", "dir": "dir"}.get(existing_driver)
        if mapped is None:
            raise HostError(
                f"pool {STORAGE_POOL} uses unsupported driver {existing_driver}; "
                "it is never replaced"
            )
        if requested != "auto" and mapped != requested:
            raise HostError(
                f"pool {STORAGE_POOL} exists with driver {existing_driver}, "
                f"requested {requested}; it is never replaced"
            )
        return mapped
    if requested != "auto":
        return requested
    return "dir" if container else "microceph"


def _nested_kvm() -> tuple[bool, str]:
    for module in ("kvm_intel", "kvm_amd"):
        path = Path(f"/sys/module/{module}/parameters/nested")
        if path.exists():
            value = path.read_text().strip()
            return value in ("1", "Y", "y"), f"{module} nested={value}"
    return False, "no kvm_intel/kvm_amd module parameters"


def _pool_field(out: str, field: str) -> str | None:
    """Parse a `field:` value from `lxc storage show` output (one call, multiple fields)."""
    for line in out.splitlines():
        stripped = line.strip()
        if stripped.startswith(f"{field}:"):
            return stripped.split(":", 1)[1].strip()
    return None


def check_host() -> list[Check]:
    """Read-only. Never installs, creates or deletes anything."""
    container = in_container()
    pool_out = _out(["lxc", "storage", "show", STORAGE_POOL])
    driver = _pool_field(pool_out, "driver")
    try:
        backend = resolve_backend("auto", driver, container)
    except HostError:
        backend = "unsupported"
    nested_ok, nested_detail = _nested_kvm()
    lxd_ok = _ok(["lxc", "query", "/1.0"])

    checks: list[Check] = [
        Check("sudo", _ok(["sudo", "true"]), "sudo -n true",
              "run `sudo -v` first or allow passwordless sudo for the MicroCeph commands"),
        Check("kvm", os.path.exists("/dev/kvm"), "/dev/kvm", "enable KVM on the host"),
        Check("nested-virt", nested_ok, nested_detail, "enable nested virtualization"),
        Check("lxd", lxd_ok, "lxc query /1.0", "run `sandbed dev host prepare`"),
    ]

    if backend == "microceph":
        rbd = _out(["sudo", "microceph.rados", "lspools"]).split()
        osd = _out(["sudo", "microceph.ceph", "osd", "stat"])
        checks.extend([
            Check("microceph", _ok(["sudo", "microceph", "cluster", "list"]), "cluster list",
                  "run `sandbed dev host prepare`"),
            Check("ceph-osd-up", " up " in osd, osd or "no output",
                  "run `sandbed dev host prepare`"),
            Check("rbd-pool", STORAGE_POOL in rbd, f"rados pool {STORAGE_POOL}",
                  "run `sandbed dev host prepare`"),
            Check("lxd-pool",
                  "driver: ceph" in pool_out and f"ceph.osd.pool_name: {STORAGE_POOL}" in pool_out,
                  f"lxc storage show {STORAGE_POOL}", "run `sandbed dev host prepare`"),
        ])
    elif backend == "dir":
        checks.append(
            Check("lxd-pool", driver == "dir", f"lxc storage show {STORAGE_POOL}",
                  "run `sandbed dev host prepare`")
        )
    else:  # unsupported
        checks.append(
            Check("lxd-pool", False, f"unsupported driver {driver}",
                  "remove or rename the pool; sandbed never replaces it")
        )

    checks.append(Check(
        "image", image_imported(), f"lxc image show {IMAGE['alias']}",
        "run `sandbed dev host prepare`",
    ))

    if container:
        hostname = socket.gethostname()
        devices = ("/dev/kvm", "/dev/vhost-net", "/dev/vhost-vsock", "/dev/net/tun")
        missing = [d for d in devices if not os.path.exists(d)]
        checks.append(Check(
            "vm-devices", not missing,
            "missing: " + ", ".join(missing) if missing else "all present",
            f"pass through KVM/vhost/TUN devices: "
            f"`lxc config set {hostname} security.nesting=true; "
            f"lxc config device add {hostname} kvm unix-char source=/dev/kvm; "
            f"lxc config device add {hostname} vhost-net unix-char source=/dev/vhost-net; "
            f"lxc config device add {hostname} vhost-vsock unix-char source=/dev/vhost-vsock; "
            f"lxc config device add {hostname} tun unix-char source=/dev/net/tun`",
        ))
        try:
            env = json.loads(_out(["lxc", "query", "/1.0"]))
        except (json.JSONDecodeError, ValueError):
            env = {}
        lxd_driver = env.get("environment", {}).get("driver", "")
        # LXD probes its instance drivers once, at daemon start: devices passed through later
        # stay unused until the daemon reloads.
        checks.append(Check(
            "lxd-vm", "qemu" in lxd_driver,
            f"environment.driver: {lxd_driver}" if lxd_driver else "no output",
            "LXD started before the KVM devices existed; run "
            "`sudo systemctl reload snap.lxd.daemon`" if not missing
            else "pass through the vm-devices first, then run "
            "`sudo systemctl reload snap.lxd.daemon`",
        ))

    if backend == "dir" and driver is not None:
        source_path = _pool_field(pool_out, "source")
        disk_path = source_path or ("/var/snap" if os.path.isdir("/var/snap") else "/")
    else:
        disk_path = "/var/snap" if os.path.isdir("/var/snap") else "/"
    free_gib = shutil.disk_usage(disk_path).free >> 30
    checks.extend([
        Check("terraform", _ok(["terraform", "version"]), "terraform version",
              "snap install terraform --classic"),
        Check("snapcraft", _ok(["snapcraft", "--version"]), "snapcraft --version",
              "snap install snapcraft --classic"),
        Check("squashfs-tools", shutil.which("unsquashfs") is not None, "unsquashfs",
              "apt install squashfs-tools"),
        Check("disk", free_gib >= MIN_FREE_GIB, f"{free_gib} GiB free (need {MIN_FREE_GIB})",
              "free disk space"),
    ])
    return checks


def prepare_host(
    osd_size: str = DEFAULT_OSD_SIZE,
    backend: str = "auto",
    source: str | None = None,
) -> dict:
    """Install and bootstrap the shared backend, then create the dev pools.

    Reuses scripts/prep-host.sh unchanged (Testflinger runs it too). Never replaces or wipes a
    healthy existing backend: the script skips bootstrap and disks that already exist.
    """
    if not PREP_SCRIPT.exists():
        raise HostError(f"missing {PREP_SCRIPT}; run from a source checkout")
    container = in_container()
    pool_out = _run_out(["lxc", "storage", "show", STORAGE_POOL])
    driver = _pool_field(pool_out, "driver")
    chosen = resolve_backend(backend, driver, container)
    if source is not None and chosen != "dir":
        raise InputError("--storage-source requires the dir backend")
    if source is not None:
        src_path = Path(source)
        if not src_path.is_absolute() or not src_path.is_dir():
            raise InputError(
                f"--storage-source must be an absolute existing directory: {source}"
            )
    if chosen == "dir" and driver is None:
        # Fail before anything changes: a pool created on a too-small filesystem cannot be
        # moved later (it is never replaced).
        target = source or next(p for p in (LXD_DIR, "/var/snap", "/") if os.path.isdir(p))
        free_gib = shutil.disk_usage(target).free >> 30
        if free_gib < MIN_FREE_GIB:
            raise HostError(
                f"{target} has {free_gib} GiB free (need {MIN_FREE_GIB}); pass "
                "--storage-source on a larger filesystem"
            )
    if chosen == "microceph":
        run(["sudo", str(PREP_SCRIPT), osd_size], capture_output=False)
        if STORAGE_POOL not in _run_out(["sudo", "microceph.rados", "lspools"]).split():
            run(["sudo", "microceph.ceph", "osd", "pool", "create", STORAGE_POOL])
            run(["sudo", "microceph.ceph", "osd", "pool", "application", "enable",
                 STORAGE_POOL, "rbd"])
        if not _run_ok(["lxc", "storage", "show", STORAGE_POOL]):
            run([
                "lxc", "storage", "create", STORAGE_POOL, "ceph",
                f"ceph.osd.pool_name={STORAGE_POOL}", "ceph.cluster_name=ceph",
                "ceph.user.name=admin",
            ])
    else:  # dir
        if driver == "dir" and source is not None:
            existing_source = _pool_field(pool_out, "source")
            if existing_source is not None and existing_source != source:
                raise HostError(
                    f"pool {STORAGE_POOL} exists with source {existing_source}, "
                    f"requested {source}; it is never replaced"
                )
        run(["sudo", "env", "SANDBED_PREP_MICROCEPH=0", str(PREP_SCRIPT), osd_size],
            capture_output=False)
        if not _run_ok(["lxc", "storage", "show", STORAGE_POOL]):
            create_args = ["lxc", "storage", "create", STORAGE_POOL, "dir"]
            if source:
                create_args.append(f"source={source}")
            run(create_args)

    if not _run_ok(["lxc", "image", "show", IMAGE["alias"]]):
        run(["lxc", "image", "copy", f"{IMAGE['remote']}:{IMAGE['alias']}",
             "local:", "--alias", IMAGE["alias"], "--vm"], capture_output=False)
    image_info = _run_out(["lxc", "image", "info", IMAGE["alias"]])
    if (_image_field(image_info, "type") != IMAGE["type"]
            or _image_field(image_info, "architecture") != IMAGE["architecture"]):
        raise HostError(
            f"staged image {IMAGE['alias']} is not a {IMAGE['type']} "
            f"{IMAGE['architecture']} image"
        )
    image_fingerprint = _image_field(image_info, "fingerprint")

    record: dict = {
        "storage_backend": chosen,
        "storage_source": source,
        "in_container": container,
        "storage_pool": STORAGE_POOL,
        "image_remote": IMAGE["remote"],
        "image_alias": IMAGE["alias"],
        "image_fingerprint": image_fingerprint,
        "lxd_version": _run_out(["lxc", "version"]),
    }
    if chosen == "microceph":
        record["osd_size"] = osd_size
        record["microceph"] = _run_out(["snap", "list", "microceph"])
    atomic_write_json(state_root() / "host.json", record)
    failed = [c for c in check_host() if not c.ok]
    if failed:
        raise HostError("host still not ready: " + ", ".join(c.name for c in failed))
    return record


def host_facts() -> dict:
    """Re-probe the host for the two facts that appear in the JSON payload."""
    container = in_container()
    driver = pool_driver()
    try:
        backend = resolve_backend("auto", driver, container)
    except HostError:
        backend = "unsupported"
    return {"storage_backend": backend, "in_container": container}


def checks_payload(checks: list[Check]) -> dict:
    return {"ok": all(c.ok for c in checks), "checks": [asdict(c) for c in checks],
            **host_facts()}