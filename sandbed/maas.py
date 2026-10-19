from __future__ import annotations

import os

from .config import Config
from .shell import run

CORE_INSTANCES = [
    "cloud-1", "cloud-2", "cloud-3",
    "sunbeam-controller-1", "sunbeam-controller-2", "sunbeam-controller-3",
    "juju-controller-1", "juju-controller-2", "juju-controller-3",
    "sunbeam-client-1",
]


def _ensure_maas_machine(profile: str, name: str, ctx: Config) -> None:
    run([
        "maas", profile, "machines", "create",
        "power_type=lxd",
        f"power_parameters_power_address={ctx.maas_url.replace('http', 'https')}",
        f"power_parameters_instance_name={name}",
        "power_parameters_project=default",
        f"hostname={name}",
    ])


def _tag(ctx: Config, name: str, tags: list[str]) -> None:
    profile = f"{ctx.maas_name}:{ctx.maas_url}"
    run(["maas", profile, "machine", name, "update", f"tags={','.join(tags)}"])


def setup_maas(ctx: Config) -> None:
    profile = f"{ctx.maas_name}:{ctx.maas_url}"

    # Expose LXD over HTTPS so the external MAAS can power-drive the VMs.
    run(["lxc", "config", "set", f"core.https_address=:{ctx.lxd_https_port}"])
    trust_token = os.environ.get("LXD_TRUST_TOKEN", "")
    if trust_token:
        run(["lxc", "config", "trust", "add", trust_token])

    for name in CORE_INSTANCES:
        _ensure_maas_machine(profile, name, ctx)

    # Reserved IP ranges.
    run(["maas", profile, "ipranges", "create", ctx.maas_space, "start", "end",
         "comment", f"{ctx.maas_name}-internal-api"])
    run(["maas", profile, "ipranges", "create", ctx.maas_space, "start", "end",
         "comment", f"{ctx.maas_name}-public-api"])

    # Role/storage/network tags (exact per-node map finalized in docs/maas-provider.md).
    _tag(ctx, "cloud-1", ["openstack-cloud-1", "compute", "storage", "sunbeam", "control"])