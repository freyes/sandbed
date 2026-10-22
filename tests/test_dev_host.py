from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from sandbed.dev import host
from sandbed.dev.errors import HostError, InputError


def _result(code: int = 0, out: str = "") -> MagicMock:
    m = MagicMock()
    m.returncode, m.stdout = code, out
    return m


def _proc(code: int = 0, out: str = "") -> MagicMock:
    return MagicMock(returncode=code, stdout=out)


@patch("sandbed.dev.host.shutil.disk_usage")
@patch("sandbed.dev.host.subprocess.run")
def test_check_host_is_read_only_and_reports_every_failure(fake_run, usage) -> None:
    usage.return_value = MagicMock(free=10 << 30)
    fake_run.return_value = _proc(1)
    checks = host.check_host()
    failed = {c.name for c in checks if not c.ok}
    assert {"sudo", "lxd", "microceph", "rbd-pool", "disk"} <= failed
    assert all(c.remediation for c in checks if not c.ok)
    verbs = {"install", "create", "init", "bootstrap", "add", "enable", "delete"}
    for call in fake_run.call_args_list:
        assert not verbs & set(call.args[0]), call.args[0]


@patch("sandbed.dev.host.shutil.disk_usage", return_value=MagicMock(free=10 << 30))
@patch("sandbed.dev.host.subprocess.run")
def test_checks_never_prompt_and_never_hang(fake_run, _usage) -> None:
    fake_run.side_effect = subprocess.TimeoutExpired(cmd="x", timeout=30)
    checks = host.check_host()  # must return, not raise
    assert not any(c.ok for c in checks if c.name in ("sudo", "lxd", "microceph", "terraform"))
    for call in fake_run.call_args_list:
        argv = call.args[0]
        assert call.kwargs["stdin"] == subprocess.DEVNULL and call.kwargs["timeout"] == 30.0
        if argv[0] == "sudo":
            assert argv[1] == "-n", argv


@patch("sandbed.dev.host.shutil.disk_usage", return_value=MagicMock(free=10 << 30))
@patch("sandbed.dev.host.subprocess.run", side_effect=FileNotFoundError)
def test_missing_programs_are_failed_checks_not_crashes(_run, _usage) -> None:
    assert [c for c in host.check_host() if c.name == "terraform"][0].ok is False


@patch("sandbed.dev.host.subprocess.run")
def test_lxd_pool_must_wrap_the_dev_rbd_pool(fake_run) -> None:
    def fake(cmd, **_kw):
        if cmd[:3] == ["lxc", "storage", "show"]:
            return _proc(0, "driver: ceph\nconfig:\n  ceph.osd.pool_name: rbd\n")
        return _proc(1)

    fake_run.side_effect = fake
    pool = next(c for c in host.check_host() if c.name == "lxd-pool")
    assert pool.ok is False


def _prepare(existing_pools: bool):
    calls: list[list[str]] = []

    def fake(cmd, check=True, **_kw):
        calls.append(cmd)
        if cmd[:3] == ["sudo", "microceph.rados", "lspools"] and existing_pools:
            return _result(0, "rbd\nsandbed-dev\n")
        if cmd[:3] == ["lxc", "storage", "show"]:
            return _result(0 if existing_pools else 1, "")
        return _result(0, "")

    with (
        patch("sandbed.dev.host.run", side_effect=fake),
        patch("sandbed.dev.host.check_host", return_value=[]),
        patch("sandbed.dev.host.atomic_write_json"),
    ):
        host.prepare_host("50G")
    return [" ".join(c) for c in calls]


def test_prepare_runs_the_script_unchanged_then_creates_the_dev_pools() -> None:
    calls = _prepare(existing_pools=False)
    assert calls[0] == f"sudo {host.PREP_SCRIPT} 50G"
    assert any("osd pool create sandbed-dev" in c for c in calls)
    assert any("storage create sandbed-dev ceph ceph.osd.pool_name=sandbed-dev" in c for c in calls)
    assert not any("lxd init" in c or "disk add" in c for c in calls)


def test_prepare_is_idempotent_when_the_pools_exist() -> None:
    calls = _prepare(existing_pools=True)
    assert not any("pool create" in c or "storage create" in c for c in calls)


# --- resolve_backend ---------------------------------------------------------

_RESOLVE_CASES = [
    (("auto", None, True), "dir"),
    (("auto", None, False), "microceph"),
    (("auto", "ceph", True), "microceph"),
    (("auto", "ceph", False), "microceph"),
    (("auto", "dir", True), "dir"),
]


@pytest.mark.parametrize("args, expected", _RESOLVE_CASES)
def test_resolve_backend_picks_the_right_driver(args, expected) -> None:
    assert host.resolve_backend(*args) == expected


@pytest.mark.parametrize(
    "args, exc, fragment",
    [
        (("auto", "zfs", False), HostError, "unsupported driver"),
        (("dir", "ceph", False), HostError, "never replaced"),
        (("microceph", "dir", False), HostError, "never replaced"),
        (("bogus", None, False), InputError, "invalid storage backend"),
    ],
)
def test_resolve_backend_refuses_incompatible_or_invalid(args, exc, fragment) -> None:
    with pytest.raises(exc) as ei:
        host.resolve_backend(*args)
    assert fragment in str(ei.value)


# --- in_container -----------------------------------------------------------


@patch("sandbed.dev.host._probe", return_value=(0, "lxc"))
@patch("sandbed.dev.host._DEVLXD")
def test_in_container_detect_virt_says_lxd(_devlxd, _probe) -> None:
    assert host.in_container() is True
    _devlxd.exists.assert_not_called()


@patch("sandbed.dev.host._probe", return_value=(0, "none"))
@patch("sandbed.dev.host._DEVLXD")
def test_in_container_detect_virt_none_and_no_sock(devlxd, _probe) -> None:
    devlxd.exists.return_value = False
    assert host.in_container() is False


@patch("sandbed.dev.host._probe", return_value=(127, ""))
@patch("sandbed.dev.host._DEVLXD")
def test_in_container_missing_detect_virt_but_sock_present(devlxd, _probe) -> None:
    devlxd.exists.return_value = True
    assert host.in_container() is True


@patch("sandbed.dev.host._probe", return_value=(127, ""))
@patch("sandbed.dev.host._DEVLXD")
def test_in_container_missing_detect_virt_and_no_sock(devlxd, _probe) -> None:
    devlxd.exists.return_value = False
    assert host.in_container() is False


# --- pool_driver ------------------------------------------------------------


@patch("sandbed.dev.host._out")
def test_pool_driver_parses_driver_line(fake_out) -> None:
    fake_out.return_value = "driver: ceph\nconfig:\n  ceph.osd.pool_name: rbd"
    assert host.pool_driver() == "ceph"


@patch("sandbed.dev.host._out", return_value="")
def test_pool_driver_none_when_pool_absent(_fake_out) -> None:
    assert host.pool_driver() is None