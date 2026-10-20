import json
import os
from unittest.mock import MagicMock, patch

import pytest

from sandbed import terraform
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

# --- managed MAAS -------------------------------------------------------------------------------


def _out(stdout: str = "", returncode: int = 0) -> MagicMock:
    m = MagicMock()
    m.stdout = stdout
    m.returncode = returncode
    return m


def _var_names(cmd: list[str]) -> set[str]:
    return {a.removeprefix("-var=").split("=", 1)[0] for a in cmd if a.startswith("-var=")}


def test_terraform_vars_maas() -> None:
    v = terraform_vars(load_config("maas", "up", maas_deploy_series="jammy", maas_disk=80))
    assert v["deploy_series"] == "jammy"
    assert v["maas_disk"] == "80"
    assert v["maas_name"] == "sandbed"
    assert v["image_sync_timeout"] == "60m"


@patch("sandbed.terraform.run")
def test_maas_apply_passes_password_via_env_not_var(fake_run) -> None:
    fake_run.return_value = _out()
    ctx = load_config("maas", "up", ssh_key="k", maas_admin_password="s3cret")
    terraform.apply(ctx)
    apply_call = [c for c in fake_run.call_args_list if c.args[0][1] == "-chdir=terraform/maas"][-1]
    assert apply_call.kwargs["env"]["TF_VAR_maas_admin_password"] == "s3cret"
    assert "maas_admin_password" not in _var_names(apply_call.args[0])
    assert not any("s3cret" in a for a in apply_call.args[0])
    assert "maas_vcpus" in _var_names(apply_call.args[0])


@patch("sandbed.terraform.run")
def test_apply_maas_config_command(fake_run) -> None:
    fake_run.return_value = _out()
    ctx = load_config("maas", "up", ssh_key="k", maas_parallelism=4)
    terraform.apply_maas_config(ctx)
    init, apply_ = [c.args[0] for c in fake_run.call_args_list]
    assert init == ["terraform", "-chdir=terraform/maas-config", "init", "-backend=false"]
    assert apply_[:5] == [
        "terraform", "-chdir=terraform/maas-config", "apply", "-auto-approve", "-parallelism=4",
    ]
    assert _var_names(apply_) == {
        "deploy_series", "maas_name", "maas_space", "generic_cidr", "ssh_key", "upstream_dns",
        "image_sync_timeout",
    }


@patch("sandbed.terraform.run")
def test_resolve_maas_fills_ctx_from_outputs(fake_run) -> None:
    outputs = {
        "maas_url": {"value": "http://172.16.1.2:5240/MAAS"},
        "maas_api_key": {"value": "ck:tk:sk", "sensitive": True},
        "maas_admin_password": {"value": "s3cret", "sensitive": True},
        "client_mgmt_ip": {"value": "192.168.200.7"},
    }
    fake_run.return_value = _out(json.dumps(outputs))
    ctx = load_config("maas", "up")
    terraform.resolve_maas(ctx)
    assert ctx.maas_url == "http://172.16.1.2:5240/MAAS"
    assert ctx.maas_token == "ck:tk:sk"
    assert ctx.maas_admin_password == "s3cret"
    assert ctx.ssh_host == "192.168.200.7"


@patch("sandbed.terraform.run")
def test_resolve_maas_keeps_explicit_ssh_host(fake_run) -> None:
    fake_run.return_value = _out(json.dumps({
        "maas_url": {"value": "u"}, "maas_api_key": {"value": "t"},
        "client_mgmt_ip": {"value": "192.168.200.7"},
    }))
    ctx = load_config("maas", "up", ssh_host="10.9.9.9")
    terraform.resolve_maas(ctx)
    assert ctx.ssh_host == "10.9.9.9"


@patch("sandbed.terraform.run")
def test_resolve_maas_required_raises_when_outputs_missing(fake_run) -> None:
    fake_run.return_value = _out("{}")
    with pytest.raises(RuntimeError, match="maas up"):
        terraform.resolve_maas(load_config("maas", "up"))


@patch("sandbed.terraform.run")
def test_resolve_maas_optional_is_silent_when_outputs_missing(fake_run) -> None:
    fake_run.return_value = _out("", returncode=1)
    ctx = load_config("maas", "destroy")
    terraform.resolve_maas(ctx, required=False)
    assert ctx.maas_url == ""


@patch("sandbed.terraform.run")
def test_maas_destroy_skips_maas_config_and_drops_its_state(fake_run, tmp_path) -> None:
    fake_run.return_value = _out()
    tf_dir = tmp_path / "terraform"
    (tf_dir / "maas-config").mkdir(parents=True)
    state = tf_dir / "maas-config" / "terraform.tfstate"
    state.write_text("{}")
    ctx = load_config("maas", "destroy", ssh_key="k")
    ctx.terraform_dir = str(tf_dir)
    terraform.destroy_terraform(ctx)
    modules = [c.args[0][1] for c in fake_run.call_args_list if "destroy" in c.args[0]]
    assert modules == [f"-chdir={tf_dir}/maas", f"-chdir={tf_dir}/network"]
    assert not state.exists()


@patch("sandbed.terraform.run")
def test_manual_destroy_unchanged(fake_run) -> None:
    fake_run.return_value = _out()
    terraform.destroy_terraform(load_config("manual", "destroy", ssh_key="k"))
    calls = fake_run.call_args_list
    assert [c.args[0][1] for c in calls] == ["-chdir=terraform/manual", "-chdir=terraform/network"]
    assert all("env" not in c.kwargs for c in calls)


def _streams(call) -> bool:
    return call.kwargs.get("capture_output", True) is False


def _terraform_calls(fake_run, verb: str) -> list:
    return [c for c in fake_run.call_args_list if c.args[0][2] == verb]


@pytest.mark.parametrize("provider", ["manual", "maas"])
@patch("sandbed.terraform.run")
def test_apply_streams_every_init_and_apply(fake_run, provider) -> None:
    fake_run.return_value = _out()
    terraform.apply(load_config(provider, "up", ssh_key="k"))
    inits = _terraform_calls(fake_run, "init")
    applies = _terraform_calls(fake_run, "apply")
    assert len(inits) == 2 and len(applies) == 2
    assert all(_streams(c) for c in inits + applies)


@patch("sandbed.terraform.run")
def test_apply_keeps_capturing_output_queries(fake_run) -> None:
    fake_run.return_value = _out()
    terraform.apply(load_config("manual", "up", ssh_key="k"))
    outputs = _terraform_calls(fake_run, "output")
    assert outputs and not any(_streams(c) for c in outputs)


@patch("sandbed.terraform.run")
def test_apply_maas_config_streams_init_and_apply(fake_run) -> None:
    fake_run.return_value = _out()
    terraform.apply_maas_config(load_config("maas", "up", ssh_key="k"))
    calls = fake_run.call_args_list
    assert [c.args[0][2] for c in calls] == ["init", "apply"]
    assert all(_streams(c) for c in calls)


@patch("sandbed.terraform.run")
def test_plan_streams_init_but_captures_the_plan(fake_run) -> None:
    fake_run.return_value = _out()
    terraform.plan(load_config("manual", "plan", ssh_key="k"), "manual")
    assert all(_streams(c) for c in _terraform_calls(fake_run, "init"))
    assert not any(_streams(c) for c in _terraform_calls(fake_run, "plan"))
