from __future__ import annotations

from .config import Config
from .sunbeam import sunbeam_destroy
from .terraform import destroy_terraform


def destroy(ctx: Config) -> None:
    print("WARNING: sunbeam teardown must precede VM deletion.")
    try:
        sunbeam_destroy(ctx)
    except Exception:
        print("sunbeam teardown failed; continuing to terraform destroy")
    destroy_terraform(ctx)