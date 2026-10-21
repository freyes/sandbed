"""Lab identity, the host-wide registry, and network allocation."""

from __future__ import annotations

import ipaddress
import re
import uuid
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .errors import HostError, InputError
from .paths import state_root
from .store import atomic_write_json, exclusive_lock, read_json

LABEL_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,30}")
# Private /16 distinct from the legacy defaults (192.168.200.0/24, 172.16.x.0/24).
CIDR_POOL = ipaddress.ip_network("10.213.0.0/16")
STORAGE_POOL = "sandbed-dev"  # LXD pool and RBD pool prepared by `sandbed dev host prepare`


def validate_label(label: str) -> str:
    if not LABEL_RE.fullmatch(label) or label.endswith("-"):
        raise InputError(
            f"invalid lab id {label!r}: use 1-31 chars of a-z, 0-9 and '-', not starting or "
            "ending with '-'"
        )
    return label


def lab_prefix(lab_uuid: str) -> str:
    """`sbd-<8 hex>`: LXD project name and prefix of lab-owned resources. A network name is a
    Linux interface name (15 chars max), so `<prefix>-m` (14) must stay short."""
    return f"sbd-{uuid.UUID(lab_uuid).hex[:8]}"


def pick_cidr(taken: Iterable[str]) -> str:
    """First /24 of CIDR_POOL overlapping nothing in `taken` (registry, routes, LXD networks)."""
    used = [ipaddress.ip_network(t, strict=False) for t in taken]
    for net in CIDR_POOL.subnets(new_prefix=24):
        if not any(net.overlaps(u) for u in used):
            return str(net)
    raise HostError(f"no free /24 left in {CIDR_POOL}")


@dataclass
class LabEntry:
    lab_uuid: str
    label: str
    owner_uid: int
    cidrs: list[str] = field(default_factory=list)
    state: str = "active"  # active | cleanup_failed | destroyed


class Registry:
    """`registry.json`: every lab and its reserved CIDRs. Mutate only inside `locked()`."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = root or state_root()
        self.path = self.root / "registry.json"

    def locked(self, timeout: float = 30.0):
        return exclusive_lock(self.root / "registry.lock", timeout=timeout)

    def entries(self) -> list[LabEntry]:
        raw = read_json(self.path, default={"labs": []})
        return [LabEntry(**item) for item in raw["labs"]]  # type: ignore[index]

    def _save(self, entries: list[LabEntry]) -> None:
        atomic_write_json(self.path, {"labs": [asdict(e) for e in entries]})

    def reserved_cidrs(self) -> list[str]:
        return [c for e in self.entries() if e.state != "destroyed" for c in e.cidrs]

    def find(self, ref: str, owner_uid: int, *, include_destroyed: bool = False) -> LabEntry:
        """Resolve a label or UUID to one of this user's labs (live ones, unless asked)."""
        matches = [
            e for e in self.entries()
            if e.owner_uid == owner_uid and ref in (e.label, e.lab_uuid)
            and (include_destroyed or e.state != "destroyed")
        ]
        if not matches:
            raise InputError(f"no such lab: {ref}")
        return matches[-1]  # a label may be reused after destroy; the newest entry wins

    def add(self, entry: LabEntry) -> None:
        entries = self.entries()
        if any(e.state != "destroyed" and e.label == entry.label for e in entries):
            raise InputError(f"lab id already in use: {entry.label}")
        prefix = lab_prefix(entry.lab_uuid)
        if any(e.state != "destroyed" and lab_prefix(e.lab_uuid) == prefix for e in entries):
            raise InputError("resource-prefix collision; retry")
        self._save([*entries, entry])

    def set_state(self, lab_uuid: str, state: str) -> None:
        entries = self.entries()
        for e in entries:
            if e.lab_uuid == lab_uuid:
                e.state = state
        self._save(entries)