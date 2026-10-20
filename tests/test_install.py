from unittest.mock import patch

import pytest

from sandbed.config import load_config
from sandbed.install import install, verify_ceph
from sandbed.shell import PhaseError


@patch("sandbed.install.verify_ceph")
@patch("sandbed.install._installed", return_value=True)
def test_install_skips_present(fake_installed, fake_verify) -> None:
    with patch("sandbed.install.run") as fake_run:
        install(load_config("manual", "up"))
    assert all(c != ["snap", "install", "lxd"] for c in [c[0][0] for c in fake_run.call_args_list])


class _Out:
    def __init__(self, stdout: str = "", returncode: int = 0) -> None:
        self.stdout = stdout
        self.returncode = returncode


@patch(
    "sandbed.install.run",
    side_effect=[
        _Out(),  # microceph.ceph -s
        _Out("1 osds: 1 up (since 3h), 1 in (since 3h)"),  # microceph.ceph osd stat
        _Out("rbd\ncephfs\n"),  # microceph.rados lspools (ensure)
        _Out("rbd\ncephfs\n"),  # microceph.rados lspools (verify)
    ],
)
def test_verify_ceph_ok(fake_run) -> None:
    verify_ceph(load_config("manual", "up"))


@patch(
    "sandbed.install.run",
    side_effect=[
        _Out(),  # microceph.ceph -s
        _Out("2 osds: 2 up (since 3h), 2 in (since 3h)"),  # microceph.ceph osd stat
        _Out("cephfs\n"),  # microceph.rados lspools (ensure) — rbd missing
        _Out(),  # microceph.ceph osd pool create rbd
        _Out(),  # microceph.ceph osd pool application enable rbd rbd
        _Out("rbd\ncephfs\n"),  # microceph.rados lspools (verify)
    ],
)
def test_verify_ceph_creates_missing_rbd_pool(fake_run) -> None:
    verify_ceph(load_config("manual", "up"))
    cmds = [c[0][0] for c in fake_run.call_args_list]
    assert ["sudo", "microceph.ceph", "osd", "pool", "create", "rbd"] in cmds


@patch(
    "sandbed.install.run",
    side_effect=[
        _Out(),  # microceph.ceph -s
        _Out("0 osd: 0 up, 0 in"),  # no OSD up
    ],
)
def test_verify_ceph_no_osd_raises(fake_run) -> None:
    with pytest.raises(PhaseError):
        verify_ceph(load_config("manual", "up"))

@patch("sandbed.install.verify_ceph")
@patch("sandbed.install._installed", return_value=False)
@patch("sandbed.install.run")
def test_install_maas_does_not_install_host_maas_snap(fake_run, _installed, _verify) -> None:
    fake_run.return_value = _Out("/dev/loop9")
    install(load_config("maas", "up"))
    cmds = [c.args[0] for c in fake_run.call_args_list]
    assert ["sudo", "snap", "install", "openstack"] in cmds
    assert ["sudo", "snap", "install", "maas"] not in cmds
