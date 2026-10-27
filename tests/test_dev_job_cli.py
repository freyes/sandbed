from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from sandbed.cli import cli

JOB = "abc123abc123"
SUBS = ["status", "logs", "wait", "cancel", "list"]


def invoke(*args: str):
    return CliRunner().invoke(cli, ["dev", *args])


# sub -> (argv after `dev job <sub>`, jobcmds function, expected call args)
CASES = {
    "status": (["--job", JOB], "job_status", (JOB,)),
    "logs": (["--job", JOB], "job_logs", (JOB, "stderr", 0, None)),
    "wait": (["--job", JOB], "job_wait", (JOB, 300)),
    "cancel": (["--job", JOB], "job_cancel", (JOB,)),
    "list": ([], "job_list", (False, None, 20)),
}


@pytest.mark.parametrize("sub", SUBS)
def test_job_command_emits_one_envelope_and_passes_defaults(sub: str) -> None:
    argv, fn, expected = CASES[sub]
    with patch(f"sandbed.dev.cli.jobcmds.{fn}", return_value={"human": "ok", "x": 1}) as fake:
        result = invoke("job", sub, *argv, "--json")
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["command"] == f"job {sub}"
    assert payload["x"] == 1
    fake.assert_called_once_with(*expected)


def test_job_options_reach_the_functions() -> None:
    with patch("sandbed.dev.cli.jobcmds.job_logs", return_value={}) as logs:
        invoke("job", "logs", "--job", JOB, "--stream", "stdout", "--offset", "5",
               "--max-bytes", "9", "--json")
    logs.assert_called_once_with(JOB, "stdout", 5, 9)
    with patch("sandbed.dev.cli.jobcmds.job_list", return_value={}) as lst:
        invoke("job", "list", "--active", "--lab", "a", "--limit", "3", "--json")
    lst.assert_called_once_with(True, "a", 3)
    with patch("sandbed.dev.cli.jobcmds.job_wait", return_value={}) as wait:
        invoke("job", "wait", "--job", JOB, "--timeout", "7", "--json")
    wait.assert_called_once_with(JOB, 7)


def test_malformed_job_id_is_an_input_failure_before_any_systemd_call(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    result = invoke("job", "status", "--job", "../x", "--json")
    assert result.exit_code == 2
    payload = json.loads(result.output)
    assert payload["status"] == "failed"
    assert payload["failure_category"] == "input"


def test_capabilities_lists_job_commands_but_no_job_detach() -> None:
    with patch("sandbed.dev.capabilities.run") as fake:
        fake.return_value.returncode, fake.return_value.stdout = 1, ""
        payload = json.loads(invoke("capabilities", "--json").output)
    assert {f"job {s}" for s in SUBS} <= set(payload["commands"])
    assert not [c for c in payload["detach_commands"] if c.startswith("job")]


def test_job_help_lists_the_subcommands() -> None:
    out = invoke("job", "--help").output
    for sub in SUBS:
        assert sub in out


def test_unknown_stream_is_rejected_by_click() -> None:
    assert invoke("job", "logs", "--job", JOB, "--stream", "bogus").exit_code == 2
