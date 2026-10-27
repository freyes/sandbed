from __future__ import annotations

import json
import subprocess
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from sandbed.cli import cli
from sandbed.dev import host
from sandbed.dev.errors import HostError, InputError


def _result(code: int = 0, out: str = "") -> MagicMock:
    m = MagicMock()
    m.returncode, m.stdout = code, out
    return m


def _proc(code: int = 0, out: str = "") -> MagicMock:
    return MagicMock(returncode=code, stdout=out)


_IMAGE_SHOW = "type: virtual-machine\narchitecture: x86_64\nfingerprint: imgfp123\n"


@patch("sandbed.dev.host.in_container", return_value=False)
@patch("sandbed.dev.host.shutil.disk_usage")
@patch("sandbed.dev.host.subprocess.run")
def test_check_host_is_read_only_and_reports_every_failure(fake_run, usage, _ic) -> None:
    usage.return_value = MagicMock(free=10 << 30)
    fake_run.return_value = _proc(1)
    checks = host.check_host()
    failed = {c.name for c in checks if not c.ok}
    assert {"sudo", "lxd", "microceph", "rbd-pool", "disk"} <= failed
    assert all(c.remediation for c in checks if not c.ok)
    verbs = {"install", "create", "init", "bootstrap", "add", "enable", "delete"}
    for call in fake_run.call_args_list:
        assert not verbs & set(call.args[0]), call.args[0]


@patch("sandbed.dev.host.in_container", return_value=False)
@patch("sandbed.dev.host.shutil.disk_usage", return_value=MagicMock(free=10 << 30))
@patch("sandbed.dev.host.subprocess.run")
def test_checks_never_prompt_and_never_hang(fake_run, _usage, _ic) -> None:
    fake_run.side_effect = subprocess.TimeoutExpired(cmd="x", timeout=30)
    checks = host.check_host()  # must return, not raise
    assert not any(c.ok for c in checks if c.name in ("sudo", "lxd", "microceph", "terraform"))
    for call in fake_run.call_args_list:
        argv = call.args[0]
        assert call.kwargs["stdin"] == subprocess.DEVNULL and call.kwargs["timeout"] == 30.0
        if argv[0] == "sudo":
            assert argv[1] == "-n", argv


@patch("sandbed.dev.host.in_container", return_value=False)
@patch("sandbed.dev.host.shutil.disk_usage", return_value=MagicMock(free=10 << 30))
@patch("sandbed.dev.host.subprocess.run", side_effect=FileNotFoundError)
def test_missing_programs_are_failed_checks_not_crashes(_run, _usage, _ic) -> None:
    assert [c for c in host.check_host() if c.name == "terraform"][0].ok is False


@patch("sandbed.dev.host.in_container", return_value=False)
@patch("sandbed.dev.host.subprocess.run")
def test_lxd_pool_must_wrap_the_dev_rbd_pool(fake_run, _ic) -> None:
    def fake(cmd, **_kw):
        if cmd[:3] == ["lxc", "storage", "show"]:
            return _proc(0, "driver: ceph\nconfig:\n  ceph.osd.pool_name: rbd\n")
        return _proc(1)

    fake_run.side_effect = fake
    pool = next(c for c in host.check_host() if c.name == "lxd-pool")
    assert pool.ok is False


