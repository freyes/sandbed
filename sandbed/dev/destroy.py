"""Tear a lab down without touching anything it does not own."""

from __future__ import annotations

import json

from ..shell import run
from .context import LabContext
from .errors import HostError, ProvisioningError
from .labs import Registry
from .provision import TF_ROOTS, terraform
from .record import LabRecord
from .runner import run_logged
from .stages import stage

GUEST_TEARDOWN_TIMEOUT = 20 * 60


def _lxc_json(path: str):
    """Parsed `lxc query` result, or None when LXD says the object does not exist."""
    result = run(["lxc", "query", path], check=False)
    if result.returncode == 0:
        return json.loads(result.stdout or "null")
    if "not found" in (result.stderr or "").lower():
        return None
    raise HostError(f"cannot verify {path}: {(result.stderr or '').strip()}")


def owned_resources(record: LabRecord) -> list[str]:
    """What of this lab still exists. Only names recorded for the lab are looked up."""
    found = []
    if _lxc_json(f"/1.0/projects/{record.project}") is not None:
        found.append(f"project:{record.project}")
    networks = _lxc_json("/1.0/networks") or []
    if any(n.rsplit("/", 1)[-1] == f"{record.project}-m" for n in networks):
        found.append(f"network:{record.project}-m")
    return found


def _guest_teardown(ctx: LabContext, run_id: str) -> str | None:
    """Bounded Sunbeam teardown. Failure is evidence, never a reason to keep the VM."""
    if not (ctx.record.installed_artifact_id and ctx.record.vm_address):
        return None
    result = run_logged(
        ctx.target().ssh_argv(["sunbeam", "cluster", "destroy", "--yes"]),
        log_path=ctx.paths.run(run_id) / "commands" / "sunbeam-destroy.log",
        timeout=GUEST_TEARDOWN_TIMEOUT,
    )
    return None if result.ok else str(result.log_path)


def destroy(ctx: LabContext, run_id: str, registry: Registry) -> dict:
    rec = ctx.record
    evidence: list[str] = []
    with stage(ctx.log, run_id, "destroy", layer=ProvisioningError):
        try:
            note = _guest_teardown(ctx, run_id)
        except Exception as err:  # unreachable VM etc.: carry on to Terraform
            note = f"guest teardown skipped: {err}"
        if note:
            evidence.append(note)
        for root in reversed(TF_ROOTS):
            directory = ctx.paths.terraform(root)
            if (directory / "terraform.tfstate").exists():
                terraform(ctx, run_id, root, "destroy", "-auto-approve", "-input=false",
                          "-lock-timeout=60s")
        remaining = owned_resources(rec)
        if remaining:
            rec.provisioning_state = "cleanup_failed"
            ctx.store.save(rec)
            with registry.locked():
                registry.set_state(rec.lab_uuid, "cleanup_failed")
            raise ProvisioningError(
                f"resources remain after destroy: {remaining}", evidence=tuple(evidence)
            )
        rec.provisioning_state = "destroyed"
        ctx.store.save(rec)
        with registry.locked():
            registry.set_state(rec.lab_uuid, "destroyed")
    return {"destroyed": True, "evidence": evidence}
