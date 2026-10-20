import json
from unittest.mock import MagicMock, patch

import pytest

from sandbed.config import load_config
from sandbed.shell import (
    PhaseError,
    RetryableConnectionError,
    is_transient_connection_error,
)
from sandbed.testflinger import (
    JOB_ID_FILE,
    allocated_job_id,
    parse_duration,
    remote_command,
    wait_for_device,
)


def _cfg(**kw):
    base = dict(use_testflinger=True, testflinger_queue="q", testflinger_key=["lp:user"])
    base.update(kw)
    return load_config("manual", "plan", **base)


_NOT_FOUND_ERR = "No data found for that job id.\n"


class _Out:
    def __init__(self, stdout: str = "", returncode: int = 0, stderr: str = "") -> None:
        self.stdout = stdout
        self.returncode = returncode
        self.stderr = stderr


def test_parse_duration() -> None:
    assert parse_duration("6h") == 21600
    assert parse_duration("30m") == 1800
    assert parse_duration("45s") == 45
    assert parse_duration("90") == 90


@patch("sandbed.testflinger.run", return_value=_Out("job_id: xyz\n"))
def test_allocated_job_id_reserve(fake_run, tmp_path, monkeypatch) -> None:
    import yaml as _yaml

    monkeypatch.chdir(tmp_path)
    assert allocated_job_id(_cfg()) == "xyz"
    cmd = fake_run.call_args[0][0]
    assert cmd[0] == "testflinger-cli"
    assert cmd[1] == "submit"
    assert "-q" in cmd
    job_file = cmd[cmd.index("-q") + 1]
    with open(job_file) as fh:
        job = _yaml.safe_load(fh)
    assert job["job_queue"] == "q"
    assert job["provision_data"]["distro"] == "resolute"
    assert job["reserve_data"]["ssh_keys"] == ["lp:user"]
    assert job["reserve_data"]["timeout"] == 21600


@patch("sandbed.testflinger.run", return_value=_Out("abc\n"))
def test_allocated_job_id_reuse(fake_run) -> None:
    assert allocated_job_id(_cfg(testflinger_job="REUSE")) == "REUSE"
    fake_run.assert_not_called()


def test_remote_command_strips_flags() -> None:
    import sandbed.testflinger as tf

    with patch.object(
        tf.sys,
        "argv",
        [
            "sandbed",
            "sunbeam",
            "--use-testflinger",
            "--testflinger-queue",
            "q",
            "--testflinger-key",
            "lp:user",
            "manual",
            "plan",
        ],
    ):
        cmd = remote_command(_cfg())
    assert "--use-testflinger" not in cmd
    assert "--testflinger-queue" not in cmd
    assert "--testflinger-key" not in cmd
    assert "q" not in cmd
    assert "lp:user" not in cmd
    assert cmd.endswith("sunbeam manual plan")


@patch("sandbed.testflinger.run")
def test_scp_repo_excludes_local_artifacts(fake_run) -> None:
    from sandbed.testflinger import scp_repo

    scp_repo(_cfg(), "1.2.3.4")
    cmd = fake_run.call_args.args[0]
    excludes = [cmd[i + 1] for i, a in enumerate(cmd) if a == "--exclude"]
    assert excludes == [
        ".venv",
        "__pycache__",
        "*.pyc",
        ".pytest_cache",
        ".ruff_cache",
        ".terraform",
    ]
    assert cmd[-2:] == [".", "ubuntu@1.2.3.4:~/sandbed"]


@patch("sandbed.testflinger.run")
def test_scp_repo_removes_stale_remote_venv_first(fake_run) -> None:
    from sandbed.testflinger import scp_repo

    scp_repo(_cfg(), "1.2.3.4")
    first, last = fake_run.call_args_list[0].args[0], fake_run.call_args_list[-1].args[0]
    assert first[0] == "ssh"
    assert first[-2:] == ["ubuntu@1.2.3.4", "rm -rf ~/sandbed/.venv"]
    assert last[0] == "rsync"