def _prepare(existing_pools: bool, backend: str = "microceph",
              container: bool = False, driver: str | None = None,
              source: str | None = None, free_gib: int = 400):
    calls: list[list[str]] = []

    def fake(cmd, check=True, **_kw):
        calls.append(cmd)
        if cmd[:3] == ["lxc", "storage", "show"]:
            if driver is not None:
                lines = f"driver: {driver}\n"
                if source:
                    lines += f"config:\n  source: {source}\n"
                return _result(0, lines)
            return _result(0 if existing_pools else 1, "")
        if cmd[:3] == ["sudo", "microceph.rados", "lspools"] and existing_pools:
            return _result(0, "rbd\nsandbed-dev\n")
        if cmd[:3] == ["lxc", "image", "show"]:
            return _result(0, _IMAGE_SHOW)
        return _result(0, "")

    with (
        patch("sandbed.dev.host.run", side_effect=fake),
        patch("sandbed.dev.host.check_host", return_value=[]),
        patch("sandbed.dev.host.atomic_write_json"),
        patch("sandbed.dev.host.in_container", return_value=container),
        patch("sandbed.dev.host.shutil.disk_usage", return_value=MagicMock(free=free_gib << 30)),
    ):
        host.prepare_host("50G", backend=backend, source=source)
    # Drop the initial read-only `lxc storage show` probe used for backend resolution.
    actionable = [c for c in calls if c[:3] != ["lxc", "storage", "show"]]
    return [" ".join(c) for c in actionable]


def test_prepare_runs_the_script_unchanged_then_creates_the_dev_pools() -> None:
    calls = _prepare(existing_pools=False)
    assert calls[0] == f"sudo {host.PREP_SCRIPT} 50G"
    assert any("osd pool create sandbed-dev" in c for c in calls)
    assert any("storage create sandbed-dev ceph ceph.osd.pool_name=sandbed-dev" in c for c in calls)
    assert not any("lxd init" in c or "disk add" in c for c in calls)


def test_prepare_is_idempotent_when_the_pools_exist() -> None:
    calls = _prepare(existing_pools=True)
    assert not any("pool create" in c or "storage create" in c for c in calls)


def test_prepare_dir_fresh_creates_dir_pool_with_source(tmp_path) -> None:
    source = str(tmp_path / "sbd")
    tmp_path.joinpath("sbd").mkdir()
    calls = _prepare(existing_pools=False, backend="dir", container=True, source=source)
    assert calls[0] == f"sudo env SANDBED_PREP_MICROCEPH=0 {host.PREP_SCRIPT} 50G"
    assert any(f"storage create sandbed-dev dir source={source}" in c for c in calls)
    assert not any("microceph" in c or "rados" in c for c in calls)
    assert not any("lxd init" in c or "disk add" in c for c in calls)


def test_prepare_dir_idempotent_when_pool_exists(tmp_path) -> None:
    source = str(tmp_path / "sbd")
    tmp_path.joinpath("sbd").mkdir()
    calls = _prepare(existing_pools=True, backend="dir", container=True,
                    driver="dir", source=source)
    assert not any("storage create" in c for c in calls)


def test_prepare_dir_vs_existing_ceph_raises_hosterror() -> None:
    with pytest.raises(HostError, match="never replaced"):
        _prepare(existing_pools=True, backend="dir", container=False, driver="ceph")


def test_prepare_source_with_microceph_raises_inputerror(tmp_path) -> None:
    source = str(tmp_path / "sbd")
    tmp_path.joinpath("sbd").mkdir()
    with pytest.raises(InputError, match="dir backend"):
        _prepare(existing_pools=False, backend="microceph", container=False, source=source)


def test_prepare_source_must_be_absolute_existing_dir() -> None:
    with pytest.raises(InputError, match="absolute existing directory"):
        _prepare(existing_pools=False, backend="dir", container=True, source="relative/path")
    with pytest.raises(InputError, match="absolute existing directory"):
        _prepare(existing_pools=False, backend="dir", container=True,
                 source="/nonexistent-dir-xyz")


def test_prepare_dir_existing_pool_different_source_raises_hosterror(tmp_path) -> None:
    source = str(tmp_path / "sbd")
    tmp_path.joinpath("sbd").mkdir()
    calls: list[list[str]] = []

    def fake(cmd, check=True, **_kw):
        calls.append(cmd)
        if cmd[:3] == ["lxc", "storage", "show"]:
            return _result(0, "driver: dir\nconfig:\n  source: /old/path\n")
        return _result(0, "")

    with (
        patch("sandbed.dev.host.run", side_effect=fake),
        patch("sandbed.dev.host.check_host", return_value=[]),
        patch("sandbed.dev.host.atomic_write_json"),
        patch("sandbed.dev.host.in_container", return_value=True),
    ):
        with pytest.raises(HostError, match="never replaced"):
            host.prepare_host("50G", backend="dir", source=source)


