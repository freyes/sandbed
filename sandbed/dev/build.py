"""Build one candidate snap from a frozen snapshot and register it with provenance."""

from __future__ import annotations

import hashlib
import re
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path

import yaml

from ..shell import run
from . import snapshot
from .adapter import SnapMeta, select_adapter
from .capabilities import build_identity
from .context import LabContext
from .errors import BuildError, DeploymentError
from .runner import run_logged
from .stages import stage
from .state import now
from .store import atomic_write_json, ensure_private_dir, read_json

BUILD_TIMEOUT = 60 * 60
ARTIFACT_ID_RE = re.compile(r"a-\d{8}T\d{6}-[0-9a-f]{8}")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def inspect_snap(path: Path) -> SnapMeta:
    """Read meta/snap.yaml from the squashfs without installing anything."""
    raw = run(["unsquashfs", "-cat", str(path), "meta/snap.yaml"], check=False)
    if raw.returncode != 0:
        raise BuildError(f"cannot read meta/snap.yaml from {path.name}")
    data = yaml.safe_load(raw.stdout)
    return SnapMeta(
        name=str(data.get("name", "")),
        version=str(data.get("version", "")),
        base=str(data.get("base", "")),
        architectures=tuple(data.get("architectures") or ()),
    )


def build(ctx: LabContext, run_id: str, source: Path, include: list[str]) -> dict:
    run_dir = ctx.paths.run(run_id)
    work = ensure_private_dir(run_dir / "build")
    src = work / "src"
    with stage(ctx.log, run_id, "build.snapshot", layer=BuildError):
        info = snapshot.capture(
            source, src, include=include, manifest_path=work / "content-manifest.json"
        )
        atomic_write_json(work / "snapshot.json", info.to_json())
    started = now()
    with stage(ctx.log, run_id, "build.pack", layer=BuildError):
        run_logged(
            ["snapcraft", "pack"], cwd=src, timeout=BUILD_TIMEOUT, check=True,
            log_path=run_dir / "commands" / "snapcraft-pack.log",
        )
        snaps = sorted(src.glob("openstack_*_amd64.snap"))
        if len(snaps) != 1:
            raise BuildError(
                f"expected exactly one openstack_*_amd64.snap, found {[s.name for s in snaps]}"
            )
    with stage(ctx.log, run_id, "build.inspect", layer=BuildError):
        meta = inspect_snap(snaps[0])
        adapter = select_adapter(meta)
        digest = sha256_file(snaps[0])
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        artifact_id = f"a-{stamp}-{digest[:8]}"
        dest = ensure_private_dir(ctx.paths.artifact(artifact_id))
        shutil.move(str(snaps[0]), dest / "openstack.snap")
        snapcraft_version = run(["snapcraft", "--version"], check=False).stdout.strip()
        provenance = {
            "artifact_id": artifact_id,
            "run_id": run_id,
            "adapter": adapter.name,
            "adapter_qualified": info.head in adapter.qualified_heads,
            "snap": {"name": meta.name, "version": meta.version, "base": meta.base,
                     "architectures": list(meta.architectures),
                     "size": (dest / "openstack.snap").stat().st_size, "sha256": digest},
            "source": info.to_json(),
            "sandbed": build_identity(),
            "snapcraft_version": snapcraft_version,
            "build_started_at": started,
            "build_finished_at": now(),
        }
        atomic_write_json(dest / "provenance.json", provenance, mode=0o400)
        shutil.rmtree(src, ignore_errors=True)  # keep content-manifest.json, drop the clone
    return provenance


def load_artifact(ctx: LabContext, artifact_id: str) -> tuple[Path, dict]:
    """The registered snap and its provenance, re-hashed: a file changed since the build is
    rejected before anything is copied to the VM."""
    if not ARTIFACT_ID_RE.fullmatch(artifact_id):
        raise BuildError(f"invalid artifact id: {artifact_id!r}")
    directory = ctx.paths.artifact(artifact_id)
    provenance = read_json(directory / "provenance.json")
    if provenance is None:
        raise BuildError(f"unknown artifact {artifact_id} in lab {ctx.entry.label}")
    snap = directory / "openstack.snap"
    if sha256_file(snap) != provenance["snap"]["sha256"]:  # type: ignore[index]
        raise DeploymentError(f"artifact {artifact_id} no longer matches its provenance")
    return snap, provenance  # type: ignore[return-value]


def new_run_id() -> str:
    return uuid.uuid4().hex