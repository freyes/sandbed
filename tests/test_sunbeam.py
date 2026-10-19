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