def test_prepare_record_contains_backend_and_source_fields(tmp_path) -> None:
    source = str(tmp_path / "sbd")
    tmp_path.joinpath("sbd").mkdir()
    captured: dict = {}

    def fake(cmd, check=True, **_kw):
        if cmd[:3] == ["lxc", "storage", "show"]:
            return _result(1, "")
        if cmd[:3] == ["lxc", "image", "show"]:
            return _result(0, _IMAGE_SHOW)
        return _result(0, "")

    with (
        patch("sandbed.dev.host.run", side_effect=fake),
        patch("sandbed.dev.host.check_host", return_value=[]),
        patch("sandbed.dev.host.atomic_write_json", side_effect=lambda p, d: captured.update(d)),
        patch("sandbed.dev.host.in_container", return_value=True),
        patch("sandbed.dev.host.shutil.disk_usage", return_value=MagicMock(free=400 << 30)),
    ):
        host.prepare_host("50G", backend="dir", source=source)
    assert captured["storage_backend"] == "dir"
    assert captured["storage_source"] == source
    assert captured["in_container"] is True
    assert "osd_size" not in captured
    assert "microceph" not in captured


def test_prepare_microceph_record_contains_osd_size_and_microceph() -> None:
    captured: dict = {}

    def fake(cmd, check=True, **_kw):
        if cmd[:3] == ["lxc", "storage", "show"]:
            return _result(1, "")
        if cmd[:3] == ["sudo", "microceph.rados", "lspools"]:
            return _result(0, "sandbed-dev\n")
        if cmd[:3] == ["lxc", "image", "show"]:
            return _result(0, _IMAGE_SHOW)
        return _result(0, "")

    with (
        patch("sandbed.dev.host.run", side_effect=fake),
        patch("sandbed.dev.host.check_host", return_value=[]),
        patch("sandbed.dev.host.atomic_write_json", side_effect=lambda p, d: captured.update(d)),
        patch("sandbed.dev.host.in_container", return_value=False),
    ):
        host.prepare_host("50G")
    assert captured["storage_backend"] == "microceph"
    assert captured["osd_size"] == "50G"
    assert "microceph" in captured
    assert captured["in_container"] is False


def test_prepare_imports_the_pinned_image_once_and_records_it() -> None:
    calls: list[list[str]] = []
    captured: dict = {}
    shows = 0

    def fake(cmd, check=True, **_kw):
        nonlocal shows
        calls.append(cmd)
        if cmd[:3] == ["lxc", "storage", "show"]:
            return _result(1, "")
        if cmd[:3] == ["sudo", "microceph.rados", "lspools"]:
            return _result(0, "sandbed-dev\n")
        if cmd[:3] == ["lxc", "image", "show"]:
            shows += 1
            return _result(0, _IMAGE_SHOW) if shows > 1 else _result(1, "")
        return _result(0, "")

    with (
        patch("sandbed.dev.host.run", side_effect=fake),
        patch("sandbed.dev.host.check_host", return_value=[]),
        patch("sandbed.dev.host.atomic_write_json", side_effect=lambda p, d: captured.update(d)),
        patch("sandbed.dev.host.in_container", return_value=False),
    ):
        host.prepare_host("50G")
    copied = [c for c in calls if c[:3] == ["lxc", "image", "copy"]]
    assert copied == [["lxc", "image", "copy", "ubuntu:26.04/cloud", "local:", "--alias", "26.04"]]
    assert captured["image_remote"] == "ubuntu"
    assert captured["image_alias"] == "26.04"
    assert captured["image_fingerprint"] == "imgfp123"


