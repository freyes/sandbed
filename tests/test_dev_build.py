from __future__ import annotations

import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from sandbed.dev import build as build_mod
from sandbed.dev.context import LabContext
from sandbed.dev.errors import BuildError, DeploymentError
from sandbed.dev.labs import LabEntry, lab_prefix
from sandbed.dev.paths import lab_paths
from sandbed.dev.record import DEFAULT_CONFIG, LabRecord, LabStore, config_hash
from sandbed.dev.runner import CommandResult
from sandbed.dev.snapshot import SnapshotInfo
from sandbed.dev.state import AttemptLog

SNAP_YAML = (
    "name: openstack\nversion: 2026.1-0123abcd+dirty\nbase: core24\narchitectures: [amd64]\n"
)


def make_ctx(tmp_path) -> LabContext:
    lab = str(uuid.uuid4())
    paths = lab_paths(lab, tmp_path)
    record = LabRecord(lab, "a", 1000, lab_prefix(lab), "10.213.0.0/24", dict(DEFAULT_CONFIG),
                       config_hash(DEFAULT_CONFIG))
    store = LabStore(paths)
    store.save(record)
    return LabContext(LabEntry(lab, "a", 1000), paths, store, record, AttemptLog(paths.attempts))


def fake_capture(source, dest, *, include, manifest_path):
    dest.mkdir(parents=True)
    manifest_path.write_text("[]")
    return SnapshotInfo(str(source), "h" * 40, None, True, "d" * 64, "c" * 64, [], [])


def fake_pack_factory(names):
    def fake_run_logged(argv, *, cwd, log_path, **_kw):
        for name in names:
            (Path(cwd) / name).write_bytes(b"snap-bytes")
        return CommandResult(tuple(argv), 0, False, 0.0, log_path, "")

    return fake_run_logged


def run_build(ctx, tmp_path, names=("openstack_2026.1_amd64.snap",)):
    shell = MagicMock(returncode=0, stdout=SNAP_YAML)
    with (
        patch.object(build_mod.snapshot, "capture", fake_capture),
        patch.object(build_mod, "run_logged", fake_pack_factory(names)),
        patch.object(build_mod, "run", return_value=shell),
        patch.object(build_mod, "build_identity", return_value={"version": "t"}),
    ):
        return build_mod.build(ctx, "run1", tmp_path / "src", [])


def test_build_registers_an_immutable_artifact_with_provenance(tmp_path) -> None:
    ctx = make_ctx(tmp_path)
    prov = run_build(ctx, tmp_path)
    snap, loaded = build_mod.load_artifact(ctx, prov["artifact_id"])
    assert snap.read_bytes() == b"snap-bytes"
    assert loaded["snap"]["sha256"] == build_mod.sha256_file(snap)
    assert loaded["snap"]["version"] == "2026.1-0123abcd+dirty"
    assert loaded["source"]["head"] == "h" * 40 and loaded["adapter"] == "2026.1"
    assert loaded["adapter_qualified"] is False  # no SHA has been qualified yet
    assert (snap.parent / "provenance.json").stat().st_mode & 0o777 == 0o400
    stages = [a.stage for a in ctx.log.attempts()]
    assert stages == ["build.snapshot", "build.pack", "build.inspect"]
    assert not (ctx.paths.run("run1") / "build" / "src").exists()


def test_a_qualified_head_is_reported_as_qualified(tmp_path, monkeypatch) -> None:
    import dataclasses

    from sandbed.dev import adapter as adapter_mod

    qualified = dataclasses.replace(adapter_mod.ADAPTERS[0], qualified_heads=frozenset({"h" * 40}))
    monkeypatch.setattr(build_mod, "select_adapter", lambda _meta: qualified)
    assert run_build(make_ctx(tmp_path), tmp_path)["adapter_qualified"] is True


def test_zero_or_two_snaps_is_a_build_error(tmp_path) -> None:
    for names in ((), ("openstack_1_amd64.snap", "openstack_2_amd64.snap")):
        ctx = make_ctx(tmp_path / str(len(names)))
        with pytest.raises(BuildError, match="exactly one"):
            run_build(ctx, tmp_path / str(len(names)), names)


def test_tampered_artifact_is_rejected_before_use(tmp_path) -> None:
    ctx = make_ctx(tmp_path)
    prov = run_build(ctx, tmp_path)
    (ctx.paths.artifact(prov["artifact_id"]) / "openstack.snap").write_bytes(b"evil")
    with pytest.raises(DeploymentError, match="no longer matches"):
        build_mod.load_artifact(ctx, prov["artifact_id"])


@pytest.mark.parametrize("bad", [
    "../x", "nope", "a-/../../etc", "a-1;touch-x", "a-20260101T000000-deadbeef extra",
    "a-20260101T000000-deadbeef\n", "a-1", "a-20260101T000000-DEADBEEF",
])
def test_artifact_ids_are_validated(tmp_path, bad) -> None:
    with pytest.raises(BuildError):
        build_mod.load_artifact(make_ctx(tmp_path), bad)


def test_unknown_artifact(tmp_path) -> None:
    with pytest.raises(BuildError, match="unknown artifact"):
        build_mod.load_artifact(make_ctx(tmp_path), "a-20260101T000000-deadbeef")