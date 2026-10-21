"""CIDRs already in use on the host, so a new lab never overlaps a route or an LXD network."""

from __future__ import annotations

import json

from ..shell import run


def taken_cidrs() -> list[str]:
    taken: list[str] = []
    routes = run(["ip", "-j", "route"], check=False)
    if routes.returncode == 0 and routes.stdout.strip():
        taken += [r["dst"] for r in json.loads(routes.stdout) if "/" in r.get("dst", "")]
    nets = run(["lxc", "network", "list", "--format", "json"], check=False)
    if nets.returncode == 0 and nets.stdout.strip():
        taken += [
            n["config"]["ipv4.address"] for n in json.loads(nets.stdout)
            if n.get("config", {}).get("ipv4.address", "none") not in ("none", "auto")
        ]
    return taken
