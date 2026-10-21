"""Render the single-node development manifest to a *file* (the legacy path interpolates the
YAML text after `--manifest`, which is not a path)."""

from __future__ import annotations

import ipaddress
from importlib import resources
from pathlib import Path

from jinja2 import Environment, StrictUndefined

from .errors import InputError


def render_dev_manifest(*, region: str, mgmt_cidr: str, floating_cidr: str) -> str:
    """Gateway is the first host and the allocation range the rest, as Sunbeam would default."""
    mgmt, floating = ipaddress.ip_network(mgmt_cidr), ipaddress.ip_network(floating_cidr)
    if mgmt.overlaps(floating):
        raise InputError(f"floating network {floating} overlaps the lab network {mgmt}")
    hosts = list(floating.hosts())
    source = resources.files("sandbed.dev").joinpath("data/manifest.yaml.j2").read_text()
    env = Environment(autoescape=False, undefined=StrictUndefined, keep_trailing_newline=True)
    return env.from_string(source).render(
        region=region, mgmt_cidr=mgmt_cidr, floating_cidr=str(floating),
        floating_gateway=str(hosts[0]), floating_start=str(hosts[1]), floating_end=str(hosts[-1]),
    )


def write_dev_manifest(
    path: Path, *, region: str, mgmt_cidr: str, floating_cidr: str
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_dev_manifest(
        region=region, mgmt_cidr=mgmt_cidr, floating_cidr=floating_cidr))
    path.chmod(0o600)
    return path
