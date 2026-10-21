"""Create the lab's LXD project, network and VM with per-lab copies of the dev Terraform roots."""

from __future__ import annotations

import json
import shutil
import time
from importlib import resources
from pathlib import Path

from ..shell import run
from ..sshkey import ensure_key
from .context import LabContext
from .errors import DeploymentError, HostError, Interrupted, ProvisioningError
from .labs import STORAGE_POOL
from .record import LabRecord
from .runner import run_logged
from .stages import GUEST_DIR, stage
from .store import atomic_write_json, ensure_private_dir

TF_ROOTS = ("dev-network", "dev-manual")
GUEST_SCRIPTS = ("run-stage.sh", "probe-guest.sh", "os.sh")
PROVISION_TIMEOUT = 20 * 60
READY_TIMEOUT = 10 * 60


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def prepare_working_copies(ctx: LabContext) -> None:
    """Each lab gets its own copy of the roots, so its state, lock file and provider cache are
    its own. Existing copies (a resumed provision) are kept: they hold the state."""
    for name in TF_ROOTS:
        source = repo_root() / "terraform" / name
        if not source.is_dir():
            raise HostError(f"missing {source}; `sandbed dev` needs a source checkout")
        target = ctx.paths.terraform(name)
        if not target.exists():
            ensure_private_dir(target.parent)
            shutil.copytree(source, target, ignore=shutil.ignore_patterns(
                ".terraform", ".terraform.lock.hcl", "*.tfstate", "*.tfstate.*"))


def write_vars(ctx: LabContext, root: str, values: dict) -> None:
    atomic_write_json(ctx.paths.terraform(root) / "terraform.tfvars.json", values)


def terraform(ctx: LabContext, run_id: str, root: str, *args: str) -> None:
    run_logged(
        ["terraform", f"-chdir={ctx.paths.terraform(root)}", *args],
        log_path=ctx.paths.run(run_id) / "commands" / f"terraform-{root}-{args[0]}.log",
        timeout=PROVISION_TIMEOUT, check=True,
    )


def terraform_outputs(ctx: LabContext, root: str) -> dict:
    out = run(["terraform", f"-chdir={ctx.paths.terraform(root)}", "output", "-json"])
    return {k: v["value"] for k, v in json.loads(out.stdout).items()}


def instance_info(record: LabRecord) -> tuple[str, str]:
    """(volatile.uuid, first global IPv4) of the lab VM, straight from LXD."""
    query = f"/1.0/instances/{record.vm_name}?project={record.project}&recursion=1"
    data = json.loads(run(["lxc", "query", query]).stdout)
    address = next(
        (a["address"] for nic in (data.get("state") or {}).get("network", {}).values()
         for a in nic.get("addresses", []) if a["family"] == "inet" and a["scope"] == "global"),
        None,
    )
    if not address:
        raise ProvisioningError("the VM reports no global IPv4 address yet")
    return data["config"]["volatile.uuid"], address


def verify_identity(record: LabRecord) -> None:
    """Every command talks only to the VM Terraform created: same LXD UUID and address."""
    uuid, address = instance_info(record)
    if (uuid, address) != (record.vm_uuid, record.vm_address):
        raise DeploymentError(
            f"VM identity changed: expected {record.vm_uuid}@{record.vm_address}, "
            f"found {uuid}@{address}"
        )


def _ssh_ok(ctx: LabContext, run_id: str, remote: list[str], timeout: float) -> bool:
    log = ctx.paths.run(run_id) / "commands" / "wait-ready.log"
    return run_logged(
        ctx.target(first_contact=True).ssh_argv(remote), log_path=log, timeout=timeout
    ).ok


def wait_ready(ctx: LabContext, run_id: str, timeout: float = READY_TIMEOUT) -> None:
    """SSH up, cloud-init finished cleanly, /dev/kvm and /dev/sdb present."""
    deadline = time.monotonic() + timeout
    while not _ssh_ok(ctx, run_id, ["true"], 30):
        if time.monotonic() > deadline:
            raise ProvisioningError("ssh did not become available", stage="provision")
        time.sleep(5)
    checks = (
        ["cloud-init", "status", "--wait"], ["test", "-c", "/dev/kvm"], ["test", "-b", "/dev/sdb"],
    )
    for remote in checks:
        if not _ssh_ok(ctx, run_id, remote, max(deadline - time.monotonic(), 30)):
            raise ProvisioningError(f"guest check failed: {' '.join(remote)}", stage="provision")


def push_guest_scripts(ctx: LabContext, run_id: str) -> None:
    target = ctx.target()
    log = ctx.paths.run(run_id) / "commands" / "push-scripts.log"
    run_logged(target.ssh_argv(["mkdir", "-p", f"{ctx.record.home}/{GUEST_DIR}"]),
               log_path=log, timeout=60, check=True)
    for name in GUEST_SCRIPTS:
        script = Path(str(resources.files("sandbed.dev").joinpath(f"data/{name}")))
        run_logged(target.scp_argv(script, f"{ctx.record.home}/{GUEST_DIR}/{name}"),
                   log_path=log, timeout=60, check=True)


def provision(ctx: LabContext, run_id: str) -> LabRecord:
    rec, cfg = ctx.record, ctx.record.config
    if rec.provisioning_state == "ready":
        verify_identity(rec)
        return rec
    rec.provisioning_state = "provisioning"
    ctx.store.save(rec)
    try:
        with stage(ctx.log, run_id, "provision", layer=ProvisioningError):
            ensure_private_dir(ctx.paths.ssh_dir)
            public_key = ensure_key(str(ctx.paths.ssh_key))
            prepare_working_copies(ctx)
            write_vars(ctx, "dev-network", {
                "project": rec.project, "mgmt_cidr": rec.mgmt_cidr,
                "ubuntu_image_alias": cfg["image_alias"],
            })
            for step in (("init", "-backend=false", "-input=false"),
                         ("apply", "-auto-approve", "-input=false", "-lock-timeout=60s")):
                terraform(ctx, run_id, "dev-network", *step)
            net = terraform_outputs(ctx, "dev-network")
            write_vars(ctx, "dev-manual", {
                "project": rec.project, "storage_pool": STORAGE_POOL,
                "mgmt_network_name": net["mgmt_network_name"],
                "image_fingerprint": net["ubuntu_image_fingerprint"],
                "vcpus": cfg["vcpus"], "memory": cfg["memory"], "disk": cfg["disk"],
                "sdb_size": cfg["sdb_size"], "ssh_key": public_key, "vm_user": rec.vm_user,
                "vm_name": rec.vm_name,
            })
            for step in (("init", "-backend=false", "-input=false"),
                         ("apply", "-auto-approve", "-input=false", "-lock-timeout=60s")):
                terraform(ctx, run_id, "dev-manual", *step)
            rec.vm_uuid, rec.vm_address = instance_info(rec)
            rec.image_fingerprint = net["ubuntu_image_fingerprint"]
            rec.terraform_version = run(["terraform", "version", "-json"]).stdout.strip()
            ctx.store.save(rec)
            wait_ready(ctx, run_id)
            push_guest_scripts(ctx, run_id)
            rec.provisioning_state = "ready"
            ctx.store.save(rec)
    except Interrupted:
        raise  # leave state `provisioning`; the next `create` reconciles and resumes
    except BaseException:
        rec.provisioning_state = "failed"
        ctx.store.save(rec)
        raise
    return rec