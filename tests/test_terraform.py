import os

from sandbed.config import load_config
from sandbed.terraform import terraform_vars


def test_terraform_vars_include_shared_dir() -> None:
    cfg = load_config("manual", "up", shared_dir="/srv/shared")
    v = terraform_vars(cfg)
    assert v["shared_dir"] == "/srv/shared"
    assert v["vcpus"] == "4"


def test_terraform_vars_obs_count() -> None:
    v = terraform_vars(load_config("maas", "up", obs_count=0))
    assert v["obs_count"] == "0"


def test_terraform_vars_cloud_init_dir_absolute() -> None:
    v = terraform_vars(load_config("manual", "up"))
    assert os.path.isabs(v["cloud_init_templates_dir"])
    assert v["cloud_init_templates_dir"].endswith("templates/cloud-init")