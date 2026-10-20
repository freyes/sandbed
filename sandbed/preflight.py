from __future__ import annotations

import ipaddress
import os

from .config import Config
from .shell import run


class PreflightError(RuntimeError):
    def __init__(self, msg: str, remediation: str) -> None:
        self.remediation = remediation
        super().__init__(msg + " — " + remediation)


def _kvm_present() -> bool:
    return os.path.exists("/dev/kvm")


def lxd_https_address(ctx: Config) -> str:
    """Address LXD must listen on so MAAS can power-drive the VMs: <generic gateway>:<port>."""
    gateway = next(ipaddress.ip_network(ctx.generic_cidr).hosts())
    return f"{gateway}:{ctx.lxd_https_port}"


def _check_lxd_https(ctx: Config) -> None:
    expected = lxd_https_address(ctx)
    result = run(["lxc", "config", "get", "core.https_address"], check=False)
    current = (result.stdout or "").strip()
    if current and current != expected:
        raise PreflightError(
            f"LXD core.https_address is already set to {current}",
            f"unset it (`lxc config unset core.https_address`) or set it to {expected}",
        )


def check(ctx: Config) -> None:
    if not _kvm_present():
        raise PreflightError("no /dev/kvm", "enable nested virtualization / KVM on the host")
    if run(["lxc", "query", "/1.0"], check=False).returncode != 0:
        raise PreflightError("LXD not reachable", "run `snap install lxd && lxd init --auto`")
    if run(["sudo", "microceph", "cluster", "list"], check=False).returncode != 0:
        raise PreflightError("microceph not reachable", "snap install microceph + bootstrap")
    if ctx.provider == "maas":
        if not ctx.ssh_key:
            raise PreflightError(
                "no SSH public key for the Sunbeam client VM",
                "pass --ssh-key (or set SUNBEAM_SSH_KEY) with a key whose private half is on "
                "the machine running sandbed, or create one with `ssh-keygen -t ed25519`",
            )
        _check_lxd_https(ctx)
    if ctx.shared_dir and not os.path.isdir(ctx.shared_dir):
        raise PreflightError(
            f"shared dir not found: {ctx.shared_dir}",
            "create the directory or drop --shared-dir",
        )