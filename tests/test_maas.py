from unittest.mock import patch

from sandbed.config import load_config
from sandbed.maas import _ensure_maas_machine


@patch("sandbed.maas.run")
def test_ensure_maas_machine_lxd_power(fake_run) -> None:
    ctx = load_config("maas", "up", maas_name="m", maas_url="http://maas")
    _ensure_maas_machine("m:http://maas", "cloud-1", ctx)
    args = fake_run.call_args[0][0]
    assert "power_type=lxd" in args
    assert "power_parameters_instance_name=cloud-1" in args