def test_prepare_does_not_reimport_when_the_image_is_present() -> None:
    calls = _prepare(existing_pools=True)
    assert not any("image copy" in c for c in calls)
    assert any("image show" in c for c in calls)


def test_prepare_rejects_a_staged_image_of_the_wrong_type() -> None:
    calls: list[list[str]] = []

    def fake(cmd, check=True, **_kw):
        calls.append(cmd)
        if cmd[:3] == ["lxc", "storage", "show"]:
            return _result(1, "")
        if cmd[:3] == ["sudo", "microceph.rados", "lspools"]:
            return _result(0, "sandbed-dev\n")
        if cmd[:3] == ["lxc", "image", "show"]:
            return _result(0, "type: container\narchitecture: x86_64\n")
        return _result(0, "")

    with (
        patch("sandbed.dev.host.run", side_effect=fake),
        patch("sandbed.dev.host.check_host", return_value=[]),
        patch("sandbed.dev.host.atomic_write_json"),
        patch("sandbed.dev.host.in_container", return_value=False),
    ):
        with pytest.raises(HostError, match="virtual-machine"):
            host.prepare_host("50G")


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


# --- check_host: bare-metal name list ----------------------------------------


_BARE_METAL_NAMES = [
    "sudo", "systemd-user", "kvm", "nested-virt", "lxd",
    "microceph", "ceph-osd-up", "rbd-pool", "lxd-pool",
    "image",
    "terraform", "snapcraft", "squashfs-tools", "disk",
]


@patch("sandbed.dev.host.in_container", return_value=False)
@patch("sandbed.dev.host.shutil.disk_usage", return_value=MagicMock(free=10 << 30))
@patch("sandbed.dev.host.subprocess.run", return_value=_proc(1))
def test_bare_metal_check_names_match_today(_run, _usage, _ic) -> None:
    names = [c.name for c in host.check_host()]
    assert names == _BARE_METAL_NAMES


@patch("sandbed.dev.host.in_container", return_value=False)
@patch("sandbed.dev.host.shutil.disk_usage", return_value=MagicMock(free=10 << 30))
@patch("sandbed.dev.host.subprocess.run")
def test_image_check_reports_presence_and_absence(fake_run, _usage, _ic) -> None:
    def present(cmd, **_kw):
        if cmd[:3] == ["lxc", "image", "show"]:
            return _proc(0, _IMAGE_SHOW)
        return _proc(1)

    fake_run.side_effect = present
    check = next(c for c in host.check_host() if c.name == "image")
    assert check.ok is True
    assert "host prepare" in check.remediation

    fake_run.side_effect = lambda cmd, **_kw: _proc(1)
    check = next(c for c in host.check_host() if c.name == "image")
    assert check.ok is False
    assert "host prepare" in check.remediation


# --- check_host: container mode ----------------------------------------------


@patch("sandbed.dev.host.in_container", return_value=True)
@patch("sandbed.dev.host.shutil.disk_usage", return_value=MagicMock(free=10 << 30))
@patch("sandbed.dev.host.subprocess.run", return_value=_proc(1))
def test_container_mode_adds_vm_checks_and_drops_microceph(_run, _usage, _ic) -> None:
    names = [c.name for c in host.check_host()]
    assert "vm-devices" in names and "lxd-vm" in names
    assert "microceph" not in names
    assert "rbd-pool" not in names
    assert "ceph-osd-up" not in names


@patch("sandbed.dev.host.socket.gethostname", return_value="myhost")
@patch("sandbed.dev.host.in_container", return_value=True)
@patch("sandbed.dev.host.shutil.disk_usage", return_value=MagicMock(free=10 << 30))
@patch("sandbed.dev.host.subprocess.run", return_value=_proc(1))
def test_vm_devices_remediation_uses_hostname(_run, _usage, _ic, _hn) -> None:
    vm = next(c for c in host.check_host() if c.name == "vm-devices")
    assert "lxc config device add myhost kvm unix-char source=/dev/kvm" in vm.remediation
    assert "lxc config set myhost security.nesting=true" in vm.remediation


