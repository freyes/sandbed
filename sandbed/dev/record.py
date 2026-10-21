"""The lab record (`lab.json`): immutable configuration plus what has been observed since."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field

from .errors import InputError
from .paths import LabPaths
from .state import now
from .store import atomic_write_json, read_json

SCHEMA_VERSION = 1
DEFAULT_CONFIG = {
    "provider": "manual",
    "profile": "single-node",
    "vcpus": 8,
    "memory": 32768,  # MiB; provisional, calibrate on the qualified host
    "disk": 100,  # GiB
    "sdb_size": "50GiB",
    "image_alias": "26.04",
    "region": "RegionOne",
    # OpenStack floating-IP network *inside* the lab VM (Sunbeam local access mode). Pinned to
    # Sunbeam's own default so results do not depend on it; must not overlap a lab CIDR.
    "floating_cidr": "172.16.2.0/24",
}
PROVISIONING_STATES = ("new", "provisioning", "ready", "failed", "cleanup_failed", "destroyed")


def config_hash(config: dict) -> str:
    return hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()


@dataclass
class LabRecord:
    lab_uuid: str
    label: str
    owner_uid: int
    project: str
    mgmt_cidr: str
    config: dict
    config_hash: str
    created_at: str = field(default_factory=now)
    schema_version: int = SCHEMA_VERSION
    vm_name: str = "cloud-1"
    vm_user: str = "ubuntu"
    vm_uuid: str | None = None
    vm_address: str | None = None
    image_fingerprint: str | None = None
    terraform_version: str | None = None
    provider_versions: dict = field(default_factory=dict)
    provisioning_state: str = "new"
    installed_artifact_id: str | None = None
    last_run_id: str | None = None

    @property
    def home(self) -> str:
        return f"/home/{self.vm_user}"


class LabStore:
    def __init__(self, paths: LabPaths) -> None:
        self.paths = paths

    def load(self) -> LabRecord:
        raw = read_json(self.paths.record)
        if raw is None:
            raise InputError(f"lab record missing: {self.paths.record}")
        return LabRecord(**raw)  # type: ignore[arg-type]

    def save(self, record: LabRecord) -> None:
        atomic_write_json(self.paths.record, asdict(record))