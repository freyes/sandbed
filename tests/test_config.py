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
