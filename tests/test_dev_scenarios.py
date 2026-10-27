from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest

from sandbed.dev import scenarios as sc
from sandbed.dev.context import LabContext
from sandbed.dev.errors import InputError, ScenarioError
from sandbed.dev.labs import LabEntry, lab_prefix
from sandbed.dev.paths import lab_paths
from sandbed.dev.record import DEFAULT_CONFIG, LabRecord, LabStore, config_hash
from sandbed.dev.runner import CommandFailed, CommandResult
from sandbed.dev.state import AttemptLog

KEY = "/home/ubuntu/snap/openstack/common/sunbeam"
LAUNCH_OUT = f"Launching an OpenStack instance ...\nssh -i {KEY} ubuntu@10.20.20.5\n"


def make_ctx(tmp_path) -> LabContext:
    lab = str(uuid.uuid4())
    paths = lab_paths(lab, tmp_path)
    record = LabRecord(lab, "a", 1000, lab_prefix(lab), "10.213.0.0/24", dict(DEFAULT_CONFIG),
                       config_hash(DEFAULT_CONFIG), vm_address="10.213.0.7",
                       provisioning_state="ready", installed_artifact_id="a-1")
    store = LabStore(paths)
    store.save(record)
    return LabContext(LabEntry(lab, "a", 1000), paths, store, record, AttemptLog(paths.attempts))


class FakeGuest:
    def __init__(self, *, status="ACTIVE", fail_on=None, gone=True):
        self.cmds: list[list[str]] = []
        self.status, self.fail_on, self.gone = status, fail_on, gone

    def __call__(self, target, attempt, argv, *, home, timeout, log_path, check=True, **_kw):
        self.cmds.append(argv)
        text = " ".join(argv)
        tail, code = "", 0
        if self.fail_on and self.fail_on in text:
            code = 1
        elif argv[:3] == ["sunbeam", "cluster", "list"]:
            tail = "cloud-1  ONLINE"
        elif "project list" in text:
            tail = 'WARNING x\n[{"ID": "p1"}]'
        elif argv[:2] == ["sunbeam", "launch"]:
            tail = LAUNCH_OUT
        elif "server show sbd-" in text:
            tail = json.dumps({"id": "sid", "status": self.status})
        elif "image show" in text:
            tail = json.dumps({"id": "iid", "name": "ubuntu", "checksum": "md5x"})
        elif "server show sid" in text:
            code = 1 if self.gone else 0
        result = CommandResult(tuple(argv), code, False, 0.0, Path(log_path), tail)
        if check and code:
            raise CommandFailed(result)
        return result


def run(ctx, guest, monkeypatch):
    monkeypatch.setattr(sc, "run_guest", guest)
    monkeypatch.setattr(sc, "verify_identity", lambda _r: None)
    outcome = sc.Outcome()
    try:
        sc.run_scenario(ctx, "abcdef0123456789", outcome)
    finally:
        pass
    return outcome


def test_happy_path_order_and_assertions(tmp_path, monkeypatch) -> None:
    ctx, guest = make_ctx(tmp_path), FakeGuest()
    outcome = run(ctx, guest, monkeypatch)
    assert outcome.assertions == {
        "kvm": True, "bootstrap": True, "authenticated": True, "server_active": True,
        "guest_ssh": True, "cleanup": True,
    }
    heads = [c[0] if c[0] != "bash" else c[1].rsplit("/", 1)[-1] for c in guest.cmds]
    assert heads.index("sunbeam") < heads.index("os.sh") < heads.index("probe-guest.sh")
    assert any(" ".join(c).endswith("floating ip delete 10.20.20.5") for c in guest.cmds)
    stages = [a.stage for a in ctx.log.attempts()]
    assert stages == [f"scenario.{s}" for s in (
        "precondition", "bootstrap", "configure", "launch", "guest-reachability", "cleanup")]
    evidence_file = ctx.paths.run("abcdef0123456789") / "scenario-evidence.json"
    evidence = json.loads(evidence_file.read_text())
    assert evidence["server_id"] == "sid" and evidence["floating_ip"] == "10.20.20.5"
    assert evidence["image"]["checksum"] == "md5x"
    probe = next(c for c in guest.cmds if c[1].endswith("probe-guest.sh"))
    assert probe[2:] == [KEY, "10.20.20.5", "900"]


def test_instance_name_is_run_specific(tmp_path, monkeypatch) -> None:
    guest = FakeGuest()
    run(make_ctx(tmp_path), guest, monkeypatch)
    launch = next(c for c in guest.cmds if c[:2] == ["sunbeam", "launch"])
    assert launch[-1] == "sbd-abcdef01"


def test_guest_ssh_failure_preserves_the_instance_and_names_the_stage(
    tmp_path, monkeypatch
) -> None:
    ctx, guest = make_ctx(tmp_path), FakeGuest(fail_on="probe-guest.sh")
    outcome = sc.Outcome()
    monkeypatch.setattr(sc, "run_guest", guest)
    monkeypatch.setattr(sc, "verify_identity", lambda _r: None)
    with pytest.raises(ScenarioError) as exc:
        sc.run_scenario(ctx, "abcdef0123456789", outcome)
    assert exc.value.stage == "scenario.guest-reachability"
    assert outcome.assertions["server_active"] is True and outcome.assertions["guest_ssh"] is False
    assert not any("delete" in " ".join(c) for c in guest.cmds)  # evidence is preserved


def test_active_alone_is_not_success(tmp_path, monkeypatch) -> None:
    ctx, guest = make_ctx(tmp_path), FakeGuest(status="ERROR")
    with pytest.raises(ScenarioError, match="not ACTIVE"):
        run(ctx, guest, monkeypatch)
    assert not any("probe-guest.sh" in " ".join(c) for c in guest.cmds)


def test_a_second_scenario_in_the_same_lab_is_refused(tmp_path, monkeypatch) -> None:
    ctx = make_ctx(tmp_path)
    ctx.log.begin("old", "scenario.bootstrap", "")  # a bootstrap was attempted before
    guest = FakeGuest()
    with pytest.raises(InputError, match="new lab"):
        run(ctx, guest, monkeypatch)
    assert guest.cmds == []


def test_no_installed_artifact_is_refused(tmp_path, monkeypatch) -> None:
    ctx = make_ctx(tmp_path)
    ctx.record.installed_artifact_id = None
    with pytest.raises(InputError, match="deploy"):
        run(ctx, FakeGuest(), monkeypatch)


def test_leftover_server_after_cleanup_fails_the_scenario(tmp_path, monkeypatch) -> None:
    with pytest.raises(ScenarioError, match="still exists"):
        run(make_ctx(tmp_path), FakeGuest(gone=False), monkeypatch)


def test_parsers() -> None:
    assert sc.parse_launch(LAUNCH_OUT) == (KEY, "ubuntu", "10.20.20.5")
    with pytest.raises(ScenarioError):
        sc.parse_launch("no address here")
    assert sc.parse_json('warn: x\n{"a": 1}\ntrailing') == {"a": 1}
    with pytest.raises(ScenarioError):
        sc.parse_json("nothing")