def test_write_and_read_job_id(tmp_path, monkeypatch) -> None:
    from sandbed.testflinger import _read_job_id, _write_job_id

    monkeypatch.chdir(tmp_path)
    _write_job_id("abc")
    assert _read_job_id() == "abc"
    assert (tmp_path / JOB_ID_FILE).read_text() == "abc\n"


def test_read_job_id_missing(tmp_path, monkeypatch) -> None:
    from sandbed.testflinger import _read_job_id

    monkeypatch.chdir(tmp_path)
    assert _read_job_id() is None


@patch(
    "sandbed.testflinger.run",
    side_effect=[_Out("reserve"), _Out(json.dumps({"job_queue": "q"}))],
)
def test_reuse_when_alive_and_matching_queue(fake_run, tmp_path, monkeypatch) -> None:
    from sandbed.testflinger import _write_job_id

    monkeypatch.chdir(tmp_path)
    _write_job_id("REUSE")
    with patch("sandbed.testflinger._reserve") as fake_reserve:
        with patch("sandbed.testflinger._warn_timeout_mismatch"):
            jid = allocated_job_id(_cfg())
    assert jid == "REUSE"
    fake_reserve.assert_not_called()


@patch(
    "sandbed.testflinger.run",
    side_effect=[_Out("complete"), _Out(json.dumps({"job_queue": "q"}))],
)
def test_new_submit_when_not_alive(fake_run, tmp_path, monkeypatch) -> None:
    from sandbed.testflinger import _read_job_id, _write_job_id

    monkeypatch.chdir(tmp_path)
    _write_job_id("STALE")
    with patch("sandbed.testflinger._reserve", return_value="NEW") as fake_reserve:
        jid = allocated_job_id(_cfg())
    assert jid == "NEW"
    fake_reserve.assert_called_once()
    assert _read_job_id() == "NEW"


@patch(
    "sandbed.testflinger.run",
    side_effect=[_Out("reserve"), _Out(json.dumps({"job_queue": "other"}))],
)
def test_new_submit_when_wrong_queue(fake_run, tmp_path, monkeypatch) -> None:
    from sandbed.testflinger import _write_job_id

    monkeypatch.chdir(tmp_path)
    _write_job_id("STALE")
    with patch("sandbed.testflinger._reserve", return_value="NEW") as fake_reserve:
        jid = allocated_job_id(_cfg())
    assert jid == "NEW"
    fake_reserve.assert_called_once()


@patch("sandbed.testflinger.run", return_value=_Out(returncode=1, stderr=_NOT_FOUND_ERR))
def test_job_queue_not_found_raises_without_retry(fake_run) -> None:
    from sandbed.testflinger import JobNotFoundError, _job_queue

    with pytest.raises(JobNotFoundError):
        _job_queue("GONE")
    assert fake_run.call_count == 1


@patch("sandbed.testflinger.run", return_value=_Out(returncode=1, stderr=_NOT_FOUND_ERR))
def test_dismiss_job_id_not_found(fake_run, tmp_path, monkeypatch, capsys) -> None:
    from sandbed.testflinger import _read_job_id, _write_job_id

    monkeypatch.chdir(tmp_path)
    _write_job_id("GONE")
    with patch("sandbed.testflinger._reserve", return_value="NEW") as fake_reserve:
        jid = allocated_job_id(_cfg())
    assert jid == "NEW"
    fake_reserve.assert_called_once()
    assert _read_job_id() == "NEW"
    out = capsys.readouterr().out
    assert "warning" in out
    assert "GONE" in out
    assert "not found" in out


