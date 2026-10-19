from __future__ import annotations

import json
import os
import subprocess

from rich.text import Text

from .config import Config
from .console import console
from .shell import run

NETWORK_VARS = (
    "mgmt_name", "mgmt_cidr", "ceph_pool_name", "ceph_rbd_pool",
    "ceph_cluster_name", "ceph_user_name", "ubuntu_image_remote", "ubuntu_image_alias",
)

INSTANCE_VARS = {
    "manual": (
        "vm_count", "vcpus", "memory", "disk", "sdb_size", "mgmt_network_name",
        "ceph_pool_name", "image_fingerprint", "shared_dir", "ssh_key", "vm_user",
        "external_network_name", "external_cidr", "cloud_init_templates_dir",
    ),
    "maas": (
        "vcpus", "memory", "disk", "sdb_size", "obs_count", "landscape_count",
        "mgmt_network_name", "ceph_pool_name", "image_fingerprint", "shared_dir",
        "ssh_key", "vm_user", "generic_network_name", "generic_cidr",
        "external_network_name", "external_cidr", "generic_parent_bridge",
        "cloud_init_templates_dir",
    ),
}

_DEFAULTS = {
    "mgmt_name": "mgmt",
    "mgmt_network_name": "mgmt",
    "external_network_name": "external",
    "generic_network_name": "generic",
    "generic_parent_bridge": "br0",
    "vm_count": "3",
    "ceph_rbd_pool": "rbd",
    "ceph_cluster_name": "ceph",
    "ceph_user_name": "admin",
    "ubuntu_image_remote": "ubuntu",
    "ubuntu_image_alias": "26.04",
}


def terraform_vars(ctx: Config) -> dict:
    return {
        "mgmt_cidr": ctx.mgmt_cidr,
        "external_cidr": ctx.external_cidr,
        "generic_cidr": ctx.generic_cidr,
        "vcpus": str(ctx.vcpus),
        "memory": str(ctx.memory),
        "disk": str(ctx.disk),
        "sdb_size": "20GiB",
        "ceph_pool_name": ctx.ceph_pool_name,
        "ceph_rbd_pool": ctx.ceph_rbd_pool,
        "shared_dir": ctx.shared_dir,
        "ssh_key": ctx.ssh_key,
        "vm_user": ctx.vm_user,
        "obs_count": str(ctx.obs_count),
        "landscape_count": str(ctx.landscape_count),
        "cloud_init_templates_dir": os.path.abspath(ctx.cloud_init_templates_dir),
    }


def _vars_for(ctx: Config, module: str, extra: dict | None = None) -> dict:
    names = NETWORK_VARS if module == "network" else INSTANCE_VARS[module]
    merged = dict(_DEFAULTS)
    merged.update(terraform_vars(ctx))
    merged.update(extra or {})
    return {k: merged[k] for k in names if k in merged}


def _network_outputs(ctx: Config) -> dict:
    result = run(
        ["terraform", f"-chdir={ctx.terraform_dir}/network", "output", "-json"],
        check=False,
    )
    if result.returncode != 0:
        return {}
    try:
        raw = json.loads(result.stdout)
        return {k: str(v["value"]) for k, v in raw.items()}
    except (json.JSONDecodeError, KeyError, TypeError):
        return {}


def _instance_extra(ctx: Config) -> dict:
    outputs = _network_outputs(ctx)
    return {
        "mgmt_network_name": outputs.get("mgmt_network_name", "mgmt"),
        "ceph_pool_name": outputs.get("ceph_pool_name", ctx.ceph_pool_name),
        "image_fingerprint": outputs.get("ubuntu_image_fingerprint", ""),
    }


def _tf(
    ctx: Config,
    module: str,
    args: list[str],
    vars_map: dict | None = None,
    capture_output: bool = True,
) -> subprocess.CompletedProcess | None:
    cmd = ["terraform", f"-chdir={ctx.terraform_dir}/{module}", *args]
    for key, value in sorted((vars_map or {}).items()):
        cmd += [f"-var={key}={value}"]
    return run(cmd, capture_output=capture_output)


def apply(ctx: Config) -> None:
    _tf(ctx, "network", ["init", "-backend=false"])
    _tf(ctx, "network", ["apply", "-auto-approve"], _vars_for(ctx, "network"))
    if ctx.provider in ("manual", "maas"):
        p = _tf(ctx, ctx.provider, ["init", "-backend=false"])
        console.print(f"[bold]Terraform init for {ctx.provider}[/bold]")
        console.print(Text.from_ansi(p.stdout))
        _tf(
            ctx,
            ctx.provider,
            ["apply", "-auto-approve"],
            _vars_for(ctx, ctx.provider, _instance_extra(ctx)),
            capture_output=False,
        )


def plan(ctx: Config, name: str) -> None:
    _tf(ctx, name, ["init", "-backend=false"])
    if name == "network":
        p = _tf(ctx, name, ["plan"], _vars_for(ctx, "network"))
    else:
        p = _tf(ctx, name, ["plan"], _vars_for(ctx, name, _instance_extra(ctx)))

    console.print(f"[bold]Terraform plan for {name}[/bold]")
    console.print(Text.from_ansi(p.stdout))

def destroy_terraform(ctx: Config) -> None:
    if ctx.provider in ("manual", "maas"):
        _tf(ctx, ctx.provider, ["destroy", "-auto-approve"], _vars_for(ctx, ctx.provider))
    _tf(ctx, "network", ["destroy", "-auto-approve"], _vars_for(ctx, "network"))
