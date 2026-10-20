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
        "cloud_init_templates_dir", "maas_vcpus", "maas_memory", "maas_disk", "maas_channel",
        "upstream_dns", "lxd_https_port",
    ),
    "maas-config": (
        "deploy_series", "maas_name", "maas_space", "generic_cidr", "ssh_key", "upstream_dns",
        "image_sync_timeout",
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
        "maas_vcpus": str(ctx.maas_vcpus),
        "maas_memory": str(ctx.maas_memory),
        "maas_disk": str(ctx.maas_disk),
        "maas_channel": ctx.maas_channel,
        "upstream_dns": ctx.maas_upstream_dns,
        "lxd_https_port": str(ctx.lxd_https_port),
        "deploy_series": ctx.maas_deploy_series,
        "maas_name": ctx.maas_name,
        "maas_space": ctx.maas_space,
        "image_sync_timeout": ctx.maas_image_sync_timeout,
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


def _maas_env(ctx: Config) -> dict:
    # The admin password reaches terraform through the environment, not `-var`, so it never
    # shows up in `ps`.
    return {**os.environ, "TF_VAR_maas_admin_password": ctx.maas_admin_password}


def _env_for(ctx: Config) -> dict | None:
    return _maas_env(ctx) if ctx.provider == "maas" else None


def _tf(
    ctx: Config,
    module: str,
    args: list[str],
    vars_map: dict | None = None,
    capture_output: bool = True,
    env: dict | None = None,
) -> subprocess.CompletedProcess | None:
    cmd = ["terraform", f"-chdir={ctx.terraform_dir}/{module}", *args]
    for key, value in sorted((vars_map or {}).items()):
        cmd += [f"-var={key}={value}"]
    # Only pass `env` when set so provider-agnostic calls stay byte-identical.
    kwargs = {"env": env} if env is not None else {}
    return run(cmd, capture_output=capture_output, **kwargs)


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
            env=_env_for(ctx),
        )


def plan(ctx: Config, name: str) -> None:
    _tf(ctx, name, ["init", "-backend=false"])
    if name == "network":
        p = _tf(ctx, name, ["plan"], _vars_for(ctx, "network"))
    else:
        p = _tf(
            ctx, name, ["plan"], _vars_for(ctx, name, _instance_extra(ctx)), env=_env_for(ctx)
        )

    console.print(f"[bold]Terraform plan for {name}[/bold]")
    console.print(Text.from_ansi(p.stdout))


def module_outputs(ctx: Config, module: str) -> dict:
    """Return `terraform output -json` of a module as {name: value}; empty if unavailable."""
    result = run(
        ["terraform", f"-chdir={ctx.terraform_dir}/{module}", "output", "-json"],
        check=False,
    )
    if result.returncode != 0:
        return {}
    try:
        return {k: v["value"] for k, v in json.loads(result.stdout).items()}
    except (json.JSONDecodeError, KeyError, TypeError):
        return {}


def resolve_maas(ctx: Config, required: bool = True) -> None:
    """Fill the MAAS connection details (and the client SSH host) from terraform outputs."""
    out = module_outputs(ctx, "maas")
    if "maas_url" not in out or "maas_api_key" not in out:
        if required:
            raise RuntimeError(
                "MAAS terraform outputs not found; run `sandbed sunbeam maas up` first"
            )
        return
    ctx.maas_url = out["maas_url"]
    ctx.maas_token = out["maas_api_key"]
    ctx.maas_admin_password = out.get("maas_admin_password", ctx.maas_admin_password)
    if not ctx.ssh_host and out.get("client_mgmt_ip"):
        ctx.ssh_host = out["client_mgmt_ip"]


def apply_maas_config(ctx: Config) -> None:
    _tf(ctx, "maas-config", ["init", "-backend=false"])
    args = ["apply", "-auto-approve"]
    if ctx.maas_parallelism:
        args.append(f"-parallelism={ctx.maas_parallelism}")
    _tf(ctx, "maas-config", args, _vars_for(ctx, "maas-config"), capture_output=False)


def _forget_maas_config_state(ctx: Config) -> None:
    # maas-config resources live inside the MAAS VM and disappear with it; destroying them
    # through the API after the VM is gone would fail, so only drop the local state.
    for name in ("terraform.tfstate", "terraform.tfstate.backup"):
        path = os.path.join(ctx.terraform_dir, "maas-config", name)
        if os.path.exists(path):
            os.remove(path)


def destroy_terraform(ctx: Config) -> None:
    if ctx.provider == "maas":
        _tf(
            ctx,
            "maas",
            ["destroy", "-auto-approve"],
            _vars_for(ctx, "maas", _instance_extra(ctx)),
            env=_maas_env(ctx),
        )
        _forget_maas_config_state(ctx)
    elif ctx.provider == "manual":
        _tf(ctx, "manual", ["destroy", "-auto-approve"], _vars_for(ctx, "manual"))
    _tf(ctx, "network", ["destroy", "-auto-approve"], _vars_for(ctx, "network"))
