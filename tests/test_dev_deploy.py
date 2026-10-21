from __future__ import annotations

import hashlib
import uuid
from pathlib import Path

import pytest

from sandbed.dev import deploy as dep
from sandbed.dev.adapter import ADAPTERS
from sandbed.dev.context import LabContext
from sandbed.dev.errors import DeploymentError, InputError
from sandbed.dev.labs import LabEntry, lab_prefix
from sandbed.dev.paths import lab_paths
from sandbed.dev.record import DEFAULT_CONFIG, LabRecord, LabStore, config_hash
from sandbed.dev.runner import CommandResult
from sandbed.dev.state import AttemptLog

SNAP_BYTES = b"snap-bytes"
SHA = hashlib.sha256(SNAP_BYTES).hexdigest()
VERSION = "2026.1-0123abcd+dirty"
CONNECTIONS = (
    "Interface Plug Slot Notes\n"
    + "".join(f"x openstack:{p} :slot -\n" for p in ADAPTERS[0].required_plugs)
)


def make_ctx(tmp_path, installed=None) -> LabContext:
    lab = str(uuid.uuid4())
    paths = lab_paths(lab, tmp_path)
    record = LabRecord(lab, "a", 1000, lab_prefix(lab), "10.213.0.0/24", dict(DEFAULT_CONFIG),
                       config_hash(DEFAULT_CONFIG), vm_address="10.213.0.7",
                       provisioning_state="ready", installed_artifact_id=installed)
    store = LabStore(paths)
    store.save(record)
    return LabContext(LabEntry(lab, "a", 1000), paths, store, record, AttemptLog(paths.attempts))


@pytest.fixture
def guest(monkeypatch, tmp_path):
    """Fake guest: records every command; answers the verification queries."""
    seen: list[list[str]] = []
    state = {"sha": SHA, "version": VERSION, "groups": "ubuntu snap_daemon lxd"}
    provenance = {"snap": {"name": "openstack", "version": VERSION, "base": "core24",
                           "architectures": ["amd64"], "sha256": SHA}}
    snap = tmp_path / "snap"
    snap.write_bytes(SNAP_BYTES)

    def run_guest(target, attempt, argv, *, home, timeout, log_path, check=True, tail_bytes=0):
        seen.append(argv)
        tail = ""
        if argv[:2] == ["snap", "list"]:
            tail = f"Name Version Rev\nopenstack {state['version']} x7\n"
        elif argv[0] == "sha256sum" or "sha256sum" in " ".join(argv):
            tail = f"{state['sha']}  /x\n"
        elif argv[:2] == ["snap", "connections"]:
            tail = CONNECTIONS
        elif argv == ["id", "-nG"]:
            tail = state["groups"]
        return CommandResult(tuple(argv), 0, False, 0.0, Path(log_path), tail)

    scp: list[list[str]] = []

    def run_logged(argv, **_kw):
        scp.append(argv)
        return CommandResult(tuple(argv), 0, False, 0.0, _kw["log_path"], "")

    monkeypatch.setattr(dep, "run_guest", run_guest)
    monkeypatch.setattr(dep, "run_logged", run_logged)
    monkeypatch.setattr(dep, "verify_identity", lambda _r: None)
    monkeypatch.setattr(dep, "load_artifact", lambda _c, _a: (snap, provenance))
    return seen, scp, state


def flat(seen):
    return [" ".join(a) for a in seen]


def test_deploy_installs_the_registered_snap_in_order(tmp_path, guest) -> None:
    seen, scp, _ = guest
    ctx = make_ctx(tmp_path)
    result = dep.deploy(ctx, "run1", "a-1")
    cmds = flat(seen)
    install = next(i for i, c in enumerate(cmds) if "snap install --dangerous" in c)
    assert install > next(i for i, c in enumerate(cmds) if c.startswith("sha256sum"))
    assert "sudo snap alias openstack.sunbeam sunbeam" in cmds
    assert any("prepare-node-script --bootstrap" in c and "pipefail" in c for c in cmds)
    assert sum(c.startswith("sudo snap connect") for c in cmds) == 4
    assert len(scp) == 2 and all(a[0] == "scp" for a in scp)  # snap and manifest
    assert result["installed_sha256"] == SHA and result["already_deployed"] is False
    assert LabStore(ctx.paths).load().installed_artifact_id == "a-1"
    assert (ctx.paths.run("run1") / "manifest.yaml").exists()


def test_checksum_mismatch_in_the_guest_stops_before_install(tmp_path, guest) -> None:
    seen, _, state = guest
    state["sha"] = "0" * 64
    with pytest.raises(DeploymentError, match="changed in transit"):
        dep.deploy(make_ctx(tmp_path), "run1", "a-1")
    assert not any("snap install" in c for c in flat(seen))


def test_wrong_installed_version_or_file_is_rejected(tmp_path, guest) -> None:
    _, _, state = guest
    state["version"] = "2026.1-ffffffff"
    with pytest.raises(DeploymentError, match="installed version"):
        dep.deploy(make_ctx(tmp_path), "run1", "a-1")


def test_installed_hash_is_taken_from_the_active_revision_file(tmp_path, guest) -> None:
    seen, _, _ = guest
    dep.deploy(make_ctx(tmp_path), "run1", "a-1")
    assert ["sudo", "sha256sum", "/var/lib/snapd/snaps/openstack_x7.snap"] in seen  # not _7


def test_missing_group_membership_is_a_deployment_error(tmp_path, guest) -> None:
    _, _, state = guest
    state["groups"] = "ubuntu"
    with pytest.raises(DeploymentError, match="snap_daemon"):
        dep.deploy(make_ctx(tmp_path), "run1", "a-1")


def test_same_artifact_again_only_verifies(tmp_path, guest) -> None:
    seen, scp, _ = guest
    result = dep.deploy(make_ctx(tmp_path, installed="a-1"), "run1", "a-1")
    assert result["already_deployed"] is True
    assert not any("snap install" in c or "snap connect " in c for c in flat(seen))
    assert not scp


def test_a_different_artifact_needs_a_new_lab(tmp_path, guest) -> None:
    with pytest.raises(InputError, match="new lab"):
        dep.deploy(make_ctx(tmp_path, installed="a-0"), "run1", "a-1")


def test_unready_lab_is_refused(tmp_path, guest) -> None:
    ctx = make_ctx(tmp_path)
    ctx.record.provisioning_state = "failed"
    with pytest.raises(InputError, match="failed"):
        dep.deploy(ctx, "run1", "a-1")


def test_parsers() -> None:
    listing = "Name Version Rev Tracking\nopenstack 2026.1-ab+dirty x3 - -\n"
    assert dep.snap_version(listing) == ("2026.1-ab+dirty", "x3")
    with pytest.raises(DeploymentError):
        dep.snap_version("Name Version\njuju 3.6 1\n")
    assert dep.first_hash(f"{SHA}  /var/x.snap\n") == SHA
    with pytest.raises(DeploymentError):
        dep.first_hash("nothing")
