"""What this harness knows about one snap-openstack revision family.

Unknown revisions are rejected rather than guessed at: the connection list and CLI flags below
come from snap-openstack's docs/CONTRIBUTING.md and snapcraft.yaml, and an adapter must be
re-qualified (exact SHA recorded) before it is widened.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .errors import InputError


@dataclass(frozen=True)
class SnapMeta:
    name: str
    version: str
    base: str
    architectures: tuple[str, ...]


@dataclass(frozen=True)
class RevisionAdapter:
    name: str
    version_re: re.Pattern
    base: str
    connect: tuple[tuple[str, ...], ...]  # argument lists for `snap connect`
    required_plugs: tuple[str, ...]  # `openstack:<plug>` names that must end up connected
    # Exact snap-openstack commits this adapter was qualified against on a real host (gate Q14).
    # Empty until qualification; results then say `adapter_qualified: false` instead of failing.
    qualified_heads: frozenset[str] = frozenset()


ADAPTERS = (
    RevisionAdapter(
        name="2026.1",
        version_re=re.compile(r"2026\.1-[0-9a-f]{8}(\+dirty)?"),
        base="core24",
        connect=(
            ("openstack:juju-bin", "juju:juju-bin"),
            ("openstack:dot-local-share-juju",),
            ("openstack:dot-config-openstack",),
            ("openstack:dot-local-share-openstack",),
        ),
        required_plugs=(
            "juju-bin", "dot-local-share-juju", "dot-config-openstack", "dot-local-share-openstack",
        ),
    ),
)


def select_adapter(meta: SnapMeta) -> RevisionAdapter:
    if meta.name != "openstack":
        raise InputError(f"not the openstack snap: {meta.name!r}")
    if "amd64" not in meta.architectures:
        raise InputError(f"unsupported architectures: {list(meta.architectures)}")
    for adapter in ADAPTERS:
        if adapter.version_re.fullmatch(meta.version) and adapter.base == meta.base:
            return adapter
    raise InputError(
        f"no supported adapter for snap version {meta.version!r} (base {meta.base!r}); "
        f"supported: {[a.name for a in ADAPTERS]}"
    )


def parse_connections(text: str) -> dict[str, str]:
    """`snap connections openstack` -> {plug name: slot}; '-' means not connected."""
    result: dict[str, str] = {}
    for line in text.splitlines()[1:]:
        cols = line.split()
        if len(cols) >= 3 and cols[1].startswith("openstack:"):
            result[cols[1].split(":", 1)[1]] = cols[2]
    return result


def missing_connections(adapter: RevisionAdapter, text: str) -> list[str]:
    seen = parse_connections(text)
    return [p for p in adapter.required_plugs if seen.get(p, "-") == "-"]