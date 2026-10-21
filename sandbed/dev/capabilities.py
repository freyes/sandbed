"""What this build of the development interface supports. Agents check this before provisioning;
the package version alone cannot say, so `interface_version` is bumped on any contract change."""

from __future__ import annotations

from importlib import metadata
from pathlib import Path

import click

from ..shell import run

INTERFACE_VERSION = 1
PROVIDERS = ["manual"]
PROFILES = ["single-node"]
SCENARIOS = [{"name": "bootstrap-and-launch", "version": 1}]


def command_paths(group: click.Group, prefix: str = "") -> list[str]:
    """Leaf command paths below `group`, derived from the click tree so it cannot drift."""
    paths: list[str] = []
    for name, cmd in sorted(group.commands.items()):
        if cmd.hidden:
            continue
        if isinstance(cmd, click.Group):
            paths += command_paths(cmd, f"{prefix}{name} ")
        else:
            paths.append(f"{prefix}{name}")
    return paths


def build_identity() -> dict:
    """sandbed version plus, in a source checkout, the git revision and dirty flag."""
    try:
        version = metadata.version("sandbed")
    except metadata.PackageNotFoundError:
        version = "unknown"
    identity: dict = {"version": version, "git_revision": None, "dirty": None}
    repo = Path(__file__).resolve().parents[2]
    try:
        head = run(["git", "-C", str(repo), "rev-parse", "HEAD"], check=False)
        if head.returncode == 0:
            identity["git_revision"] = head.stdout.strip()
            status = run(["git", "-C", str(repo), "status", "--porcelain"], check=False)
            identity["dirty"] = bool(status.stdout.strip()) if status.returncode == 0 else None
    except (FileNotFoundError, OSError):
        pass
    return identity


def capabilities(group: click.Group) -> dict:
    return {
        "interface_version": INTERFACE_VERSION,
        "commands": command_paths(group),
        "providers": PROVIDERS,
        "profiles": PROFILES,
        "scenarios": SCENARIOS,
        "sandbed": build_identity(),
    }