@patch("sandbed.dev.host.in_container", return_value=True)
@patch("sandbed.dev.host.shutil.disk_usage")
@patch("sandbed.dev.host.subprocess.run")
def test_container_mode_is_read_only_no_forbidden_verbs(fake_run, usage, _ic) -> None:
    usage.return_value = MagicMock(free=10 << 30)
    fake_run.return_value = _proc(1)
    host.check_host()
    verbs = {"install", "create", "init", "bootstrap", "add", "enable", "delete"}
    for call in fake_run.call_args_list:
        assert not verbs & set(call.args[0]), call.args[0]


@patch("sandbed.dev.host.in_container", return_value=True)
@patch("sandbed.dev.host.shutil.disk_usage")
@patch("sandbed.dev.host.subprocess.run")
def test_dir_pool_source_used_for_disk_check(fake_run, usage, _ic) -> None:
    usage.return_value = MagicMock(free=400 << 30)

    def fake(cmd, **_kw):
        if cmd[:3] == ["lxc", "storage", "show"]:
            return _proc(0, "driver: dir\nconfig:\n  source: /srv/sbd\n")
        return _proc(1)

    fake_run.side_effect = fake
    host.check_host()
    usage.assert_called_with("/srv/sbd")


@patch("sandbed.dev.host.in_container", return_value=False)
@patch("sandbed.dev.host.shutil.disk_usage", return_value=MagicMock(free=10 << 30))
@patch("sandbed.dev.host.subprocess.run")
def test_zfs_pool_is_unsupported_driver(fake_run, _usage, _ic) -> None:
    def fake(cmd, **_kw):
        if cmd[:3] == ["lxc", "storage", "show"]:
            return _proc(0, "driver: zfs\n")
        return _proc(1)

    fake_run.side_effect = fake
    pool = next(c for c in host.check_host() if c.name == "lxd-pool")
    assert pool.ok is False
    assert "unsupported driver zfs" in pool.detail


# --- host_facts / checks_payload ---------------------------------------------


@patch("sandbed.dev.host.in_container", return_value=True)
@patch("sandbed.dev.host.pool_driver", return_value=None)
def test_host_facts_container_no_pool(_pd, _ic) -> None:
    assert host.host_facts() == {"storage_backend": "dir", "in_container": True}


@patch("sandbed.dev.host.in_container", return_value=False)
@patch("sandbed.dev.host.pool_driver", return_value="ceph")
def test_host_facts_bare_metal_ceph(_pd, _ic) -> None:
    assert host.host_facts() == {"storage_backend": "microceph", "in_container": False}


@patch("sandbed.dev.host.in_container", return_value=False)
@patch("sandbed.dev.host.pool_driver", return_value="zfs")
def test_host_facts_unsupported_driver(_pd, _ic) -> None:
    assert host.host_facts() == {"storage_backend": "unsupported", "in_container": False}


@patch("sandbed.dev.host.host_facts",
       return_value={"storage_backend": "dir", "in_container": True})
def test_checks_payload_includes_host_facts(_hf) -> None:
    payload = host.checks_payload([])
    assert payload["storage_backend"] == "dir"
    assert payload["in_container"] is True
    assert payload["ok"] is True
    assert payload["checks"] == []

# --- dir pre-flight and lxd-vm remediation -----------------------------------


def test_prepare_dir_refuses_small_target_before_changing_anything(tmp_path) -> None:
    calls: list[list[str]] = []

    def fake(cmd, check=True, **_kw):
        calls.append(cmd)
        return _result(1, "")

    with (
        patch("sandbed.dev.host.run", side_effect=fake),
        patch("sandbed.dev.host.atomic_write_json") as write,
        patch("sandbed.dev.host.in_container", return_value=True),
        patch("sandbed.dev.host.shutil.disk_usage", return_value=MagicMock(free=28 << 30)),
    ):
        with pytest.raises(HostError, match="28 GiB free.*--storage-source"):
            host.prepare_host("50G", backend="auto", source=str(tmp_path))
    assert calls == [["lxc", "storage", "show", host.STORAGE_POOL]]
    write.assert_not_called()


