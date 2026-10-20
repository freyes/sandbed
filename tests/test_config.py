from sandbed.config import load_config


def test_load_config_defaults() -> None:
    cfg = load_config("manual", "up")
    assert cfg.provider == "manual"
    assert cfg.mgmt_cidr == "192.168.200.0/24"
    assert cfg.vcpus == 4


def test_load_config_overrides() -> None:
    cfg = load_config("maas", "up", mgmt_cidr="10.0.0.0/24", obs_count=0)
    assert cfg.mgmt_cidr == "10.0.0.0/24"
    assert cfg.obs_count == 0


def test_config_testflinger_defaults() -> None:
    cfg = load_config("manual", "plan")
    assert cfg.use_testflinger is False
    assert cfg.testflinger_series == "resolute"
    assert cfg.testflinger_queue == ""
    assert cfg.testflinger_timeout == "6h"
    assert cfg.testflinger_key == []


def test_config_testflinger_override() -> None:
    cfg = load_config(
        "manual",
        "plan",
        use_testflinger=True,
        testflinger_queue="q",
        testflinger_series="noble",
        testflinger_timeout="30m",
        testflinger_key=("lp:user",),
    )
    assert cfg.use_testflinger is True
    assert cfg.testflinger_queue == "q"
    assert cfg.testflinger_series == "noble"
    assert cfg.testflinger_timeout == "30m"
    assert cfg.testflinger_key == ["lp:user"]

def test_maas_defaults() -> None:
    cfg = load_config("maas", "up")
    assert cfg.maas_name == "sandbed"
    assert cfg.maas_admin_password == "changeme"
    assert cfg.maas_deploy_series == "noble"
    assert cfg.maas_channel == "3.6/stable"
    assert cfg.maas_image_sync_timeout == "60m"
    assert (cfg.maas_vcpus, cfg.maas_memory, cfg.maas_disk) == (4, 8192, 50)


def test_maas_name_override_wins() -> None:
    assert load_config("maas", "up", maas_name="mycloud").maas_name == "mycloud"


def test_manual_keeps_empty_maas_name() -> None:
    assert load_config("manual", "up").maas_name == ""


def _home_with_keys(tmp_path, monkeypatch, **keys):
    ssh = tmp_path / ".ssh"
    ssh.mkdir()
    for name, content in keys.items():
        (ssh / name).write_text(content)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("SUNBEAM_SSH_KEY", raising=False)


def test_maas_ssh_key_defaults_to_the_users_public_key(tmp_path, monkeypatch) -> None:
    keys = {"id_rsa.pub": "ssh-rsa RSA\n", "id_ed25519.pub": "ssh-ed25519 ED\n"}
    _home_with_keys(tmp_path, monkeypatch, **keys)
    assert load_config("maas", "up").ssh_key == "ssh-ed25519 ED"


def test_maas_ssh_key_flag_wins_over_the_default(tmp_path, monkeypatch) -> None:
    _home_with_keys(tmp_path, monkeypatch, **{"id_ed25519.pub": "ssh-ed25519 ED\n"})
    assert load_config("maas", "up", ssh_key="ssh-ed25519 MINE").ssh_key == "ssh-ed25519 MINE"


def test_maas_ssh_key_stays_empty_without_any_key(tmp_path, monkeypatch) -> None:
    _home_with_keys(tmp_path, monkeypatch)
    assert load_config("maas", "up").ssh_key == ""


def test_manual_ssh_key_is_never_defaulted_from_disk(tmp_path, monkeypatch) -> None:
    _home_with_keys(tmp_path, monkeypatch, **{"id_ed25519.pub": "ssh-ed25519 ED\n"})
    assert load_config("manual", "up").ssh_key == ""