@patch(
    "sandbed.testflinger.run",
    side_effect=[_Out("complete"), _Out(json.dumps({"job_queue": "q"}))],
)
def test_dismiss_job_id_not_alive_warns(fake_run, tmp_path, monkeypatch, capsys) -> None:
    from sandbed.testflinger import _write_job_id

    monkeypatch.chdir(tmp_path)
    _write_job_id("STALE")
    with patch("sandbed.testflinger._reserve", return_value="NEW"):
        allocated_job_id(_cfg())
    out = capsys.readouterr().out
    assert "warning" in out
    assert "STALE" in out
    assert "complete" in out


@patch(
    "sandbed.testflinger.run",
    side_effect=[_Out("reserve"), _Out(json.dumps({"job_queue": "other"}))],
)
def test_dismiss_job_id_wrong_queue_warns(fake_run, tmp_path, monkeypatch, capsys) -> None:
    from sandbed.testflinger import _write_job_id

    monkeypatch.chdir(tmp_path)
    _write_job_id("STALE")
    with patch("sandbed.testflinger._reserve", return_value="NEW"):
        allocated_job_id(_cfg())
    out = capsys.readouterr().out
    assert "warning" in out
    assert "other" in out


@patch("sandbed.testflinger._submit_job_file", return_value="NEWJOB")
def test_job_file_overwrites_dotfile(fake_submit, tmp_path, monkeypatch) -> None:
    from sandbed.testflinger import _read_job_id, _write_job_id

    monkeypatch.chdir(tmp_path)
    _write_job_id("OLD")
    jid = allocated_job_id(_cfg(testflinger_job_file="j.yaml"))
    assert jid == "NEWJOB"
    assert _read_job_id() == "NEWJOB"
    fake_submit.assert_called_once()


def test_humanize_duration() -> None:
    from sandbed.testflinger import _humanize_duration

    assert _humanize_duration(0) == "00:00"
    assert _humanize_duration(9.9) == "00:09"
    assert _humanize_duration(65) == "01:05"
    assert _humanize_duration(3661) == "01:01:01"


class _FakeConsole:
    def __init__(self, *args, **kwargs) -> None:
        self.logs: list[tuple] = []

    def log(self, *args, **kwargs) -> None:
        self.logs.append(args)


class _FakeLive:
    def __init__(self, console=None, **kwargs) -> None:
        self.console = console
        self.updates: list = []

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> bool:
        return False

    def update(self, renderable) -> None:
        self.updates.append(renderable)


def test_wait_for_device_logs_transitions() -> None:
    from itertools import count

    from sandbed import testflinger as tf

    clock = count(0)
    fake_console = _FakeConsole()
    with patch.object(tf, "console", fake_console):
        with patch.object(tf, "Live", _FakeLive):
            with patch.object(tf.time, "sleep"):
                with patch.object(tf.time, "monotonic", side_effect=lambda: next(clock)):
                    with patch.object(
                        tf, "_device_ip", side_effect=[None, None, "10.0.0.5"]
                    ):
                        with patch.object(
                            tf, "_job_status", side_effect=["waiting", "provision"]
                        ):
                            ip = wait_for_device(_cfg(), "JOB1")

    assert ip == "10.0.0.5"
    assert len(fake_console.logs) == 1
    msg = fake_console.logs[0][0]
    assert "JOB1" in msg
    assert "waiting" in msg
    assert "provision" in msg


def test_wait_for_device_first_state_not_logged() -> None:
    from itertools import count

    from sandbed import testflinger as tf

    clock = count(0)
    fake_console = _FakeConsole()
    with patch.object(tf, "console", fake_console):
        with patch.object(tf, "Live", _FakeLive):
            with patch.object(tf.time, "sleep"):
                with patch.object(tf.time, "monotonic", side_effect=lambda: next(clock)):
                    with patch.object(tf, "_device_ip", side_effect=[None, "10.0.0.5"]):
                        with patch.object(tf, "_job_status", side_effect=["waiting"]):
                            ip = wait_for_device(_cfg(), "JOB1")

    assert ip == "10.0.0.5"
    assert fake_console.logs == []


