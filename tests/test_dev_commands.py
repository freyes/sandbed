from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from sandbed.cli import cli
from sandbed.dev import commands
from sandbed.dev.errors import DeploymentError, HostError, InputError, ScenarioError
from sandbed.dev.record import LabStore
from sandbed.dev.store import LockBusy, exclusive_lock


@pytest.fixture
def state(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    return tmp_path


def invoke(*args: str):
    return CliRunner().invoke(cli, ["dev", *args])


def test_the_command_surface_is_the_published_one() -> None:
    with patch("sandbed.dev.capabilities.run") as fake:
        fake.return_value.returncode, fake.return_value.stdout = 1, ""
        payload = json.loads(invoke("capabilities", "--json").output)
    assert payload["commands"] == [
        "build", "capabilities", "collect", "create", "deploy", "destroy", "host check",
        "host prepare", "status", "test",
    ]


def test_create_options_reach_the_command() -> None:
    with patch.object(commands, "create", return_value={"human": "ok"}) as fake:
        result = invoke("create", "--id", "bug-1", "--vcpus", "4", "--sdb-size", "10GiB", "--json")
    assert result.exit_code == 0
    fake.assert_called_once_with(
        "bug-1", provider="manual", profile="single-node",
        overrides={"vcpus": 4, "memory": None, "disk": None, "sdb_size": "10GiB"},
    )


def test_errors_become_envelopes_with_context_and_exit_codes() -> None:
    err = DeploymentError("snap changed", stage="deploy.transfer", lab_id="L", run_id="R")
    with patch.object(commands, "deploy", side_effect=err):
        result = invoke("deploy", "--id", "a", "--artifact", "a-1", "--json")
    payload = json.loads(result.output)
    assert result.exit_code == 4
    assert (payload["status"], payload["failure_category"], payload["failed_stage"]) == (
        "failed", "deployment", "deploy.transfer")
    assert (payload["lab_id"], payload["run_id"]) == ("L", "R")


def test_a_failed_test_result_exits_with_its_layer_code() -> None:
    fake_result = {"status": "failed", "exit_code": 5, "failure_category": "scenario",
                   "failed_stage": "scenario.guest-reachability", "run_id": "R"}
    with patch.object(commands, "run_test", return_value=fake_result):
        result = invoke("test", "--id", "a", "--json")
    payload = json.loads(result.output)
    assert result.exit_code == 5 and payload["failed_stage"].endswith("guest-reachability")
    assert "exit_code" not in payload


def test_ctrl_c_exits_130_without_claiming_anything_stopped() -> None:
    with patch.object(commands, "status", side_effect=KeyboardInterrupt):
        result = invoke("status", "--id", "a", "--json")
    assert result.exit_code == 130 and json.loads(result.output)["status"] == "interrupted"


def test_only_manual_single_node_is_accepted(state) -> None:
    result = invoke("create", "--id", "a", "--provider", "maas", "--json")
    assert result.exit_code == 2 and json.loads(result.output)["failure_category"] == "input"


def test_json_mode_prints_one_object_even_for_failures(state) -> None:
    result = invoke("status", "--id", "missing", "--json")
    assert result.exit_code == 2
    assert json.loads(result.output)["message"] == "no such lab: missing"


def make_lab(label="a", **overrides):
    with (
        patch.object(commands, "require_host"),
        patch.object(commands, "taken_cidrs", return_value=["192.168.0.0/24"]),
        patch.object(commands, "provision") as prov,
    ):
        def fake_provision(ctx, run_id):
            ctx.record.provisioning_state, ctx.record.vm_address = "ready", "10.213.0.7"
            ctx.store.save(ctx.record)
            return ctx.record

        prov.side_effect = fake_provision
        return commands.create(label, provider="manual", profile="single-node",
                               overrides=overrides)


def test_create_allocates_a_free_cidr_and_creates_private_state(state) -> None:
    out = make_lab()
    assert out["vm_address"] == "10.213.0.7"
    entry = commands.Registry().find("a", __import__("os").getuid())
    assert entry.cidrs == ["10.213.0.0/24"]
    assert LabStore(commands.lab_paths(entry.lab_uuid)).load().provisioning_state == "ready"
    second = make_lab("b")
    assert commands.Registry().find("b", __import__("os").getuid()).cidrs == ["10.213.1.0/24"]
    assert second["lab_id"] != out["lab_id"]


def test_a_crash_while_registering_leaves_nothing_reserved(state) -> None:
    with (
        patch.object(commands, "require_host"),
        patch.object(commands, "taken_cidrs", return_value=[]),
        patch.object(commands.Registry, "add", side_effect=KeyboardInterrupt),
    ):
        with pytest.raises(KeyboardInterrupt):
            commands.create("a", provider="manual", profile="single-node", overrides={})
    assert commands.Registry().reserved_cidrs() == []
    assert not list((state / "sandbed/dev/labs").glob("*"))
    assert make_lab("a")["vm_address"] == "10.213.0.7"  # label and CIDR are reusable


def test_a_record_written_before_a_crash_never_blocks_the_label(state) -> None:
    """Simulate dying after the record was saved but before the registry was updated."""
    import uuid

    from sandbed.dev.labs import lab_prefix
    from sandbed.dev.record import DEFAULT_CONFIG, LabRecord, config_hash

    orphan = str(uuid.uuid4())
    LabStore(commands.lab_paths(orphan)).save(LabRecord(
        orphan, "a", 1, lab_prefix(orphan), "10.213.0.0/24", dict(DEFAULT_CONFIG),
        config_hash(DEFAULT_CONFIG)))
    out = make_lab("a")
    assert out["lab_id"] != orphan
    assert commands.Registry().find("a", __import__("os").getuid()).cidrs == ["10.213.0.0/24"]


def test_create_again_with_the_same_config_resumes_but_a_changed_one_is_refused(state) -> None:
    first = make_lab(vcpus=4)
    again = make_lab(vcpus=4)
    assert again["lab_id"] == first["lab_id"]
    with pytest.raises(InputError, match="immutable"):
        make_lab(vcpus=6)


def test_run_test_collects_after_failure_and_reports_the_layer(state) -> None:
    make_lab()

    def failing(ctx, run_id, outcome):
        outcome.assertions["server_active"] = True
        raise ScenarioError("ssh failed", stage="scenario.guest-reachability")

    collection = {"status": "partial", "diagnostics_path": "/d.tar.gz"}
    with (
        patch.object(commands, "run_scenario", side_effect=failing),
        patch.object(commands, "collect_run", return_value=collection) as collect,
    ):
        result = commands.run_test("a", "bootstrap-and-launch")
    collect.assert_called_once()
    assert result["status"] == "failed" and result["exit_code"] == 5
    assert result["failed_stage"] == "scenario.guest-reachability"
    assert result["assertions"] == {"server_active": True} and result["preserved"] is True
    assert (state / "sandbed/dev/labs").exists()
    run_dir = next((state / "sandbed/dev/labs").glob("*/runs/*"))
    assert json.loads((run_dir / "result.json").read_text())["failure_category"] == "scenario"
    assert (run_dir / "report.md").exists()


def test_run_test_success_and_unknown_scenario(state) -> None:
    make_lab()
    collection = {"status": "complete", "diagnostics_path": "/d.tar.gz"}
    with (
        patch.object(commands, "run_scenario"),
        patch.object(commands, "collect_run", return_value=collection),
    ):
        result = commands.run_test("a", "bootstrap-and-launch")
    assert result["status"] == "succeeded" and "exit_code" not in result
    with pytest.raises(InputError, match="unknown scenario"):
        commands.run_test("a", "nope")


def test_collect_of_an_unknown_run_is_an_input_error(state) -> None:
    make_lab()
    with pytest.raises(InputError, match="unknown run"):
        commands.collect("a", "deadbeef")


def test_incomplete_collection_is_nonzero(state) -> None:
    make_lab()
    run_id = "r1"
    commands_lab = commands.Registry().find("a", __import__("os").getuid())
    commands.lab_paths(commands_lab.lab_uuid).run(run_id).mkdir(parents=True)
    partial = {"status": "partial", "diagnostics_path": "/d", "index": {"entries": []}}
    with patch.object(commands, "collect_run", return_value=partial):
        out = commands.collect("a", run_id)
    assert out["exit_code"] == 6 and out["status"] == "failed"


def test_taken_cidrs_runs_outside_the_registry_lock(state) -> None:
    """`taken_cidrs()` must not be called while the host-wide registry lock is held, so a slow
    host query cannot block every other sandbed process."""
    reg_root = commands.Registry().root
    lock_path = reg_root / "registry.lock"
    observed: list[bool] = []

    def spy_taken_cidrs():
        try:
            with exclusive_lock(lock_path, timeout=0.05):
                pass
            observed.append(False)  # lock was NOT held by create()
        except LockBusy:
            observed.append(True)  # lock WAS held -- the bug we are guarding against
        return []

    with (
        patch.object(commands, "require_host"),
        patch.object(commands, "taken_cidrs", side_effect=spy_taken_cidrs),
        patch.object(commands, "provision") as prov,
    ):
        def fake_provision(ctx, run_id):
            ctx.record.provisioning_state, ctx.record.vm_address = "ready", "10.213.0.7"
            ctx.store.save(ctx.record)
            return ctx.record

        prov.side_effect = fake_provision
        commands.create("a", provider="manual", profile="single-node", overrides={})
    assert observed == [False], "taken_cidrs() was called while the registry lock was held"


def test_a_hosterror_from_taken_cidrs_aborts_create_without_side_effects(state) -> None:
    """If the host cannot be queried for taken CIDRs, `create` must fail closed: no lab
    directory, no registry entry, no reserved label."""
    reg = commands.Registry()
    labs_dir = reg.root / "labs"
    initial_entries = reg.entries()

    with (
        patch.object(commands, "require_host"),
        patch.object(commands, "taken_cidrs", side_effect=HostError("ip -j route: boom")),
    ):
        with pytest.raises(HostError, match="ip -j route"):
            commands.create("a", provider="manual", profile="single-node", overrides={})
    assert reg.entries() == initial_entries
    assert reg.reserved_cidrs() == []
    if labs_dir.exists():
        assert not list(labs_dir.glob("*"))

