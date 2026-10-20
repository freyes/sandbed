from unittest.mock import patch

from sandbed.config import load_config
from sandbed.maas import setup_maas


@patch("sandbed.maas.console")
@patch("sandbed.maas.terraform")
def test_setup_maas_resolves_then_applies_maas_config(fake_tf, _console) -> None:
    ctx = load_config("maas", "up")
    setup_maas(ctx)
    assert [c[0] for c in fake_tf.method_calls] == ["resolve_maas", "apply_maas_config"]
    fake_tf.resolve_maas.assert_called_once_with(ctx)
    fake_tf.apply_maas_config.assert_called_once_with(ctx)


@patch("sandbed.maas.console")
@patch("sandbed.maas.terraform")
def test_setup_maas_prints_login_and_warns_on_default_password(fake_tf, fake_console) -> None:
    ctx = load_config("maas", "up")
    ctx.maas_url = "http://172.16.1.2:5240/MAAS"
    setup_maas(ctx)
    printed = " ".join(str(c.args[0]) for c in fake_console.print.call_args_list)
    assert "http://172.16.1.2:5240/MAAS" in printed
    assert "admin" in printed
    assert "default" in printed


@patch("sandbed.maas.console")
@patch("sandbed.maas.terraform")
def test_setup_maas_no_warning_for_custom_password(fake_tf, fake_console) -> None:
    ctx = load_config("maas", "up", maas_admin_password="s3cret")
    setup_maas(ctx)
    printed = " ".join(str(c.args[0]) for c in fake_console.print.call_args_list)
    assert "warning" not in printed
