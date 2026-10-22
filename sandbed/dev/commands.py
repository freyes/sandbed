"""One function per `sandbed dev` command; each returns the extra fields of its envelope."""

from __future__ import annotations

import os
import shutil
import uuid
from dataclasses import asdict
from pathlib import Path

from .build import build as build_artifact
from .context import LabContext, open_lab
from .deploy import deploy as deploy_artifact
from .destroy import destroy as destroy_lab
from .diagnostics import collect_run
from .errors import DevError, HostError, InputError, InternalError
from .host import check_host, checks_payload, prepare_host
from .hostnet import taken_cidrs
from .labs import LabEntry, Registry, lab_prefix, pick_cidr, validate_label
from .paths import lab_paths
from .provision import provision
from .record import DEFAULT_CONFIG, LabRecord, LabStore, config_hash
from .report import build_result, write_result
from .scenarios import NAME, Outcome, run_scenario
from .state import now
from .store import atomic_write_json, ensure_private_dir, read_json

SUPPORTED_SCENARIOS = (NAME,)


def new_run(ctx: LabContext, kind: str) -> str:
    run_id = uuid.uuid4().hex
    atomic_write_json(ctx.paths.run(run_id) / "run.json",
                      {"run_id": run_id, "kind": kind, "started_at": now()})
    ctx.record.last_run_id = run_id
    ctx.store.save(ctx.record)
    return run_id


def _ids(ctx: LabContext, run_id: str | None = None) -> dict:
    return {"lab_id": ctx.entry.lab_uuid, "label": ctx.entry.label, "run_id": run_id}


def require_host(only: tuple[str, ...] | None = None) -> None:
    failed = [c for c in check_host() if not c.ok and (only is None or c.name in only)]
    if failed:
        detail = "; ".join(f"{c.name}: {c.remediation}" for c in failed)
        raise HostError(f"host is not ready ({detail})")


def host_check() -> dict:
    payload = checks_payload(check_host())
    return {"status": "succeeded" if payload["ok"] else "failed",
            "exit_code": 0 if payload["ok"] else 3, **payload}


def host_prepare(
    osd_size: str, storage_backend: str, storage_source: str | None,
) -> dict:
    return {
        "host": prepare_host(osd_size, backend=storage_backend, source=storage_source),
        "human": "host prepared",
    }


def create(label: str, *, provider: str, profile: str, overrides: dict) -> dict:
    validate_label(label)
    if (provider, profile) != ("manual", "single-node"):
        raise InputError("milestone 1 supports only --provider manual --profile single-node")
    config = {**DEFAULT_CONFIG, **{k: v for k, v in overrides.items() if v is not None}}
    reg, uid = Registry(), os.getuid()
    try:
        reg.find(label, uid)
        exists = True
    except InputError:
        exists = False
    if not exists:
        require_host()
        ensure_private_dir(reg.root)
        # Outside the registry lock: a slow host query must not block other sandbed processes.
        host_cidrs = taken_cidrs()
        with reg.locked():
            lab_uuid = str(uuid.uuid4())
            cidr = pick_cidr([*reg.reserved_cidrs(), *host_cidrs])
            paths = lab_paths(lab_uuid, reg.root)
            # Record first, publish second: a crash in between leaves an unreferenced
            # directory, never a reserved label and CIDR with no usable lab record.
            LabStore(paths).save(LabRecord(
                lab_uuid, label, uid, lab_prefix(lab_uuid), cidr, config, config_hash(config)))
            try:
                reg.add(LabEntry(lab_uuid, label, uid, [cidr]))
            except BaseException:
                shutil.rmtree(paths.root, ignore_errors=True)
                raise
    with open_lab(label, mutate=True, registry=reg) as ctx:
        if ctx.record.config_hash != config_hash(config):
            raise InputError("lab configuration is immutable; create a new lab to change it",
                             **_ids(ctx))
        run_id = new_run(ctx, "create")
        try:
            record = provision(ctx, run_id)
        except DevError as err:
            err.context.update(_ids(ctx, run_id))
            raise
    return {**_ids(ctx, run_id), "vm_address": record.vm_address, "vm_uuid": record.vm_uuid,
            "human": f"lab {label} ready at {record.vm_address}"}


