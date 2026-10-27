from __future__ import annotations

import subprocess
from unittest.mock import patch

import pytest

from sandbed.dev.errors import HostError
from sandbed.dev.hostnet import taken_cidrs


def _proc(stdout="", code=0, stderr=""):
    return subprocess.CompletedProcess(args=[], returncode=code, stdout=stdout, stderr=stderr)


def test_ip_route_nonzero_exit_raises_hosterror() -> None:
    with patch("sandbed.dev.hostnet.subprocess.run", return_value=_proc("", code=1, stderr="oops")):
        with pytest.raises(HostError, match="ip -j route"):
            taken_cidrs()


def test_lxc_network_list_nonzero_exit_raises_hosterror() -> None:
    def fake(argv, **_kw):
        if argv[0] == "ip":
            return _proc("[]", code=0)
        return _proc("", code=1, stderr="lxd unreachable")

    with patch("sandbed.dev.hostnet.subprocess.run", side_effect=fake):
        with pytest.raises(HostError, match="lxc network list"):
            taken_cidrs()


def test_ip_route_timeout_raises_hosterror() -> None:
    with patch(
        "sandbed.dev.hostnet.subprocess.run",
        side_effect=subprocess.TimeoutExpired(["ip", "-j", "route"], 30),
    ):
        with pytest.raises(HostError, match="ip -j route"):
            taken_cidrs()


def test_lxc_network_list_timeout_raises_hosterror() -> None:
    def fake(argv, **_kw):
        if argv[0] == "ip":
            return _proc("[]", code=0)
        raise subprocess.TimeoutExpired(["lxc", "network", "list"], 30)

    with patch("sandbed.dev.hostnet.subprocess.run", side_effect=fake):
        with pytest.raises(HostError, match="lxc network list"):
            taken_cidrs()


def test_missing_ip_binary_raises_hosterror() -> None:
    with patch("sandbed.dev.hostnet.subprocess.run", side_effect=FileNotFoundError("ip")):
        with pytest.raises(HostError, match="ip -j route"):
            taken_cidrs()


def test_missing_lxc_binary_raises_hosterror() -> None:
    def fake(argv, **_kw):
        if argv[0] == "ip":
            return _proc("[]", code=0)
        raise FileNotFoundError("lxc")

    with patch("sandbed.dev.hostnet.subprocess.run", side_effect=fake):
        with pytest.raises(HostError, match="lxc network list"):
            taken_cidrs()


def test_unparsable_ip_json_raises_hosterror() -> None:
    with patch("sandbed.dev.hostnet.subprocess.run", return_value=_proc("not json", code=0)):
        with pytest.raises(HostError, match="ip -j route"):
            taken_cidrs()


def test_unparsable_lxc_json_raises_hosterror() -> None:
    def fake(argv, **_kw):
        if argv[0] == "ip":
            return _proc("[]", code=0)
        return _proc("not json", code=0)

    with patch("sandbed.dev.hostnet.subprocess.run", side_effect=fake):
        with pytest.raises(HostError, match="lxc network list"):
            taken_cidrs()


def test_empty_but_successful_output_is_fine() -> None:
    """No routes and no LXD networks is a valid host state, not an error."""
    with patch("sandbed.dev.hostnet.subprocess.run", return_value=_proc("", code=0)):
        assert taken_cidrs() == []


def test_routes_without_dst_slash_are_skipped() -> None:
    payload = '[{"dst": "10.0.0.0/24"}, {"dst": "default"}, {"dst": "127.0.0.1"}]'
    with patch("sandbed.dev.hostnet.subprocess.run", return_value=_proc(payload, code=0)):
        # only the entry with a '/' survives
        assert taken_cidrs() == ["10.0.0.0/24"]


def test_lxd_networks_with_none_auto_or_missing_ipv4_are_skipped() -> None:
    payload = (
        '[{"config": {"ipv4.address": "10.213.0.1/24"}},'
        ' {"config": {"ipv4.address": "none"}},'
        ' {"config": {"ipv4.address": "auto"}},'
        ' {"config": {}}]'
    )

    def fake(argv, **_kw):
        if argv[0] == "ip":
            return _proc("[]", code=0)
        return _proc(payload, code=0)

    with patch("sandbed.dev.hostnet.subprocess.run", side_effect=fake):
        assert taken_cidrs() == ["10.213.0.1/24"]


def test_routes_and_lxd_networks_are_combined() -> None:
    routes = '[{"dst": "192.168.0.0/24"}, {"dst": "172.16.5.0/24"}]'
    nets = '[{"config": {"ipv4.address": "10.213.0.1/24"}}]'

    def fake(argv, **_kw):
        if argv[0] == "ip":
            return _proc(routes, code=0)
        return _proc(nets, code=0)

    with patch("sandbed.dev.hostnet.subprocess.run", side_effect=fake):
        assert taken_cidrs() == ["192.168.0.0/24", "172.16.5.0/24", "10.213.0.1/24"]


def test_subprocess_run_uses_devnull_stdin_and_30s_timeout() -> None:
    """The host queries must not inherit our stdin and must be bounded."""
    seen: dict = {}

    def fake(argv, **kw):
        seen.update(kw)
        return _proc("[]", code=0)

    with patch("sandbed.dev.hostnet.subprocess.run", side_effect=fake):
        taken_cidrs()
    assert seen["stdin"] is subprocess.DEVNULL
    assert seen["timeout"] == 30.0
    assert seen["capture_output"] is True
    assert seen["text"] is True