def test_prepare_dir_preflight_measures_lxd_dir_without_source() -> None:
    with (
        patch("sandbed.dev.host.run", return_value=_result(1, "")),
        patch("sandbed.dev.host.in_container", return_value=True),
        patch("sandbed.dev.host.os.path.isdir", return_value=True),
        patch("sandbed.dev.host.shutil.disk_usage",
              return_value=MagicMock(free=10 << 30)) as usage,
    ):
        with pytest.raises(HostError):
            host.prepare_host("50G")
    usage.assert_called_once_with(host.LXD_DIR)


def test_prepare_dir_existing_pool_skips_preflight() -> None:
    calls = _prepare(existing_pools=True, backend="dir", container=True, driver="dir",
                     free_gib=1)
    assert not any("storage create" in c for c in calls)


def _lxd_vm(devices_present: bool) -> host.Check:
    def fake(cmd, **_kw):
        if cmd[:3] == ["lxc", "query", "/1.0"]:
            return _proc(0, '{"environment": {"driver": "lxc"}}')
        return _proc(1)

    with (
        patch("sandbed.dev.host.subprocess.run", side_effect=fake),
        patch("sandbed.dev.host.in_container", return_value=True),
        patch("sandbed.dev.host.shutil.disk_usage", return_value=MagicMock(free=400 << 30)),
        patch("sandbed.dev.host.os.path.exists", return_value=devices_present),
    ):
        return next(c for c in host.check_host() if c.name == "lxd-vm")


def test_lxd_vm_remediation_says_reload_when_devices_arrived_after_start() -> None:
    check = _lxd_vm(devices_present=True)
    assert check.ok is False
    assert check.remediation.startswith("LXD started before the KVM devices existed")
    assert "sudo systemctl reload snap.lxd.daemon" in check.remediation


def test_lxd_vm_remediation_points_at_devices_when_missing() -> None:
    check = _lxd_vm(devices_present=False)
    assert check.remediation.startswith("pass through the vm-devices first")


# --- systemd-user and tty-less sudo -------------------------------------------

_SYSTEMD_REMEDIATION = (
    "enable a user manager: sudo loginctl enable-linger $USER, then export "
    "XDG_RUNTIME_DIR=/run/user/$(id -u) (needs systemd >= 240)"
)
_SUDO_REMEDIATION = (
    "background jobs run without a terminal, so cached 'sudo -v' credentials do not "
    "carry over: allow passwordless sudo (NOPASSWD) for this user"
)


def _systemd_probe(version_out=(0, "systemd 255 (255.4-1)"), manager=0, linger=(0, "Linger=yes")):
    def fake(cmd, env=None):
        if cmd[0] == "systemd-run":
            return version_out
        if cmd[0] == "systemctl":
            return manager, ""
        if cmd[0] == "loginctl":
            return linger
        raise AssertionError(cmd)

    return patch("sandbed.dev.host._probe", side_effect=fake)


def test_systemd_user_ok_when_version_255_and_user_manager_reachable() -> None:
    with _systemd_probe():
        check = host.check_systemd_user()
    assert check.name == "systemd-user" and check.ok is True
    assert "255" in check.detail and "linger yes" in check.detail


