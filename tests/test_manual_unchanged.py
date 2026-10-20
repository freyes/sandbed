"""Characterization tests: `manual` behaviour that the managed-MAAS work must not alter."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from sandbed import terraform
from sandbed.cli import PHASES, cli
from sandbed.config import load_config
from sandbed.hooks import _env
from sandbed.install import install
from sandbed.preflight import check
from sandbed.sunbeam import deploy_sunbeam

# Every Config field that exists today. Hooks export each as SDB_CFG_<NAME> (public interface),
# so none may disappear.
EXISTING_CONFIG_FIELDS = [
    "provider", "action", "mgmt_cidr", "external_cidr", "generic_cidr", "vcpus", "memory", "disk",
    "obs_count", "landscape_count", "ubuntu_image_remote", "ubuntu_image_alias", "ceph_pool_name",
    "ceph_rbd_pool", "ceph_cluster_name", "ceph_user_name", "microceph_osd_size",
    "microceph_osd_img", "microceph_disk_size", "shared_dir", "maas_name", "maas_url", "maas_token",
    "maas_space", "lxd_https_port", "internal_api_range", "public_api_range", "floating_ip_range",
    "region", "db_topology", "project_network", "ssh_key", "ssh_host", "vm_user", "hooks_dir",
    "no_hooks", "cloud_init_templates_dir", "use_testflinger", "testflinger_series",
    "testflinger_queue", "testflinger_timeout", "testflinger_job", "testflinger_job_file",
    "testflinger_key", "terraform_dir", "state_file",
]

MANUAL_INSTANCE_VARS = {
    "ceph_pool_name", "cloud_init_templates_dir", "disk", "external_cidr", "external_network_name",
    "image_fingerprint", "memory", "mgmt_network_name", "sdb_size", "shared_dir", "ssh_key",
    "vcpus", "vm_count", "vm_user",
}


def _out(stdout: str = "") -> MagicMock:
    m = MagicMock()
    m.stdout = stdout
    m.returncode = 0
    return m


def _var_names(cmd: list[str]) -> set[str]:
    return {a.removeprefix("-var=").split("=", 1)[0] for a in cmd if a.startswith("-var=")}


@patch("sandbed.terraform.run")
def test_manual_apply_runs_network_then_manual(fake_run) -> None:
    fake_run.return_value = _out()
    terraform.apply(load_config("manual", "up", ssh_key="k"))
    cmds = [c.args[0] for c in fake_run.call_args_list]
    heads = [c[1:4] for c in cmds]
    assert heads == [
        ["-chdir=terraform/network", "init", "-backend=false"],
        ["-chdir=terraform/network", "apply", "-auto-approve"],
        ["-chdir=terraform/manual", "init", "-backend=false"],
        ["-chdir=terraform/network", "output", "-json"],
        ["-chdir=terraform/manual", "apply", "-auto-approve"],
    ]
    assert _var_names(cmds[-1]) == MANUAL_INSTANCE_VARS
    # manual never passes a custom environment to terraform
    assert all("env" not in c.kwargs for c in fake_run.call_args_list)


@patch("sandbed.terraform.run")
def test_manual_plan_targets_only_the_manual_module(fake_run) -> None:
    fake_run.return_value = _out()
    terraform.plan(load_config("manual", "plan", ssh_key="k"), "manual")
    cmds = [c.args[0] for c in fake_run.call_args_list]
    assert {c[1] for c in cmds} == {"-chdir=terraform/manual", "-chdir=terraform/network"}
    assert [c[2] for c in cmds if c[1] == "-chdir=terraform/manual"] == ["init", "plan"]


@patch("sandbed.preflight.os.path.exists", return_value=True)
@patch("sandbed.preflight.run")
def test_manual_preflight_checks_lxd_and_microceph_only(fake_run, _exists) -> None:
    fake_run.return_value = _out()
    check(load_config("manual", "up"))
    assert [c.args[0] for c in fake_run.call_args_list] == [
        ["lxc", "query", "/1.0"],
        ["sudo", "microceph", "cluster", "list"],
    ]


@patch("sandbed.install.verify_ceph")
@patch("sandbed.install._installed", return_value=False)
@patch("sandbed.install.run")
def test_manual_install_never_touches_maas(fake_run, _installed, _verify) -> None:
    fake_run.return_value = _out("/dev/loop9")
    install(load_config("manual", "up"))
    cmds = [c.args[0] for c in fake_run.call_args_list]
    assert ["sudo", "snap", "install", "openstack"] in cmds
    assert not any("maas" in part for cmd in cmds for part in cmd)


@patch("sandbed.sunbeam.render", return_value="MANIFEST")
@patch("sandbed.sunbeam.run")
def test_manual_sunbeam_commands(fake_run, _render) -> None:
    ctx = load_config("manual", "sunbeam")
    ctx.ssh_host = "10.0.0.5"
    deploy_sunbeam(ctx)
    assert [c.args[0] for c in fake_run.call_args_list] == [
        ["ssh", "ubuntu@10.0.0.5", "sudo snap install openstack"],
        ["ssh", "ubuntu@10.0.0.5", "sunbeam prepare-node-script --bootstrap | bash"],
        [
            "ssh",
            "ubuntu@10.0.0.5",
            "sunbeam cluster bootstrap --role control,compute,storage --manifest MANIFEST",
        ],
        ["ssh", "ubuntu@10.0.0.5", "sunbeam configure --accept-defaults --openrc"],
    ]


def test_manual_hook_env_keeps_every_existing_key() -> None:
    ctx = load_config("manual", "up")
    env = _env(ctx, "terraform", "terraform.pre")
    for field in EXISTING_CONFIG_FIELDS:
        assert f"SDB_CFG_{field.upper()}" in env, field
    assert env["SDB_PROVIDER"] == "manual"
    # the maas deployment name only gets a default for the maas provider
    assert env["SDB_CFG_MAAS_NAME"] == ""
    assert env["SDB_CFG_MAAS_URL"] == ""
    assert env["SDB_CFG_MAAS_TOKEN"] == ""


def test_phase_list_is_stable() -> None:
    assert PHASES == ["preflight", "install-tools", "terraform", "maas-setup", "sunbeam"]


def test_manual_up_runs_every_phase_and_never_resolves_maas(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    with (
        patch("sandbed.cli._dispatch") as dispatch,
        patch("sandbed.terraform.resolve_maas", create=True) as resolve,
    ):
        result = CliRunner().invoke(cli, ["sunbeam", "manual", "up", "--no-hooks"])
    assert result.exit_code == 0, result.output
    assert [c.args[1] for c in dispatch.call_args_list] == PHASES
    assert "done maas-setup" in result.output
    resolve.assert_not_called()
