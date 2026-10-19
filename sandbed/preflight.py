from __future__ import annotations

import os

from .config import Config
from .shell import run


class PreflightError(RuntimeError):
    def __init__(self, msg: str, remediation: str) -> None:
        self.remediation = remediation
        super().__init__(msg + " — " + remediation)


def _kvm_present() -> bool:
    return os.path.exists("/dev/kvm")


def check(ctx: Config) -> None:
    if not _kvm_present():
        raise PreflightError("no /dev/kvm", "enable nested virtualization / KVM on the host")
    if run(["lxc", "query", "/1.0"], check=False).returncode != 0:
        raise PreflightError("LXD not reachable", "run `snap install lxd && lxd init --auto`")
    if run(["sudo", "microceph", "cluster", "list"], check=False).returncode != 0:
        raise PreflightError("microceph not reachable", "snap install microceph + bootstrap")
    if ctx.shared_dir and not os.path.isdir(ctx.shared_dir):
        raise PreflightError(
            f"shared dir not found: {ctx.shared_dir}",
            "create the directory or drop --shared-dir",
        )