def test_systemd_user_probes_use_the_bus_env_and_read_only_argv() -> None:
    with patch("sandbed.dev.host._probe", return_value=(0, "systemd 255")) as probe:
        host.check_systemd_user()
    argvs = [c.args[0] for c in probe.call_args_list]
    assert ["systemd-run", "--version"] in argvs
    assert ["systemctl", "--user", "show-environment"] in argvs
    for call in probe.call_args_list:
        assert call.args[1] is not None and "PATH" in call.args[1]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"version_out": (127, "")},  # missing
        {"version_out": (0, "systemd 239 (239-1)")},  # old
        {"manager": 1},  # no user manager
    ],
    ids=["missing", "old", "no-user-manager"],
)
def test_systemd_user_fails_with_remediation(kwargs) -> None:
    with _systemd_probe(**kwargs):
        check = host.check_systemd_user()
    assert check.ok is False
    assert check.remediation == _SYSTEMD_REMEDIATION


@pytest.mark.parametrize(
    "linger, word",
    [((0, "Linger=yes"), "yes"), ((0, "Linger=no"), "no"), ((1, ""), "unknown")],
)
def test_linger_state_only_changes_the_detail(linger, word) -> None:
    with _systemd_probe(linger=linger):
        check = host.check_systemd_user()
    assert check.ok is True
    assert f"linger {word}" in check.detail


@patch("sandbed.dev.host.subprocess.run", return_value=_proc(0))
def test_sudo_runs_without_a_controlling_terminal_when_setsid_exists(fake_run) -> None:
    with patch("sandbed.dev.host.shutil.which", return_value="/usr/bin/setsid"):
        check = host.check_sudo()
    assert check.name == "sudo" and check.ok is True
    assert fake_run.call_args.args[0] == ["setsid", "-w", "sudo", "-n", "true"]
    assert fake_run.call_args.kwargs["stdin"] == subprocess.DEVNULL
    assert fake_run.call_args.kwargs["timeout"] == host.PROBE_TIMEOUT


@patch("sandbed.dev.host.subprocess.run", return_value=_proc(0))
def test_sudo_falls_back_to_plain_sudo_without_setsid(fake_run) -> None:
    with patch("sandbed.dev.host.shutil.which", return_value=None):
        host.check_sudo()
    assert fake_run.call_args.args[0] == ["sudo", "-n", "true"]
    assert fake_run.call_args.kwargs["stdin"] == subprocess.DEVNULL


@patch("sandbed.dev.host.subprocess.run", return_value=_proc(1))
def test_sudo_notty_failure_carries_the_nopasswd_remediation(_run) -> None:
    check = host.check_sudo()
    assert check.ok is False
    assert check.remediation == _SUDO_REMEDIATION


@patch("sandbed.dev.host.in_container", return_value=False)
@patch("sandbed.dev.host.shutil.disk_usage", return_value=MagicMock(free=10 << 30))
@patch("sandbed.dev.host.subprocess.run")
def test_new_checks_use_no_mutating_verb_and_appear_in_the_payload(fake_run, _u, _ic) -> None:
    fake_run.return_value = _proc(1)
    checks = host.check_host()
    with patch("sandbed.dev.host.host_facts", return_value={}):
        payload = host.checks_payload(checks)
    names = [c["name"] for c in payload["checks"]]
    assert names[:2] == ["sudo", "systemd-user"]
    verbs = {"install", "create", "init", "bootstrap", "add", "enable", "delete", "start"}
    for call in fake_run.call_args_list:
        assert not verbs & set(call.args[0]), call.args[0]
        assert call.args[0][:2] != ["systemd-run", "true"]


def test_host_check_json_exits_3_when_only_systemd_user_fails() -> None:
    passing = [host.Check("sudo", True, "ok"), host.Check("kvm", True, "ok"),
               host.Check("systemd-user", False, "unreachable", _SYSTEMD_REMEDIATION)]
    with (
        patch("sandbed.dev.commands.check_host", return_value=passing),
        patch("sandbed.dev.host.host_facts",
              return_value={"storage_backend": "dir", "in_container": False}),
    ):
        result = CliRunner().invoke(cli, ["dev", "host", "check", "--json"])
    payload = json.loads(result.output)
    assert result.exit_code == 3
    assert payload["ok"] is False
    assert [c["name"] for c in payload["checks"] if not c["ok"]] == ["systemd-user"]
