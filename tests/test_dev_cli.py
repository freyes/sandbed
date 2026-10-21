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
    assert payload["sandbed"]["git_revision"] == "abc123"
    assert "capabilities" in payload["commands"]


def test_use_testflinger_is_rejected_not_forwarded() -> None:
    result = CliRunner().invoke(cli, ["dev", "--use-testflinger", "capabilities"])
    assert result.exit_code == 2
    assert "not supported" in result.output


def test_the_legacy_command_is_still_registered() -> None:
    assert {"sunbeam", "dev"} <= set(cli.commands)
