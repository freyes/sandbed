from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from sandbed.cli import cli
from sandbed.dev.capabilities import INTERFACE_VERSION


def _git(stdout: str = "") -> MagicMock:
    m = MagicMock()
    m.returncode = 0
    m.stdout = stdout
    return m


@patch("sandbed.dev.capabilities.run")
def test_capabilities_json(fake_run) -> None:
    fake_run.return_value = _git("abc123\n")
    result = CliRunner().invoke(cli, ["dev", "capabilities", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["command"] == "capabilities"
    assert payload["interface_version"] == INTERFACE_VERSION
    assert payload["providers"] == ["manual"]
    assert payload["profiles"] == ["single-node"]
    assert payload["scenarios"] == [{"name": "bootstrap-and-launch", "version": 1}]
    assert payload["storage_backends"] == ["microceph", "dir"]
    assert payload["sandbed"]["git_revision"] == "abc123"
    assert "capabilities" in payload["commands"]
    assert INTERFACE_VERSION == 3
    assert payload["detach_commands"] == [
        "build", "collect", "create", "deploy", "destroy", "host prepare", "test"]


@patch("sandbed.dev.capabilities.run", side_effect=FileNotFoundError("git not found"))
def test_capabilities_tolerates_missing_git(fake_run) -> None:
    result = CliRunner().invoke(cli, ["dev", "capabilities", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["sandbed"]["git_revision"] is None
    assert payload["sandbed"]["dirty"] is None


@patch("sandbed.dev.cli.capabilities", side_effect=RuntimeError("boom"))
def test_execute_reports_unexpected_exception_as_internal(fake_cap) -> None:
    result = CliRunner().invoke(cli, ["dev", "capabilities", "--json"])
    assert result.exit_code == 6
    payload = json.loads(result.output)
    assert payload["status"] == "failed"
    assert payload["failure_category"] == "internal"
    assert "boom" in payload["message"]
    assert payload["message"].startswith("RuntimeError: ")
    lines = [ln for ln in result.output.splitlines() if ln.strip()]
    assert len(lines) == 1


def test_use_testflinger_is_rejected_not_forwarded() -> None:
    result = CliRunner().invoke(cli, ["dev", "--use-testflinger", "capabilities"])
    assert result.exit_code == 2
    assert "not supported" in result.output


def test_the_legacy_command_is_still_registered() -> None:
    assert {"sunbeam", "dev"} <= set(cli.commands)


def test_build_outcome_success() -> None:
    from sandbed.dev.cli import build_outcome

    out = build_outcome("x", lambda: {"x": 1, "human": "ok"})
    assert (out.code, out.human) == (0, "ok")
    assert (out.payload["status"], out.payload["x"]) == ("succeeded", 1)


def test_build_outcome_dev_error() -> None:
    from sandbed.dev.cli import build_outcome
    from sandbed.dev.errors import InputError

    def body() -> dict:
        raise InputError("bad")

    out = build_outcome("x", body)
    assert out.code == 2
    assert out.payload["status"] == "failed"
    assert out.payload["failure_category"] == "input"


def test_build_outcome_unexpected_exception() -> None:
    from sandbed.dev.cli import build_outcome

    def body() -> dict:
        raise RuntimeError("boom")

    out = build_outcome("x", body)
    assert out.code == 6
    assert out.payload["message"].startswith("RuntimeError: ")


def test_build_outcome_keyboard_interrupt() -> None:
    from sandbed.dev.cli import build_outcome

    def body() -> dict:
        raise KeyboardInterrupt

    out = build_outcome("x", body)
    assert out.code == 130
    assert out.payload["status"] == "interrupted"


def test_build_outcome_folded_failure() -> None:
    from sandbed.dev.cli import build_outcome

    out = build_outcome("x", lambda: {"status": "failed", "exit_code": 4})
    assert (out.code, out.payload["status"]) == (4, "failed")
