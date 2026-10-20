import shlex
from unittest.mock import MagicMock, patch

import pytest

from sandbed import remote

SSH = ["ssh", "-o", "X=1", "ubuntu@1.2.3.4"]


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


@pytest.fixture
def clock(monkeypatch):
    c = _Clock()
    monkeypatch.setattr(remote.time, "monotonic", c.monotonic)
    monkeypatch.setattr(remote.time, "sleep", c.sleep)
    return c


def _out(stdout: str = "", returncode: int = 0, stderr: str = "") -> MagicMock:
    m = MagicMock()
    m.stdout, m.returncode, m.stderr = stdout, returncode, stderr
    return m


class _Proc:
    """Stand-in for the `ssh ... tail -f` process."""

    def __init__(self, lines: list[str], returncode: int) -> None:
        self.stdout = iter(lines)
        self.returncode = returncode

    def wait(self) -> int:
        return self.returncode

    def poll(self):
        return self.returncode

    def terminate(self) -> None:
        self.terminated = True


def test_start_script_detaches_under_nohup_and_logs_to_files() -> None:
    script = remote.start_script("run", "cd ~/sandbed && uv run sandbed sunbeam manual up")
    assert "nohup sh -c" in script
    assert "> ~/.sandbed-jobs/run.log 2>&1 < /dev/null &" in script
    assert "echo $! > ~/.sandbed-jobs/run.pid" in script
    # the exit status is written to a file so a reconnecting client can read it
    assert "echo $? > ~/.sandbed-jobs/run.rc" in script
    # a job that is still running is attached to, never started twice
    assert 'kill -0 "$(cat ~/.sandbed-jobs/run.pid)"' in script


def test_start_script_quotes_the_command() -> None:
    script = remote.start_script("run", "echo 'it''s' && true")
    inner = shlex.split(script.split("nohup sh -c ")[1])[0]
    assert inner.startswith("(echo 'it''s' && true); echo $? > ")


def test_follow_script_resumes_at_the_given_line() -> None:
    script = remote.follow_script("run", 41)
    assert script == "tail -n +42 -f --pid=$(cat ~/.sandbed-jobs/run.pid) ~/.sandbed-jobs/run.log"


@patch("sandbed.remote.subprocess.Popen")
@patch("sandbed.remote.run")
def test_run_detached_returns_the_remote_exit_status(fake_run, fake_popen, clock) -> None:
    fake_run.side_effect = [_out("started\n"), _out("0\n")]
    fake_popen.return_value = _Proc(["one\n", "two\n"], 0)
    assert remote.run_detached(SSH, "run", "true") == 0
    start_cmd, rc_cmd = (c.args[0] for c in fake_run.call_args_list)
    assert start_cmd[:4] == SSH and "nohup sh -c" in start_cmd[-1]
    assert rc_cmd[:4] == SSH and "run.rc" in rc_cmd[-1]
    follow_cmd = fake_popen.call_args.args[0]
    assert follow_cmd[:4] == SSH and follow_cmd[-1].startswith("tail -n +1 -f")


@patch("sandbed.remote.subprocess.Popen")
@patch("sandbed.remote.run")
def test_run_detached_propagates_a_failing_remote_command(fake_run, fake_popen, clock) -> None:
    fake_run.side_effect = [_out("started\n"), _out("3\n")]
    fake_popen.return_value = _Proc(["boom\n"], 0)
    assert remote.run_detached(SSH, "run", "false") == 3


@patch("sandbed.remote.subprocess.Popen")
@patch("sandbed.remote.run")
def test_run_detached_reattaches_after_a_dropped_connection(fake_run, fake_popen, clock) -> None:
    fake_run.side_effect = [_out("started\n"), _out("0\n")]
    fake_popen.side_effect = [
        _Proc(["a\n", "b\n", "partial"], 255),  # the unfinished last line is not counted
        _Proc(["c\n"], 0),
    ]
    assert remote.run_detached(SSH, "run", "true") == 0
    first, second = (c.args[0][-1] for c in fake_popen.call_args_list)
    assert first.startswith("tail -n +1 -f")
    assert second.startswith("tail -n +3 -f")  # two complete lines were already shown
    assert clock.sleeps == [15]


