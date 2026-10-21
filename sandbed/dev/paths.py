"""Where development-lab state lives: $XDG_STATE_HOME/sandbed/dev (default ~/.local/state)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def state_root() -> Path:
    base = os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state")
    return Path(base) / "sandbed" / "dev"


@dataclass(frozen=True)
class LabPaths:
    root: Path  # state_root()/labs/<lab-uuid>

    @property
    def record(self) -> Path:
        return self.root / "lab.json"

    @property
    def lock(self) -> Path:
        return self.root / "lock"

    @property
    def attempts(self) -> Path:
        return self.root / "attempts.json"

    @property
    def ssh_dir(self) -> Path:
        return self.root / "ssh"

    @property
    def ssh_key(self) -> Path:
        return self.ssh_dir / "id_ed25519"

    @property
    def known_hosts(self) -> Path:
        return self.ssh_dir / "known_hosts"

    @property
    def private(self) -> Path:
        return self.root / "private"

    def terraform(self, root: str) -> Path:
        return self.root / "terraform" / root

    def artifact(self, artifact_id: str) -> Path:
        return self.root / "artifacts" / artifact_id

    def run(self, run_id: str) -> Path:
        return self.root / "runs" / run_id


def lab_paths(lab_uuid: str, root: Path | None = None) -> LabPaths:
    return LabPaths((root or state_root()) / "labs" / lab_uuid)