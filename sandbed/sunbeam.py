from __future__ import annotations

from .config import Config
from .manifest import render
from .shell import run


def _ssh(target: str, user: str, script: str) -> None:
    run(["ssh", f"{user}@{target}", script])


def _snap(ctx: Config, ip: str) -> None:
    _ssh(ip, ctx.vm_user, "sudo snap install openstack")


def deploy_sunbeam(ctx: Config) -> None:
    manifest = render(
        f"manifests/{ctx.provider}.yaml.j2",
        {
            "region": ctx.region,
            "project_network": ctx.project_network,
            "db_topology": ctx.db_topology,
            "deployment_name": ctx.maas_name,
            "maas_url": ctx.maas_url,
            "maas_token": ctx.maas_token,
        },
    )

    node = ctx.ssh_host

    if ctx.provider == "manual":
        _snap(ctx, node)
        _ssh(node, ctx.vm_user, "sunbeam prepare-node-script --bootstrap | bash")
        _ssh(
            node,
            ctx.vm_user,
            f"sunbeam cluster bootstrap --role control,compute,storage --manifest {manifest}",
        )
        _ssh(node, ctx.vm_user, "sunbeam configure --accept-defaults --openrc")
    else:
        _snap(ctx, node)
        _ssh(node, ctx.vm_user, "sunbeam prepare-node-script --client | bash")
        _ssh(
            node,
            ctx.vm_user,
            f"sunbeam deployment add maas {ctx.maas_name} {ctx.maas_token} {ctx.maas_url}",
        )
        _ssh(node, ctx.vm_user, f"sunbeam deployment space map {ctx.maas_space}")
        _ssh(node, ctx.vm_user, "sunbeam deployment validate")
        _ssh(node, ctx.vm_user, f"sunbeam cluster bootstrap --manifest {manifest}")
        _ssh(node, ctx.vm_user, f"sunbeam cluster deploy --manifest {manifest}")
        _ssh(node, ctx.vm_user, "sunbeam configure --accept-defaults --openrc")


def sunbeam_destroy(ctx: Config) -> None:
    _ssh(ctx.ssh_host, ctx.vm_user, "sunbeam cluster destroy --yes")