def test_is_transient_connection_error() -> None:
    assert is_transient_connection_error(
        "requests.exceptions.ConnectionError: HTTPSConnectionPool"
    )
    assert is_transient_connection_error("Max retries exceeded with url:")
    assert is_transient_connection_error("Network is unreachable")
    assert is_transient_connection_error("[Errno 101] Network is unreachable")
    assert not is_transient_connection_error("job not found")
    assert not is_transient_connection_error("")


def test_tf_run_retries_on_connection_error() -> None:
    from sandbed import testflinger as tf

    good = _Out("job_id: SUCCESS\n")
    err = RetryableConnectionError(
        ["testflinger-cli"], "/tmp/x", 1,
        "requests.exceptions.ConnectionError: Max retries exceeded",
    )

    with patch.object(tf, "run", side_effect=[err, err, good]):
        with patch("time.sleep"):
            result = tf._tf_run(["testflinger-cli", "reserve"])
    assert result.stdout == "job_id: SUCCESS\n"


def test_tf_run_retry_prints_message() -> None:
    from sandbed import testflinger as tf

    err = RetryableConnectionError(
        ["testflinger-cli"], "/tmp/x", 1,
        "requests.exceptions.ConnectionError: Max retries exceeded",
    )
    good = _Out("job_id: OK\n")
    printed: list[str] = []

    with patch.object(tf, "run", side_effect=[err, good]):
        with patch("time.sleep"):
            with patch.object(tf, "console"):
                tf.console.print = lambda msg, **kw: printed.append(msg)
                result = tf._tf_run(["testflinger-cli", "reserve"])

    assert result.stdout == "job_id: OK\n"
    assert len(printed) == 1
    msg = printed[0]
    assert "attempt 1" in msg
    assert "attempt 2" in msg
    assert "Max retries exceeded" in msg
    assert "retrying" in msg


def test_tf_run_retries_on_connection_error_check_false() -> None:
    from sandbed import testflinger as tf

    err_result = _Out(
        stdout="",
        returncode=1,
        stderr="requests.exceptions.ConnectionError: Max retries exceeded",
    )
    good = _Out("ok\n")

    with patch.object(tf, "run", side_effect=[err_result, err_result, good]):
        with patch("time.sleep"):
            result = tf._tf_run(["testflinger-cli", "status", "JOB"], check=False)
    assert result.stdout == "ok\n"


def test_tf_run_no_retry_on_phase_error() -> None:
    from sandbed import testflinger as tf

    err = PhaseError(["testflinger-cli"], "/tmp/x", 1)
    mock_run = MagicMock(side_effect=err)

    with patch.object(tf, "run", mock_run):
        with pytest.raises(PhaseError):
            tf._tf_run(["testflinger-cli", "reserve"])
    assert mock_run.call_count == 1


def test_tf_run_no_retry_on_non_connection_check_false() -> None:
    from sandbed import testflinger as tf

    err_result = _Out(stdout="", returncode=1, stderr="job not found")

    with patch.object(tf, "run", return_value=err_result):
        result = tf._tf_run(["testflinger-cli", "status", "JOB"], check=False)
    assert result.returncode == 1


def test_tf_run_exhausts_retries_and_reraises() -> None:
    from sandbed import testflinger as tf

    err = RetryableConnectionError(["testflinger-cli"], "/tmp/x", 1, "ConnectionError")
    mock_run = MagicMock(side_effect=err)

    with patch.object(tf, "run", mock_run):
        with patch("time.sleep"):
            with pytest.raises(RetryableConnectionError):
                tf._tf_run(["testflinger-cli", "reserve"])
    assert mock_run.call_count == tf.TF_MAX_ATTEMPTS

