from __future__ import annotations

import json
import subprocess
import uuid
from unittest.mock import MagicMock, patch

import pytest

from sandbed.dev import destroy as destroy_mod
from sandbed.dev import provision as prov
from sandbed.dev.context import LabContext
from sandbed.dev.errors import HostError, ProvisioningError
from sandbed.dev.labs import LabEntry, Registry, lab_prefix
from sandbed.dev.paths import lab_paths
from sandbed.dev.record import DEFAULT_CONFIG, LabRecord, LabStore, config_hash
from sandbed.dev.state import AttemptLog
from sandbed.dev.store import LockBusy, exclusive_lock


def make_ctx(tmp_path) -> tuple[LabContext, Registry]:
    reg = Registry(tmp_path)
    lab = str(uuid.uuid4())
    reg.add(LabEntry(lab, "a", 1000, ["10.213.0.0/24"]))
    paths = lab_paths(lab, tmp_path)
    record = LabRecord(lab, "a", 1000, lab_prefix(lab), "10.213.0.0/24", dict(DEFAULT_CONFIG),
                       config_hash(DEFAULT_CONFIG))
    store = LabStore(paths)
    store.save(record)
    return LabContext(LabEntry(lab, "a", 1000), paths, store, record,
                      AttemptLog(paths.attempts)), reg


def shell(stdout="", code=0, stderr="") -> MagicMock:
    return MagicMock(returncode=code, stdout=stdout, stderr=stderr)


def not_found() -> MagicMock:
    return shell("", 1, "Error: Not found")


def test_owned_resources_reports_project_and_network(tmp_path) -> None:
    ctx, _ = make_ctx(tmp_path)
    net = f"/1.0/networks/{ctx.record.project}-m"

    def fake(cmd, check=True, **_kw):
        if cmd[-1].startswith("/1.0/projects/"):
            return shell("{}")
        return shell(json.dumps([net, "/1.0/networks/lxdbr0"]))

    with patch.object(destroy_mod.subprocess, "run", fake):
        assert destroy_mod.owned_resources(ctx.record) == [
            f"project:{ctx.record.project}", f"network:{ctx.record.project}-m"]


def test_owned_resources_cannot_claim_absence_when_lxd_is_broken(tmp_path) -> None:
    ctx, _ = make_ctx(tmp_path)
    with patch.object(
        destroy_mod.subprocess, "run", return_value=shell("", 1, "connection refused")
    ):
        with pytest.raises(HostError):
            destroy_mod.owned_resources(ctx.record)


def test_destroy_reverses_terraform_order_and_releases_the_registry(tmp_path) -> None:
    ctx, reg = make_ctx(tmp_path)
    for root in prov.TF_ROOTS:
        d = ctx.paths.terraform(root)
        d.mkdir(parents=True)
        (d / "terraform.tfstate").write_text("{}")
    order = []
    with (
        patch.object(destroy_mod, "terraform", lambda c, r, root, *a: order.append((root, a[0]))),
        patch.object(destroy_mod.subprocess, "run", return_value=not_found()),
    ):
        destroy_mod.destroy(ctx, "run1", reg)
    assert order == [("dev-manual", "destroy"), ("dev-network", "destroy")]
    assert reg.reserved_cidrs() == []
    assert LabStore(ctx.paths).load().provisioning_state == "destroyed"


def test_destroy_with_leftovers_is_cleanup_failed_and_keeps_the_cidr(tmp_path) -> None:
    ctx, reg = make_ctx(tmp_path)
    with patch.object(destroy_mod.subprocess, "run", return_value=shell("{}")):
        with pytest.raises(ProvisioningError, match="remain"):
            destroy_mod.destroy(ctx, "run1", reg)
    assert reg.reserved_cidrs() == ["10.213.0.0/24"]
    assert LabStore(ctx.paths).load().provisioning_state == "cleanup_failed"


def test_destroy_after_partial_provisioning_without_state_is_fine(tmp_path) -> None:
    ctx, reg = make_ctx(tmp_path)
    with patch.object(destroy_mod.subprocess, "run", return_value=not_found()):
        assert destroy_mod.destroy(ctx, "run1", reg)["destroyed"] is True


def test_guest_teardown_failure_does_not_stop_vm_cleanup(tmp_path) -> None:
    ctx, reg = make_ctx(tmp_path)
    ctx.record.installed_artifact_id, ctx.record.vm_address = "a-1", "10.213.0.7"
    ctx.paths.ssh_key.parent.mkdir(parents=True)

    def failing(argv, **_kw):
        raise OSError("ssh: no route")

    with (
        patch.object(destroy_mod, "run_logged", failing),
        patch.object(destroy_mod.subprocess, "run", return_value=not_found()),
    ):
        result = destroy_mod.destroy(ctx, "run1", reg)
    assert result["destroyed"] and "guest teardown skipped" in result["evidence"][0]


def _lock_guard(reg: Registry) -> tuple[list[bool], callable]:
    """Wrap ``set_state`` so each call records whether the registry lock was held."""
    held: list[bool] = []
    original = reg.set_state

    def guarded(lab_uuid: str, state: str) -> None:
        try:
            with exclusive_lock(reg.root / "registry.lock", timeout=0.05):
                pass
            held.append(False)  # lock was NOT held by the caller
        except LockBusy:
            held.append(True)
        original(lab_uuid, state)

    reg.set_state = guarded
    return held, original


def test_destroy_holds_registry_lock_for_set_state_destroyed(tmp_path) -> None:
    ctx, reg = make_ctx(tmp_path)
    held, _ = _lock_guard(reg)
    with patch.object(destroy_mod.subprocess, "run", return_value=not_found()):
        destroy_mod.destroy(ctx, "run1", reg)
    assert held == [True]
    assert reg.reserved_cidrs() == []


def test_destroy_holds_registry_lock_for_set_state_cleanup_failed(tmp_path) -> None:
    ctx, reg = make_ctx(tmp_path)
    held, _ = _lock_guard(reg)
    with patch.object(destroy_mod.subprocess, "run", return_value=shell("{}")):
        with pytest.raises(ProvisioningError, match="remain"):
            destroy_mod.destroy(ctx, "run1", reg)
    assert held == [True]
    assert reg.reserved_cidrs() == ["10.213.0.0/24"]


def test_lxc_json_timeout_raises_hosterror() -> None:
    with patch.object(
        destroy_mod.subprocess, "run",
        side_effect=subprocess.TimeoutExpired(["lxc", "query", "/1.0"], 30),
    ):
        with pytest.raises(HostError, match="cannot verify"):
            destroy_mod._lxc_json("/1.0/projects/foo")


def test_lxc_json_missing_lxc_raises_hosterror() -> None:
    with patch.object(destroy_mod.subprocess, "run", side_effect=FileNotFoundError("lxc")):
        with pytest.raises(HostError, match="cannot verify"):
            destroy_mod._lxc_json("/1.0/projects/foo")
