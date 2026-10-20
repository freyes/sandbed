from __future__ import annotations

from .config import Config
from .shell import PhaseError, run


def _installed(cmd: list[str]) -> bool:
    return run(cmd, check=False).returncode == 0


def _microceph_bootstrap(ctx: Config) -> None:
    # single-node file-backed (loop) OSD
    img = ctx.microceph_osd_img
    if not _installed(["snap", "list", "microceph"]):
        run(["sudo", "snap", "install", "microceph"])
    if not _installed(["sudo", "microceph", "cluster", "list"]):
        run(["sudo", "microceph", "cluster", "bootstrap"])
        run(["truncate", "-s", f"{ctx.microceph_osd_size}G", img])
        loop = run(["losetup", "--find", "--show", img]).stdout.strip()
        run(["sudo", "microceph", "disk", "add", loop, "--wipe"])


def _ensure_rbd_pool(ctx: Config) -> None:
    pool = ctx.ceph_rbd_pool
    pools = run(["sudo", "microceph.rados", "lspools"]).stdout
    if pool in pools.split():
        return
    run(["sudo", "microceph.ceph", "osd", "pool", "create", pool])
    run(["sudo", "microceph.ceph", "osd", "pool", "application", "enable", pool, "rbd"])


def verify_ceph(ctx: Config) -> None:
    # Ceph must be functional BEFORE terraform creates the `ceph` LXD storage pool.
    # A single-node microceph reports HEALTH_WARN by design, so we don't require
    # HEALTH_OK; we require the cluster reachable, >=1 OSD `up`, and the RBD pool present.
    run(["sudo", "microceph.ceph", "-s"], check=True)

    osd_stat = run(["sudo", "microceph.ceph", "osd", "stat"]).stdout
    if " up " not in osd_stat:
        raise PhaseError(["sudo", "microceph.ceph", "osd", "stat"], "/tmp/sandbed.log", 1)

    _ensure_rbd_pool(ctx)

    pools = run(["sudo", "microceph.rados", "lspools"]).stdout
    if ctx.ceph_rbd_pool not in pools.split():
        raise PhaseError(["sudo", "microceph.rados", "lspools"], "/tmp/sandbed.log", 1)


def install(ctx: Config) -> None:
    if not _installed(["lxc", "info"]):
        run(["sudo", "snap", "install", "lxd"])
        run(["sudo", "lxd", "init", "--auto"])
    _microceph_bootstrap(ctx)
    verify_ceph(ctx)
    if not _installed(["terraform", "version"]):
        run(
            ["wget", "-O-", "https://apt.releases.hashicorp.com/gpg"],
            log_path="/tmp/sunbeam-apt.log",
        )
        run(["sudo", "apt-get", "update"])
        run(["sudo", "apt-get", "install", "-y", "terraform"])
    if not _installed(["snap", "list", "openstack"]):
        run(["sudo", "snap", "install", "openstack"])
