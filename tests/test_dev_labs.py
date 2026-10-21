from __future__ import annotations

import uuid

import pytest

from sandbed.dev.errors import HostError, InputError
from sandbed.dev.labs import (
    LabEntry,
    Registry,
    lab_prefix,
    pick_cidr,
    validate_label,
)
from sandbed.dev.paths import lab_paths, state_root


@pytest.mark.parametrize("label", ["bug-123", "a", "x" * 31, "0abc"])
def test_valid_labels(label) -> None:
    assert validate_label(label) == label


@pytest.mark.parametrize("label", ["", "Bug", "-a", "a-", "a_b", "a b", "x" * 32, "../x", "a/b"])
def test_invalid_labels(label) -> None:
    with pytest.raises(InputError):
        validate_label(label)


def test_prefix_keeps_network_names_within_the_interface_limit() -> None:
    prefix = lab_prefix(str(uuid.uuid4()))
    assert prefix.startswith("sbd-") and len(prefix) == 12
    assert len(f"{prefix}-m") <= 15


def test_pick_cidr_skips_taken_and_overlapping_ranges() -> None:
    assert pick_cidr([]) == "10.213.0.0/24"
    assert pick_cidr(["10.213.0.0/24"]) == "10.213.1.0/24"
    assert pick_cidr(["10.213.0.0/24", "10.213.1.0/24", "10.213.2.0/23"]) == "10.213.4.0/24"


def test_pick_cidr_exhaustion() -> None:
    with pytest.raises(HostError):
        pick_cidr(["10.213.0.0/16"])


def test_state_root_honours_xdg(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    assert state_root() == tmp_path / "sandbed" / "dev"
    assert lab_paths("u").ssh_key == tmp_path / "sandbed/dev/labs/u/ssh/id_ed25519"


def entry(label: str) -> LabEntry:
    return LabEntry(str(uuid.uuid4()), label, owner_uid=1000, cidrs=["10.213.0.0/24"])


def test_registry_roundtrip_find_and_duplicate_label(tmp_path) -> None:
    reg = Registry(tmp_path)
    first = entry("a")
    with reg.locked():
        reg.add(first)
        with pytest.raises(InputError, match="in use"):
            reg.add(entry("a"))
    assert reg.find("a", 1000).lab_uuid == first.lab_uuid
    assert reg.find(first.lab_uuid, 1000).label == "a"
    with pytest.raises(InputError):
        reg.find("a", 1001)  # another user's lab is invisible


def test_destroyed_lab_releases_its_label_and_cidrs(tmp_path) -> None:
    reg = Registry(tmp_path)
    first = entry("a")
    reg.add(first)
    assert reg.reserved_cidrs() == ["10.213.0.0/24"]
    reg.set_state(first.lab_uuid, "cleanup_failed")
    assert reg.reserved_cidrs() == ["10.213.0.0/24"]  # still reserved until confirmed gone
    reg.set_state(first.lab_uuid, "destroyed")
    assert reg.reserved_cidrs() == []
    reg.add(entry("a"))