from __future__ import annotations

import tarfile
import uuid
from pathlib import Path

from sandbed.dev import diagnostics as diag
from sandbed.dev.context import LabContext
from sandbed.dev.labs import LabEntry, lab_prefix
from sandbed.dev.paths import lab_paths
from sandbed.dev.record import DEFAULT_CONFIG, LabRecord, LabStore, config_hash
from sandbed.dev.runner import CommandResult
from sandbed.dev.state import AttemptLog


def make_ctx(tmp_path, address="10.213.0.7") -> LabContext:
    lab = str(uuid.uuid4())
    paths = lab_paths(lab, tmp_path)
    record = LabRecord(lab, "a", 1000, lab_prefix(lab), "10.213.0.0/24", dict(DEFAULT_CONFIG),
                       config_hash(DEFAULT_CONFIG), vm_address=address,
                       provisioning_state="ready")
    store = LabStore(paths)
    store.save(record)
    return LabContext(LabEntry(lab, "a", 1000), paths, store, record, AttemptLog(paths.attempts))


def fake_logged(answers):
    def run_logged(argv, *, log_path, **_kw):
        remote = argv[-1] if argv[0] == "ssh" else " ".join(argv)
        for needle, (code, tail, timed_out) in answers.items():
            if needle in remote:
                return CommandResult(tuple(argv), code, timed_out, 0.0, log_path, tail)
        return CommandResult(tuple(argv), 0, False, 0.0, log_path, "ok")

    return run_logged


def test_collectors_are_named_bounded_and_never_interpolate_the_run_id(tmp_path) -> None:
    ctx = make_ctx(tmp_path)
    items = diag.collectors(ctx, "deadbeef", server_id="sid")
    names = [c.name for c in items]
    assert len(names) == len(set(names)) and "instance-console" in names
    stage_logs = next(c for c in items if c.name == "stage-logs")
    assert "deadbeef" not in stage_logs.argv[2] and stage_logs.argv[-1] == "deadbeef"
    assert all(c.timeout <= 120 and c.max_bytes <= 1_000_000 for c in items)
    assert "instance-console" not in [c.name for c in diag.collectors(ctx, "r", None)]


def test_executor_maps_exit_codes_to_statuses(tmp_path, monkeypatch) -> None:
    ctx = make_ctx(tmp_path)
    monkeypatch.setattr(diag, "run_logged", fake_logged({
        "juju": (127, "juju: command not found", False),
        "snap-services": (1, "boom", False),
        "journalctl": (0, "", True),
    }))
    execute = diag.make_executor(ctx, tmp_path)
    by = {c.name: c for c in diag.collectors(ctx, "r", None)}
    assert execute(by["juju-status"])[0] == diag.UNAVAILABLE
    assert execute(by["clusterd-journal"])[0] == diag.TIMEOUT
    assert execute(by["snap-list"])[0] == diag.OK
    assert execute(by["lxd-version"])[0] == diag.OK


def test_guest_collectors_are_unavailable_without_an_address(tmp_path) -> None:
    ctx = make_ctx(tmp_path, address=None)
    execute = diag.make_executor(ctx, tmp_path)
    guest = next(c for c in diag.collectors(ctx, "r", None) if c.where == "guest")
    assert execute(guest)[0] == diag.UNAVAILABLE


def test_each_collection_is_a_new_attempt_and_the_archive_is_exportable(
    tmp_path, monkeypatch
) -> None:
    ctx = make_ctx(tmp_path, address=None)  # host collectors only; no inspection
    monkeypatch.setattr(diag, "run_logged", fake_logged({}))
    first = diag.collect_run(ctx, "run1")
    second = diag.collect_run(ctx, "run1")
    assert (first["attempt"], second["attempt"]) == (1, 2)
    assert first["status"] == "partial"  # guest collectors were unavailable
    assert first["diagnostics_path"].endswith("diagnostics.tar.gz")
    assert second["diagnostics_path"].endswith("diagnostics-2.tar.gz")
    with tarfile.open(first["diagnostics_path"]) as tar:
        assert "index.json" in tar.getnames()
    assert Path(first["diagnostics_path"]).exists()