@patch("sandbed.remote.subprocess.Popen")
@patch("sandbed.remote.run")
def test_run_detached_gives_up_after_5_minutes_without_a_connection(
    fake_run, fake_popen, clock
) -> None:
    fake_run.side_effect = [_out("started\n")]
    fake_popen.side_effect = lambda *a, **k: _Proc([], 255)
    with pytest.raises(TimeoutError, match="300s"):
        remote.run_detached(SSH, "run", "true")
    assert clock.now == 300


@patch("sandbed.remote.subprocess.Popen")
@patch("sandbed.remote.run")
def test_run_detached_resets_the_clock_when_the_connection_made_progress(
    fake_run, fake_popen, clock
) -> None:
    fake_run.side_effect = [_out("started\n"), _out("0\n")]
    flaky = [_Proc(["x\n"], 255)] * 30  # each attempt shows a line, so it never times out
    fake_popen.side_effect = [*(_Proc([f"{i}\n"], 255) for i in range(30)), _Proc([], 0)]
    assert remote.run_detached(SSH, "run", "true") == 0
    assert flaky and clock.now == 30 * 15


@patch("sandbed.remote.subprocess.Popen")
@patch("sandbed.remote.run")
def test_run_detached_retries_the_start_while_unreachable(fake_run, fake_popen, clock) -> None:
    fake_run.side_effect = [
        _out("", 255, "Connection refused"),
        _out("started\n"),
        _out("0\n"),
    ]
    fake_popen.return_value = _Proc([], 0)
    assert remote.run_detached(SSH, "run", "true") == 0
    assert clock.sleeps == [15]


@patch("sandbed.remote.subprocess.Popen")
@patch("sandbed.remote.run")
def test_run_detached_reports_a_missing_exit_status(fake_run, fake_popen, clock) -> None:
    fake_run.side_effect = [_out("started\n"), _out("255\n")]
    fake_popen.return_value = _Proc([], 0)
    assert remote.run_detached(SSH, "run", "true") == 255


@patch("sandbed.remote.subprocess.Popen")
@patch("sandbed.remote.run")
def test_run_detached_attaches_to_a_running_job(fake_run, fake_popen, clock) -> None:
    fake_run.side_effect = [_out("attach\n"), _out("0\n")]
    fake_popen.return_value = _Proc(["resumed\n"], 0)
    assert remote.run_detached(SSH, "run", "true") == 0
    fake_popen.assert_called_once()


class _InterruptedProc(_Proc):
    """`ssh ... tail -f` whose reader is interrupted by Ctrl+C."""

    def __init__(self) -> None:
        super().__init__([], None)
        self.terminated = False

        def lines():
            yield "tick 1\n"
            raise KeyboardInterrupt

        self.stdout = lines()

    def poll(self):
        return None


@patch("sandbed.remote.subprocess.Popen")
@patch("sandbed.remote.run")
def test_ctrl_c_leaves_the_job_running_and_says_how_to_stop_it(
    fake_run, fake_popen, clock, capsys
) -> None:
    fake_run.side_effect = [_out("started\n")]
    proc = _InterruptedProc()
    fake_popen.return_value = proc
    with pytest.raises(SystemExit) as exc:
        remote.run_detached(SSH, "run", "true")
    assert exc.value.code == 130
    assert proc.terminated  # only the local follower is stopped
    assert fake_run.call_count == 1  # nothing was sent to kill the remote job
    out = capsys.readouterr().out
    assert "keeps running on the node" in out
    assert "kill $(cat ~/.sandbed-jobs/run.pid)" in out
    assert "ubuntu@1.2.3.4" in out
