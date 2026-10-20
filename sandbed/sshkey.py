from __future__ import annotations

import os
from pathlib import Path

from .config import Config
from .console import console
from .shell import run


def ensure_key(path: str) -> str:
    """Return the public key for `path`, creating a password-less ed25519 key if none exists."""
    private = Path(path)
    if not private.exists():
        private.parent.mkdir(parents=True, exist_ok=True)
        run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", "sandbed", "-f", str(private)])
        console.print(f"created ssh key [bold]{private}[/bold]")
    public = Path(f"{path}.pub")
    if public.exists():
        return public.read_text().strip()
    return run(["ssh-keygen", "-y", "-f", str(private)]).stdout.strip()


def prepare(ctx: Config) -> None:
    """Use the dedicated key unless the user gave `--ssh-key` / SUNBEAM_SSH_KEY.

    Sets `ctx.ssh_key` (authorized on every node) and `ctx.ssh_identity` (the private key
    sandbed itself ssh-es with).
    """
    if ctx.ssh_key:
        return
    path = os.path.abspath(ctx.ssh_key_file)
    ctx.ssh_key = ensure_key(path)
    ctx.ssh_identity = path
