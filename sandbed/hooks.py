from __future__ import annotations

import os
import subprocess
from dataclasses import fields
from pathlib import Path

from .config import Config

LOG_PATH = "/tmp/sandbed.log"


def find_hooks(hooks_dir: str) -> list[str]:
    path = Path(hooks_dir)
    if not path.is_dir():
        return []
    return sorted(p.name for p in path.iterdir() if p.is_file() and os.access(p, os.X_OK))


def _env(ctx: Config, phase: str, name: str) -> dict:
    env = os.environ.copy()
    env["SDB_PROVIDER"] = ctx.provider
    env["SDB_ACTION"] = ctx.action
    env["SDB_PHASE"] = phase
    env["SDB_HOOK"] = name
    for f in fields(ctx):
        env[f"SDB_CFG_{f.name.upper()}"] = str(getattr(ctx, f.name))
    return env


def run_hook(name: str, ctx: Config, phase: str = "", feed_stderr: bool = False) -> None:
    if ctx.no_hooks:
        return
    if name not in find_hooks(ctx.hooks_dir):
        return

    hook_path = Path(ctx.hooks_dir) / name
    env = _env(ctx, phase, name)

    stdin_data = None
    if feed_stderr and os.path.exists(LOG_PATH):
        stdin_data = Path(LOG_PATH).read_text()

    proc = subprocess.run(
        [str(hook_path)], cwd=os.getcwd(), env=env,
        input=stdin_data, capture_output=True, text=True,
    )

    with open(LOG_PATH, "a") as fh:
        fh.write(f"$ {name} (exit {proc.returncode})\n")
        if proc.stdout:
            fh.write(proc.stdout)
        if proc.stderr:
            fh.write(proc.stderr)

    if proc.returncode != 0:
        raise RuntimeError(f"hook '{name}' failed (exit {proc.returncode}); aborting")