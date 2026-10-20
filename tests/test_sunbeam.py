from unittest.mock import patch

from sandbed.config import load_config
from sandbed.sunbeam import deploy_sunbeam


@patch("sandbed.sunbeam.render", return_value="---\nregion: RegionOne\n")
@patch("sandbed.sunbeam.run")
def test_manual_flow_bootstraps(fake_run, fake_render) -> None:
    ctx = load_config("manual", "sunbeam")
    ctx.ssh_host = "192.168.200.10"
    deploy_sunbeam(ctx)
    all_cmds = [" ".join(c[0][0]) for c in fake_run.call_args_list]
    assert any("cluster bootstrap" in c for c in all_cmds)
    assert any("prepare-node-script --bootstrap" in c for c in all_cmds)

@patch("sandbed.sunbeam.render", return_value="MANIFEST")
@patch("sandbed.sunbeam.run")
def test_maas_flow_uses_resolved_connection_and_series(fake_run, fake_render) -> None:
    ctx = load_config("maas", "sunbeam", maas_deploy_series="jammy")
    ctx.ssh_host = "192.168.200.7"
    ctx.maas_url = "http://172.16.1.2:5240/MAAS"
    ctx.maas_token = "ck:tk:sk"
    deploy_sunbeam(ctx)
    cmds = [c.args[0][-1] for c in fake_run.call_args_list]
    assert "sunbeam deployment add maas sandbed ck:tk:sk http://172.16.1.2:5240/MAAS" in cmds
    render_ctx = fake_render.call_args.args[1]
    assert render_ctx["deploy_series"] == "jammy"
    assert render_ctx["maas_url"] == "http://172.16.1.2:5240/MAAS"


@patch("sandbed.sunbeam.render", return_value="MANIFEST")
@patch("sandbed.sunbeam.run")
def test_ssh_uses_the_dedicated_identity_when_sandbed_owns_the_key(fake_run, _render) -> None:
    ctx = load_config("manual", "sunbeam")
    ctx.ssh_host = "10.0.0.5"
    ctx.ssh_identity = "/work/.sandbed_ed25519"
    deploy_sunbeam(ctx)
    first = fake_run.call_args_list[0].args[0]
    assert first[:3] == ["ssh", "-i", "/work/.sandbed_ed25519"]
    assert "IdentitiesOnly=yes" in first
    assert "StrictHostKeyChecking=accept-new" in first
    assert first[-2:] == ["ubuntu@10.0.0.5", "sudo snap install openstack"]
    calls = fake_run.call_args_list
    assert all(c.args[0][:3] == ["ssh", "-i", "/work/.sandbed_ed25519"] for c in calls)
