from unittest.mock import patch

import pytest

from sandbed.config import load_config
from sandbed.preflight import PreflightError, check


class _Fake:
    def __init__(self, code: int) -> None:
        self.returncode = code


@patch("sandbed.preflight.os.path.exists", return_value=True)
@patch("sandbed.preflight.run", side_effect=lambda *a, **k: _Fake(0))
def test_check_ok(fake_run, fake_exists) -> None:
    check(load_config("manual", "up"))


@patch("sandbed.preflight.os.path.exists", return_value=False)
def test_check_no_kvm(fake_exists) -> None:
    with pytest.raises(PreflightError):
        check(load_config("manual", "up"))

KEY = "ssh-ed25519 AAAA test"


class _Out:
    def __init__(self, stdout: str = "", returncode: int = 0) -> None:
        self.stdout = stdout
        self.returncode = returncode


def _lxc_https(current: str):
    def fake(cmd, **_kwargs):
        return _Out(current if cmd[:3] == ["lxc", "config", "get"] else "")

    return fake


@patch("sandbed.preflight.os.path.exists", return_value=True)
def test_maas_preflight_accepts_unset_https_address(_exists) -> None:
    with patch("sandbed.preflight.run", side_effect=_lxc_https("")):
        check(load_config("maas", "up", ssh_key=KEY))


@patch("sandbed.preflight.os.path.exists", return_value=True)
def test_maas_preflight_accepts_matching_https_address(_exists) -> None:
    with patch("sandbed.preflight.run", side_effect=_lxc_https("172.16.1.1:8443")):
        check(load_config("maas", "up", ssh_key=KEY))


@patch("sandbed.preflight.os.path.exists", return_value=True)
def test_maas_preflight_rejects_foreign_https_address(_exists) -> None:
    with patch("sandbed.preflight.run", side_effect=_lxc_https("[::]:8443")):
        with pytest.raises(PreflightError, match="core.https_address"):
            check(load_config("maas", "up", ssh_key=KEY))


@patch("sandbed.preflight.os.path.exists", return_value=True)
def test_maas_https_address_follows_generic_cidr_and_port(_exists) -> None:
    ctx = load_config(
        "maas", "up", ssh_key=KEY, generic_cidr="10.9.0.0/24", lxd_https_port=9443
    )
    with patch("sandbed.preflight.run", side_effect=_lxc_https("10.9.0.1:9443")):
        check(ctx)


@patch("sandbed.preflight.os.path.exists", return_value=True)
def test_manual_preflight_ignores_https_address(_exists) -> None:
    with patch("sandbed.preflight.run", side_effect=_lxc_https("[::]:8443")) as fake_run:
        check(load_config("manual", "up"))
    assert not any(c.args[0][:3] == ["lxc", "config", "get"] for c in fake_run.call_args_list)


@patch("sandbed.preflight.os.path.exists", return_value=True)
def test_maas_preflight_requires_an_ssh_key(_exists, tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))  # no ~/.ssh keys to fall back on
    monkeypatch.delenv("SUNBEAM_SSH_KEY", raising=False)
    with patch("sandbed.preflight.run", side_effect=_lxc_https("")):
        with pytest.raises(PreflightError, match="--ssh-key"):
            check(load_config("maas", "up", ssh_key=""))


@patch("sandbed.preflight.os.path.exists", return_value=True)
def test_manual_preflight_does_not_require_an_ssh_key(_exists) -> None:
    with patch("sandbed.preflight.run", side_effect=_lxc_https("")):
        check(load_config("manual", "up", ssh_key=""))
