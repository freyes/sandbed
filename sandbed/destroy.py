from __future__ import annotations

from .config import Config
from .sunbeam import sunbeam_destroy
from .terraform import destroy_terraform, resolve_maas


def destroy(ctx: Config) -> None:
    print("WARNING: sunbeam teardown must precede VM deletion.")
    if ctx.provider == "maas":
        resolve_maas(ctx, required=False)  # finds the client VM to run the teardown on
    try:
        sunbeam_destroy(ctx)
    except Exception:
        print("sunbeam teardown failed; continuing to terraform destroy")
    destroy_terraform(ctx)