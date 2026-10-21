"""SSH/SCP to a lab VM. The target comes from Terraform + LXD discovery, never from user input,
and remote commands are built from argument lists, never from interpolated strings."""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass
from pathlib import Path

from .errors import InputError

# Absolute, no spaces, quotes, colons, globs or shell metacharacters; `..` is rejected below.
_REMOTE_PATH = re.compile(r"/[A-Za-z0-9._/+-]+")


def safe_remote_path(path: str) -> str:
    if not _REMOTE_PATH.fullmatch(path) or ".." in path.split("/"):
        raise InputError(f"unsafe remote path: {path!r}")
    return path


@dataclass(frozen=True)
class SshTarget:
    host: str
    user: str
    key: Path
    known_hosts: Path
    # The first connection after provisioning records the VM's host key; every later one must
    # match it.
    first_contact: bool = False

    def _opts(self) -> list[str]:
        policy = "accept-new" if self.first_contact else "yes"
        return [
            "-i", str(self.key),
            "-o", "IdentitiesOnly=yes",
            "-o", "BatchMode=yes",
            "-o", "ConnectTimeout=10",
            "-o", "ServerAliveInterval=15",
            "-o", f"UserKnownHostsFile={self.known_hosts}",
            "-o", f"StrictHostKeyChecking={policy}",
        ]

    def ssh_argv(self, remote: list[str]) -> list[str]:
        if not remote:
            raise InputError("empty remote command")
        return ["ssh", *self._opts(), f"{self.user}@{self.host}", "--", shlex.join(remote)]

    def scp_argv(self, local: Path, remote_path: str) -> list[str]:
        remote = safe_remote_path(remote_path)
        return ["scp", "-q", *self._opts(), str(local), f"{self.user}@{self.host}:{remote}"]

    def scp_from_argv(self, remote_path: str, local: Path) -> list[str]:
        remote = safe_remote_path(remote_path)
        return ["scp", "-q", *self._opts(), f"{self.user}@{self.host}:{remote}", str(local)]
