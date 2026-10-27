"""Install exactly the chosen artifact in the lab VM and prove what was installed."""

from __future__ import annotations

import re
from pathlib import Path

from .adapter import RevisionAdapter, SnapMeta, missing_connections, select_adapter
from .build import load_artifact
from .context import LabContext
from .errors import DeploymentError, InputError
from .manifest import write_dev_manifest
from .provision import verify_identity
from .runner import run_logged
from .stages import GUEST_DIR, run_guest, stage

TRANSFER_TIMEOUT = 10 * 60
INSTALL_TIMEOUT = 30 * 60
WAIT_JUJU = (
    'for i in $(seq 1 60); do snap list juju >/dev/null 2>&1 && exit 0; sleep 5; done; exit 1'
)
PREPARE_NODE = "sunbeam prepare-node-script --bootstrap | bash -x"


def snap_version(snap_list_output: str) -> tuple[str, str]:
    """(version, revision) columns of `snap list openstack`."""
    rows = [ln.split() for ln in snap_list_output.splitlines() if ln.strip()]
    for row in rows[1:]:
        if row[0] == "openstack" and len(row) >= 3 and re.fullmatch(r"x?\d+", row[2]):
            return row[1], row[2]
    raise DeploymentError("openstack is not installed in the guest")


def first_hash(output: str) -> str:
    """First sha256 in `sha256sum` output."""
    for token in output.split():
        if len(token) == 64 and all(c in "0123456789abcdef" for c in token):
            return token
    raise DeploymentError(f"no sha256 in guest output: {output.strip()[:200]!r}")


def _guest(ctx: LabContext, run_id: str, attempt, argv: list[str], timeout: float, name: str):
    return run_guest(
        ctx.target(), attempt, argv, home=ctx.record.home, timeout=timeout,
        log_path=ctx.paths.run(run_id) / "commands" / f"{name}.log",
    )


def _meta(provenance: dict) -> SnapMeta:
    snap = provenance["snap"]
    return SnapMeta(snap["name"], snap["version"], snap["base"], tuple(snap["architectures"]))


def verify_installed(
    ctx: LabContext, run_id: str, attempt, provenance: dict, adapter: RevisionAdapter
) -> dict:
    """Tie the running installation to the registered artifact. Fails on any mismatch."""
    version, revision = snap_version(_guest(
        ctx, run_id, attempt, ["snap", "list", "openstack"], 120, "verify-snap-list").tail)
    if version != provenance["snap"]["version"]:
        raise DeploymentError(
            f"installed version {version} != built {provenance['snap']['version']}"
        )
    # The *active* revision's file: older revisions may still be on disk.
    digest = first_hash(_guest(
        ctx, run_id, attempt,
        ["sudo", "sha256sum", f"/var/lib/snapd/snaps/openstack_{revision}.snap"],
        120, "verify-installed-sha",
    ).tail)
    if digest != provenance["snap"]["sha256"]:
        raise DeploymentError("installed snap file does not match the artifact checksum")
    connections = _guest(ctx, run_id, attempt, ["snap", "connections", "openstack"], 120,
                         "verify-connections").tail
    missing = missing_connections(adapter, connections)
    if missing:
        raise DeploymentError(f"required snap connections missing: {missing}")
    _guest(ctx, run_id, attempt, ["sunbeam", "--help"], 120, "verify-cli-probe")
    groups = _guest(ctx, run_id, attempt, ["id", "-nG"], 60, "verify-groups").tail.split()
    if "snap_daemon" not in groups:
        raise DeploymentError("the VM user is not in snap_daemon in a new session")
    return {"snap_version": version, "installed_sha256": digest}


def deploy(ctx: LabContext, run_id: str, artifact_id: str) -> dict:
    rec = ctx.record
    if rec.provisioning_state != "ready":
        raise InputError(f"lab is {rec.provisioning_state}; run `sandbed dev create` first")
    snap, provenance = load_artifact(ctx, artifact_id)
    adapter = select_adapter(_meta(provenance))
    if rec.installed_artifact_id not in (None, artifact_id):
        raise InputError(
            f"lab already runs {rec.installed_artifact_id}; use a new lab for {artifact_id}"
        )
    verify_identity(rec)
    run_dir = ctx.paths.run(run_id)
    guest_dir = f"{rec.home}/{GUEST_DIR}"
    guest_snap = f"{guest_dir}/artifacts/{artifact_id}/openstack.snap"
    fingerprint = provenance["snap"]["sha256"]

    if rec.installed_artifact_id == artifact_id:  # repeat: observe, do not reinstall
        with stage(ctx.log, run_id, "deploy.verify", layer=DeploymentError,
                   fingerprint=fingerprint) as attempt:
            observed = verify_installed(ctx, run_id, attempt, provenance, adapter)
        return {"artifact_id": artifact_id, "already_deployed": True, **observed}

    manifest = write_dev_manifest(
        run_dir / "manifest.yaml", region=rec.config["region"], mgmt_cidr=rec.mgmt_cidr,
        floating_cidr=rec.config["floating_cidr"],
    )
    target, log = ctx.target(), run_dir / "commands" / "transfer.log"
    with stage(ctx.log, run_id, "deploy.transfer", layer=DeploymentError,
               fingerprint=fingerprint) as attempt:
        _guest(ctx, run_id, attempt, ["mkdir", "-p", f"{guest_dir}/artifacts/{artifact_id}"], 60,
               "mkdir")
        for local, remote in ((snap, guest_snap), (Path(manifest), f"{guest_dir}/manifest.yaml")):
            run_logged(target.scp_argv(local, remote), log_path=log, timeout=TRANSFER_TIMEOUT,
                       check=True)
        guest_sha = first_hash(_guest(ctx, run_id, attempt, ["sha256sum", guest_snap], 300,
                                      "guest-sha").tail)
        if guest_sha != fingerprint:
            raise DeploymentError("snap changed in transit: guest checksum != provenance")
    with stage(ctx.log, run_id, "deploy.install", layer=DeploymentError,
               fingerprint=fingerprint) as attempt:
        _guest(ctx, run_id, attempt, ["sudo", "snap", "install", "--dangerous", guest_snap],
               INSTALL_TIMEOUT, "snap-install")
        _guest(ctx, run_id, attempt, ["sudo", "snap", "alias", "openstack.sunbeam", "sunbeam"],
               120, "snap-alias")
    with stage(ctx.log, run_id, "deploy.prepare-node", layer=DeploymentError) as attempt:
        _guest(ctx, run_id, attempt, ["bash", "-o", "pipefail", "-c", PREPARE_NODE],
               INSTALL_TIMEOUT, "prepare-node")
    with stage(ctx.log, run_id, "deploy.connect", layer=DeploymentError) as attempt:
        _guest(ctx, run_id, attempt, ["bash", "-c", WAIT_JUJU], 600, "wait-juju")
        for args in adapter.connect:
            _guest(ctx, run_id, attempt, ["sudo", "snap", "connect", *args], 120,
                   f"connect-{args[0].split(':')[1]}")
    with stage(ctx.log, run_id, "deploy.verify", layer=DeploymentError,
               fingerprint=fingerprint) as attempt:
        observed = verify_installed(ctx, run_id, attempt, provenance, adapter)
    rec.installed_artifact_id = artifact_id
    ctx.store.save(rec)
    return {"artifact_id": artifact_id, "already_deployed": False, **observed}
