from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from sandbed.cli import cli
from sandbed.dev import commands, runjob, worker
from sandbed.dev.results import Outcome, envelope

LAUNCH = "sandbed.dev.cli.runjob.launch"

# command -> (argv after `dev`, name of the commands.* function the worker must call)
INVOCATIONS = {
    "host prepare": (["host", "prepare", "--storage-backend", "dir", "--storage-source", "/s"],
                     "host_prepare"),
    "create": (["create", "--id", "a", "--vcpus", "2"], "create"),
    "build": (["build", "--id", "a", "--source", ".", "--include", "x.py"], "build"),
    "deploy": (["deploy", "--id", "a", "--artifact", "art-1"], "deploy"),
    "test": (["test", "--id", "a"], "run_test"),
    "collect": (["collect", "--id", "a", "--run", "r1"], "collect"),
    "destroy": (["destroy", "--id", "a"], "destroy"),
}


def invoke(*args: str):
    return CliRunner().invoke(cli, ["dev", *args])


def launched() -> runjob.Launched:
    return runjob.Launched({
        "status": "running", "job_id": "j1", "unit": "u",
        "human": "job j1 started; follow with: sandbed dev job wait --job j1 --json",
    })


def test_detach_prints_one_running_envelope_and_exits_zero() -> None:
    with patch(LAUNCH, return_value=launched()) as fake:
        result = invoke("create", "--id", "a", "--detach", "--json")
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert (payload["status"], payload["job_id"], payload["command"]) == ("running", "j1", "create")
    assert fake.call_args.kwargs == {"detach": True}


def test_blocking_create_prints_the_outcome_and_exits_with_its_code() -> None:
    out = Outcome(envelope("create", "failed", failure_category="deployment"), "", 4)
    with patch(LAUNCH, return_value=out):
        result = invoke("create", "--id", "a", "--json")
    assert result.exit_code == 4
    assert json.loads(result.output)["failure_category"] == "deployment"


def test_build_passes_an_absolute_source(tmp_path, monkeypatch) -> None:
    (tmp_path / "src").mkdir()
    monkeypatch.chdir(tmp_path)
    with patch(LAUNCH, return_value=launched()) as fake:
        result = invoke("build", "--id", "a", "--source", "src", "--detach", "--json")
    assert result.exit_code == 0
    assert fake.call_args.args[1]["source"] == str((tmp_path / "src").resolve())


@pytest.mark.parametrize("command", sorted(INVOCATIONS))
def test_cli_params_are_what_the_worker_reads(command: str) -> None:
    argv, func = INVOCATIONS[command]
    with patch(LAUNCH, return_value=launched()) as fake:
        assert invoke(*argv, "--detach", "--json").exit_code == 0
    name, params, lab_ref = fake.call_args.args
    assert name == command
    assert lab_ref == ("@host" if command == "host prepare" else "a")
    with patch.object(commands, func, MagicMock(return_value={})) as target:
        worker.JOB_COMMANDS[command](params)
    target.assert_called_once()


def test_capabilities_lists_detach_commands() -> None:
    with patch("sandbed.dev.capabilities.run", side_effect=FileNotFoundError):
        payload = json.loads(invoke("capabilities", "--json").output)
    assert payload["interface_version"] == 3
    assert payload["detach_commands"] == [
        "build", "collect", "create", "deploy", "destroy", "host prepare", "test"]


@pytest.mark.parametrize("argv", [["status", "--id", "a"], ["host", "check"], ["capabilities"]])
def test_read_only_commands_have_no_detach(argv: list[str]) -> None:
    assert "--detach" not in invoke(*argv, "--help").output


@pytest.mark.parametrize("argv", [
    ["create", "--id", "Bad_Label"],
    ["deploy", "--id", "Bad_Label", "--artifact", "x"],
    ["test", "--id", "a", "--scenario", "nope"],
])
def test_bad_input_exits_2_before_any_job_is_launched(argv: list[str]) -> None:
    with patch(LAUNCH) as fake:
        result = invoke(*argv, "--json")
    assert result.exit_code == 2
    assert json.loads(result.output)["failure_category"] == "input"
    fake.assert_not_called()


def test_human_detach_line_goes_to_stdout() -> None:
    with patch(LAUNCH, return_value=launched()):
        result = CliRunner().invoke(
            cli, ["dev", "create", "--id", "a", "--detach"])
    assert result.exit_code == 0
    assert "job j1 started" in result.stdout and result.stderr == ""


UUID_LAB = "12345678-1234-1234-1234-123456789012"
LAB_COMMANDS = {
    "build": ["--source", ".", "--include", "x.py"],
    "deploy": ["--artifact", "art-1"],
    "test": [],
    "collect": ["--run", "r1"],
    "destroy": [],
}


@pytest.mark.parametrize("command", LAB_COMMANDS)
def test_lab_commands_accept_a_uuid_lab_id(command: str) -> None:
    with patch(LAUNCH, return_value=launched()) as fake:
        result = invoke(command, "--id", UUID_LAB, *LAB_COMMANDS[command], "--detach", "--json")
    assert result.exit_code == 0
    _name, params, lab_ref = fake.call_args.args
    assert (lab_ref, params["lab"]) == (UUID_LAB, UUID_LAB)


@pytest.mark.parametrize("command", LAB_COMMANDS)
def test_lab_commands_reject_a_malformed_lab_id(command: str) -> None:
    with patch(LAUNCH, return_value=launched()) as fake:
        result = invoke(command, "--id", "Bad_Label", *LAB_COMMANDS[command], "--detach", "--json")
    assert result.exit_code == 2
    assert json.loads(result.output)["failure_category"] == "input"
    fake.assert_not_called()


def test_create_rejects_a_uuid_as_a_new_label() -> None:
    with patch(LAUNCH, return_value=launched()) as fake:
        result = invoke("create", "--id", UUID_LAB, "--detach", "--json")
    assert result.exit_code == 2
    fake.assert_not_called()
