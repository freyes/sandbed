from unittest.mock import patch

from click.testing import CliRunner

from sandbed.cli import cli


@patch("sandbed.terraform.run")
def test_cli_plan_manual(fake_run) -> None:
    result = CliRunner().invoke(cli, ["sunbeam", "manual", "plan"])
    assert result.exit_code == 0


def test_cli_maas_requires_credentials() -> None:
    result = CliRunner().invoke(cli, ["sunbeam", "maas", "up"])
    assert result.exit_code != 0
    assert "requires" in result.output.lower() or result.exception is not None


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