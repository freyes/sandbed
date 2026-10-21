"""CIDRs already in use on the host, so a new lab never overlaps a route or an LXD network."""

from __future__ import annotations

import json
import subprocess

from .errors import HostError

_IP_ROUTE = ["ip", "-j", "route"]
_LXC_NETWORKS = ["lxc", "network", "list", "--format", "json"]
_TIMEOUT = 30.0


def _run(argv: list[str], label: str) -> str:
    """Run a host query, failing closed on any error (non-zero exit, bad JSON, timeout,
    missing binary). Empty-but-successful output is fine (no routes / no LXD networks)."""
    try:
        result = subprocess.run(
            argv,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=_TIMEOUT,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError) as err:
        raise HostError(f"{label}: {err}") from err
    if result.returncode != 0:
        raise HostError(f"{label}: exit {result.returncode}: {(result.stderr or '').strip()}")
    return result.stdout


def taken_cidrs() -> list[str]:
    taken: list[str] = []
    routes_raw = _run(_IP_ROUTE, "ip -j route")
    if routes_raw.strip():
        try:
            routes = json.loads(routes_raw)
        except json.JSONDecodeError as err:
            raise HostError(f"ip -j route: unparsable JSON: {err}") from err
        taken += [r["dst"] for r in routes if "/" in r.get("dst", "")]
    nets_raw = _run(_LXC_NETWORKS, "lxc network list")
    if nets_raw.strip():
        try:
            nets = json.loads(nets_raw)
        except json.JSONDecodeError as err:
            raise HostError(f"lxc network list: unparsable JSON: {err}") from err
        taken += [
            n["config"]["ipv4.address"] for n in nets
            if n.get("config", {}).get("ipv4.address", "none") not in ("none", "auto")
        ]
    return taken