def build(label: str, source: str, include: tuple[str, ...]) -> dict:
    require_host(only=("snapcraft", "squashfs-tools"))
    with open_lab(label, mutate=False) as ctx:
        run_id = new_run(ctx, "build")
        try:
            prov = build_artifact(ctx, run_id, Path(source), list(include))
        except DevError as err:
            err.context.update(_ids(ctx, run_id))
            raise
    return {**_ids(ctx, run_id), "artifact_id": prov["artifact_id"],
            "artifact": {"sha256": prov["snap"]["sha256"], "version": prov["snap"]["version"],
                         "adapter_qualified": prov["adapter_qualified"],
                         "source_head": prov["source"]["head"],
                         "source_content_sha256": prov["source"]["content_sha256"]},
            "human": prov["artifact_id"]}


def deploy(label: str, artifact_id: str) -> dict:
    with open_lab(label, mutate=True) as ctx:
        run_id = new_run(ctx, "deploy")
        try:
            result = deploy_artifact(ctx, run_id, artifact_id)
        except DevError as err:
            err.context.update(_ids(ctx, run_id))
            raise
    return {**_ids(ctx, run_id), **result}


def run_test(label: str, scenario: str) -> dict:
    if scenario not in SUPPORTED_SCENARIOS:
        raise InputError(f"unknown scenario {scenario!r}; supported: {list(SUPPORTED_SCENARIOS)}")
    with open_lab(label, mutate=True) as ctx:
        run_id = new_run(ctx, "test")
        run_dir = ctx.paths.run(run_id)
        outcome, error = Outcome(), None
        artifact = ctx.record.installed_artifact_id
        try:
            run_scenario(ctx, run_id, outcome)
        except DevError as err:
            error = err
        except Exception as err:  # a harness bug must still produce a truthful result
            error = InternalError(f"{type(err).__name__}: {err}")
        collection = collect_run(ctx, run_id)  # after success and after failure
        prov = read_json(ctx.paths.artifact(artifact) / "provenance.json") if artifact else None
        result = build_result(
            command="test", lab_id=ctx.entry.lab_uuid, label=label, run_id=run_id,
            error=error, outcome=outcome, provenance=prov, collection=collection, run_dir=run_dir,
        )
        write_result(run_dir, result)
    result.pop("command", None)
    result.pop("schema_version", None)
    status = result.pop("status")
    return {"status": status, **result}


def status(label: str) -> dict:
    with open_lab(label, mutate=False) as ctx:
        rec = ctx.record
        return {
            **_ids(ctx, rec.last_run_id),
            "provisioning_state": rec.provisioning_state,
            "vm_address": rec.vm_address,
            "installed_artifact_id": rec.installed_artifact_id,
            "blocked_reason": ctx.log.blocked_reason(),
            "attempts": [asdict(a) for a in ctx.log.attempts()],
            "human": f"{label}: {rec.provisioning_state}",
        }


def collect(label: str, run_id: str) -> dict:
    with open_lab(label, mutate=False) as ctx:
        if not ctx.paths.run(run_id).is_dir():
            raise InputError(f"unknown run {run_id}", **_ids(ctx))
        collection = collect_run(ctx, run_id)
        code = 0 if collection["status"] == "complete" else 6
        return {**_ids(ctx, run_id), "collection_status": collection["status"],
                "diagnostics_path": collection["diagnostics_path"],
                "status": "succeeded" if code == 0 else "failed", "exit_code": code,
                "collection": collection["index"]}


def destroy(label: str) -> dict:
    reg = Registry()
    with open_lab(label, mutate=False, registry=reg, include_destroyed=True) as ctx:
        run_id = new_run(ctx, "destroy")
        try:
            result = destroy_lab(ctx, run_id, reg)
        except DevError as err:
            err.context.update(_ids(ctx, run_id))
            raise
    return {**_ids(ctx, run_id), **result, "human": f"lab {label} destroyed"}
