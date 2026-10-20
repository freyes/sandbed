from __future__ import annotations

from . import terraform
from .config import Config
from .console import console


def setup_maas(ctx: Config) -> None:
    """Configure the freshly deployed MAAS (images, network, machines, tags) via terraform."""
    terraform.resolve_maas(ctx)
    terraform.apply_maas_config(ctx)
    console.print(
        f"[bold]MAAS[/bold] {ctx.maas_url}  user: admin  password: {ctx.maas_admin_password}"
    )
    if ctx.maas_admin_password == "changeme":
        console.print(
            "[yellow]warning: MAAS admin password is the default; "
            "set --maas-admin-password[/yellow]"
        )
