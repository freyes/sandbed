from __future__ import annotations

from pathlib import Path

import pytest

from sandbed.dev.errors import InputError
from sandbed.dev.ssh import SshTarget


def target(first: bool = False) -> SshTarget:
    return SshTarget("10.213.0.7", "ubuntu", Path("/k/id"), Path("/k/known_hosts"), first)


def test_ssh_command_is_a_single_quoted_argument_vector() -> None:
    argv = target().ssh_argv(["sudo", "snap", "install", "--dangerous", "/home/ubuntu/a b.snap"])
    assert argv[0] == "ssh" and argv[-2] == "--"
    assert argv[-1] == "sudo snap install --dangerous '/home/ubuntu/a b.snap'"
    assert "ubuntu@10.213.0.7" in argv
    assert "UserKnownHostsFile=/k/known_hosts" in argv
    assert "IdentitiesOnly=yes" in argv and "BatchMode=yes" in argv


def test_shell_metacharacters_in_arguments_stay_data() -> None:
    argv = target().ssh_argv(["echo", "x; rm -rf /"])
    assert argv[-1] == "echo 'x; rm -rf /'"


def test_host_key_policy_is_strict_after_first_contact() -> None:
    assert "StrictHostKeyChecking=yes" in target().ssh_argv(["true"])
    assert "StrictHostKeyChecking=accept-new" in target(True).ssh_argv(["true"])


def test_empty_remote_command_is_rejected() -> None:
    with pytest.raises(InputError):
        target().ssh_argv([])


def test_scp_argv() -> None:
    argv = target().scp_argv(Path("/tmp/x.snap"), "/home/ubuntu/x.snap")
    assert argv[0] == "scp" and argv[-1] == "ubuntu@10.213.0.7:/home/ubuntu/x.snap"


@pytest.mark.parametrize("bad", [
    "relative/x", "/home/u/a b", "/home/u/a;touch x", "/home/u/$(id)", "/home/u/a:b",
    "/home/u/../etc/passwd", "/home/u/a\nb", "/home/u/'q'", "/home/u/*", "",
])
def test_unsafe_remote_paths_are_rejected_in_both_directions(bad) -> None:
    with pytest.raises(InputError):
        target().scp_argv(Path("/tmp/x"), bad)
    with pytest.raises(InputError):
        target().scp_from_argv(bad, Path("/tmp/x"))


def test_scp_from_requires_an_absolute_remote_path() -> None:
    argv = target().scp_from_argv("/home/ubuntu/r.tar.gz", Path("/tmp/r.tar.gz"))
    assert argv[-2:] == ["ubuntu@10.213.0.7:/home/ubuntu/r.tar.gz", "/tmp/r.tar.gz"]
    with pytest.raises(InputError):
        target().scp_from_argv("relative", Path("/tmp/x"))