@patch("sandbed.testflinger.run")
def test_authorize_key_appends_the_key_idempotently(fake_run) -> None:
    from sandbed.testflinger import authorize_key

    authorize_key(_cfg(ssh_key="ssh-ed25519 AAAA sandbed"), "1.2.3.4")
    cmd = fake_run.call_args.args[0]
    assert cmd[0] == "ssh" and cmd[-2] == "ubuntu@1.2.3.4"
    script = cmd[-1]
    assert "grep -qxF 'ssh-ed25519 AAAA sandbed' ~/.ssh/authorized_keys" in script
    assert ">> ~/.ssh/authorized_keys" in script


@patch("sandbed.testflinger.run")
def test_authorize_key_without_a_key_does_nothing(fake_run) -> None:
    from sandbed.testflinger import authorize_key

    authorize_key(_cfg(ssh_key=""), "1.2.3.4")
    fake_run.assert_not_called()


class _Clock:
    """Fake time for wait_for_ssh: sleeping advances monotonic()."""

    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def _ssh_denied() -> _Out:
    return _Out("", returncode=255, stderr="ubuntu@1.2.3.4: Permission denied (publickey).\n")


def test_wait_for_ssh_returns_immediately_when_ssh_works(monkeypatch) -> None:
    from sandbed import testflinger as tf

    clock = _Clock()
    monkeypatch.setattr(tf.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(tf.time, "sleep", clock.sleep)
    with patch.object(tf, "run", return_value=_Out()) as fake_run:
        tf.wait_for_ssh("1.2.3.4")
    cmd = fake_run.call_args.args[0]
    assert cmd[0] == "ssh" and cmd[-2:] == ["ubuntu@1.2.3.4", "true"]
    assert "BatchMode=yes" in cmd and "ConnectTimeout=10" in cmd
    assert fake_run.call_args.kwargs == {"check": False}
    assert clock.sleeps == []


def test_wait_for_ssh_retries_every_15s_until_the_key_is_accepted(monkeypatch, capsys) -> None:
    from sandbed import testflinger as tf

    clock = _Clock()
    monkeypatch.setattr(tf.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(tf.time, "sleep", clock.sleep)
    with patch.object(tf, "run", side_effect=[_ssh_denied(), _ssh_denied(), _Out()]) as fake_run:
        tf.wait_for_ssh("1.2.3.4")
    assert fake_run.call_count == 3
    assert clock.sleeps == [15, 15]


def test_wait_for_ssh_gives_up_after_5_minutes(monkeypatch) -> None:
    import pytest

    from sandbed import testflinger as tf

    clock = _Clock()
    monkeypatch.setattr(tf.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(tf.time, "sleep", clock.sleep)
    with patch.object(tf, "run", return_value=_ssh_denied()) as fake_run:
        with pytest.raises(TimeoutError, match="Permission denied"):
            tf.wait_for_ssh("1.2.3.4")
    assert clock.now == 300
    assert fake_run.call_count == 21  # t=0, 15, ..., 300
    assert set(clock.sleeps) == {15}


def test_run_waits_for_ssh_then_syncs_authorizes_preps_and_runs(monkeypatch) -> None:
    from sandbed import testflinger

    calls: list[str] = []
    monkeypatch.setattr(testflinger, "allocated_job_id", lambda ctx: "J")
    monkeypatch.setattr(testflinger, "wait_for_device", lambda ctx, job: "1.2.3.4")
    monkeypatch.setattr(testflinger, "wait_for_ssh", lambda host: calls.append("wait"))
    monkeypatch.setattr(testflinger, "scp_repo", lambda ctx, host: calls.append("sync"))
    monkeypatch.setattr(testflinger, "authorize_key", lambda ctx, host: calls.append("authorize"))
    monkeypatch.setattr(testflinger, "prep_host", lambda ctx, host: calls.append("prep") or 0)
    monkeypatch.setattr(testflinger, "run_remote", lambda ctx, host: calls.append("run") or 0)
    assert testflinger.run_(_cfg()) == 0
    assert calls == ["wait", "sync", "authorize", "prep", "run"]
