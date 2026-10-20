from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from sandbed.cli import PHASES, cli


@patch("sandbed.terraform.run")
def test_cli_plan_manual(fake_run) -> None:
    fake_run.return_value.stdout = ""
    fake_run.return_value.returncode = 0
    result = CliRunner().invoke(cli, ["sunbeam", "manual", "plan"])
    assert result.exit_code == 0


def test_cli_maas_up_needs_no_credentials(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    with patch("sandbed.cli._dispatch") as dispatch, patch("sandbed.cli.terraform") as tf:
        result = CliRunner().invoke(cli, ["sunbeam", "maas", "up", "--no-hooks"])
    assert result.exit_code == 0, result.output
    assert [c.args[1] for c in dispatch.call_args_list] == PHASES
    # the MAAS connection is read from terraform outputs right before the phases that need it
    assert tf.resolve_maas.call_count == 2


def test_cli_maas_sunbeam_action_resolves_maas(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    with patch("sandbed.cli._dispatch"), patch("sandbed.cli.terraform") as tf:
        result = CliRunner().invoke(cli, ["sunbeam", "maas", "sunbeam", "--no-hooks"])
    assert result.exit_code == 0, result.output
    tf.resolve_maas.assert_called_once()


@pytest.mark.parametrize("flag", ["--maas-url", "--maas-token"])
def test_cli_external_maas_flags_removed(flag) -> None:
    result = CliRunner().invoke(cli, ["sunbeam", "maas", "plan", flag, "x"])
    assert result.exit_code == 2
    assert "No such option" in result.output


def test_cli_rejects_bad_image_sync_timeout() -> None:
    result = CliRunner().invoke(
        cli, ["sunbeam", "maas", "plan", "--maas-image-sync-timeout", "soon"]
    )
    assert result.exit_code == 2
    assert "<N>m or <N>h" in result.output


@patch("sandbed.terraform.run")
def test_cli_plan_maas_notes_that_maas_config_is_skipped(fake_run) -> None:
    fake_run.return_value.stdout = ""
    fake_run.return_value.returncode = 0
    result = CliRunner().invoke(cli, ["sunbeam", "maas", "plan"])
    assert result.exit_code == 0, result.output
    assert "maas-config is not planned" in result.output


@patch("sandbed.testflinger.run_", return_value=0)
def test_cli_testflinger_requires_queue(fake_run) -> None:
    result = CliRunner().invoke(cli, ["sunbeam", "--use-testflinger", "manual", "plan"])
    assert result.exit_code != 0
    assert "testflinger-queue" in result.output


@patch("sandbed.testflinger.run_", return_value=0)
def test_cli_testflinger_job_file_conflict(fake_run) -> None:
    result = CliRunner().invoke(
        cli,
        [
            "sunbeam",
            "--use-testflinger",
            "--testflinger-queue",
            "q",
            "--testflinger-key",
            "lp:user",
            "--testflinger-job",
            "abc",
            "--testflinger-job-file",
            "j.yaml",
            "manual",
            "plan",
        ],
    )
    assert result.exit_code != 0
    assert "mutually exclusive" in result.output


@patch("sandbed.testflinger.run_", return_value=0)
def test_cli_testflinger_requires_key_on_allocate(fake_run) -> None:
    result = CliRunner().invoke(
        cli, ["sunbeam", "--use-testflinger", "--testflinger-queue", "q", "manual", "plan"]
    )
    assert result.exit_code != 0
    assert "testflinger-key" in result.output


@patch("sandbed.testflinger.run_", return_value=0)
def test_cli_testflinger_delegates(fake_run) -> None:
    result = CliRunner().invoke(
        cli,
        [
            "sunbeam",
            "--use-testflinger",
            "--testflinger-queue",
            "q",
            "--testflinger-key",
            "lp:user",
            "manual",
            "plan",
        ],
    )
    assert result.exit_code == 0
    fake_run.assert_called_once()


@patch("sandbed.testflinger.run_", return_value=0)
def test_cli_testflinger_reuse_no_key_needed(fake_run) -> None:
    result = CliRunner().invoke(
        cli,
        [
            "sunbeam",
            "--use-testflinger",
            "--testflinger-queue",
            "q",
            "--testflinger-job",
            "REUSE",
            "manual",
            "plan",
        ],
    )
    assert result.exit_code == 0
    fake_run.assert_called_once()

def test_cli_lxd_https_port_flag(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    seen = {}

    def fake_dispatch(ctx, phase):
        seen["port"] = ctx.lxd_https_port

    with patch("sandbed.cli._dispatch", side_effect=fake_dispatch), patch("sandbed.cli.terraform"):
        result = CliRunner().invoke(
            cli, ["sunbeam", "maas", "sunbeam", "--no-hooks", "--lxd-https-port", "9443"]
        )
    assert result.exit_code == 0, result.output
    assert seen["port"] == 9443


def test_cli_prepares_the_ssh_key_for_every_action(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    prepare = MagicMock()
    monkeypatch.setattr("sandbed.cli.sshkey", MagicMock(prepare=prepare))
    with patch("sandbed.cli._dispatch"):
        result = CliRunner().invoke(cli, ["sunbeam", "manual", "up", "--no-hooks"])
    assert result.exit_code == 0, result.output
    prepare.assert_called_once()


@patch("sandbed.testflinger.run_", return_value=0)
def test_cli_testflinger_rejects_a_key_outside_the_working_directory(fake_run, monkeypatch) -> None:
    def outside(ctx):
        ctx.ssh_key = "ssh-ed25519 AAAA"
        ctx.ssh_identity = "/elsewhere/key"

    monkeypatch.setattr("sandbed.cli.sshkey", MagicMock(prepare=outside))
    result = CliRunner().invoke(
        cli,
        ["sunbeam", "--use-testflinger", "--testflinger-queue", "q", "--testflinger-key", "lp:u",
         "manual", "plan"],
    )
    assert result.exit_code == 2
    assert "inside the working directory" in result.output
    fake_run.assert_not_called()


@patch("sandbed.testflinger.run_", return_value=0)
def test_cli_testflinger_accepts_a_key_inside_the_working_directory(fake_run, monkeypatch) -> None:
    import os

    def inside(ctx):
        ctx.ssh_key = "ssh-ed25519 AAAA"
        ctx.ssh_identity = os.path.join(os.getcwd(), ".sandbed_ed25519")

    monkeypatch.setattr("sandbed.cli.sshkey", MagicMock(prepare=inside))
    result = CliRunner().invoke(
        cli,
        ["sunbeam", "--use-testflinger", "--testflinger-queue", "q", "--testflinger-key", "lp:u",
         "manual", "plan"],
    )
    assert result.exit_code == 0, result.output
    fake_run.assert_called_once()
