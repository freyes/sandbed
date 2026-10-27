from __future__ import annotations

import json
import uuid
from unittest.mock import MagicMock, patch

import pytest

from sandbed.dev import provision as prov
from sandbed.dev.context import LabContext
from sandbed.dev.errors import DeploymentError, HostError, ProvisioningError
from sandbed.dev.labs import STORAGE_POOL, LabEntry, Registry, lab_prefix
from sandbed.dev.paths import lab_paths
from sandbed.dev.record import DEFAULT_CONFIG, LabRecord, LabStore, config_hash
from sandbed.dev.runner import CommandResult
from sandbed.dev.state import AttemptLog


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


def ok_result(argv, log_path, tail="") -> CommandResult:
    return CommandResult(tuple(argv), 0, False, 0.0, log_path, tail)


def shell(stdout="", code=0, stderr="") -> MagicMock:
    return MagicMock(returncode=code, stdout=stdout, stderr=stderr)


LXC_INSTANCE = json.dumps({
    "config": {"volatile.uuid": "vm-uuid-1"},
    "state": {"network": {
        "lo": {"addresses": [{"family": "inet", "scope": "local", "address": "127.0.0.1"}]},
        "enp5s0": {"addresses": [
            {"family": "inet6", "scope": "global", "address": "fd00::1"},
            {"family": "inet", "scope": "global", "address": "10.213.0.7"},
        ]},
    }},
})


def test_instance_info_picks_the_global_ipv4(tmp_path) -> None:
    ctx, _ = make_ctx(tmp_path)
    with patch.object(prov, "run", return_value=shell(LXC_INSTANCE)) as fake:
        assert prov.instance_info(ctx.record) == ("vm-uuid-1", "10.213.0.7")
    query = fake.call_args.args[0][-1]
    assert f"project={ctx.record.project}" in query and "cloud-1" in query


def test_vm_identity_change_is_detected(tmp_path) -> None:
    ctx, _ = make_ctx(tmp_path)
    ctx.record.vm_uuid, ctx.record.vm_address = "other-uuid", "10.213.0.7"
    with patch.object(prov, "run", return_value=shell(LXC_INSTANCE)):
        with pytest.raises(DeploymentError, match="identity changed"):
            prov.verify_identity(ctx.record)


def test_provision_applies_network_then_vm_and_never_manages_the_shared_pool(tmp_path) -> None:
    ctx, _ = make_ctx(tmp_path)
    calls: list[list[str]] = []

    def fake_logged(argv, *, log_path, **_kw):
        calls.append(argv)
        return ok_result(argv, log_path)

    def fake_run(cmd, check=True, **_kw):
        if cmd[0] == "terraform" and "output" in cmd:
            return shell(json.dumps({
                "mgmt_network_name": {"value": f"{ctx.record.project}-m"},
            }))
        if cmd[:3] == ["lxc", "image", "info"]:
            return shell("Fingerprint: fp123\nArchitecture: x86_64\nType: virtual-machine\n")
        if cmd[0] == "lxc":
            return shell(LXC_INSTANCE)
        return shell("{}")

    with (
        patch.object(prov, "run_logged", fake_logged),
        patch.object(prov, "run", fake_run),
        patch.object(prov, "ensure_key", return_value="ssh-ed25519 AAAA lab"),
        patch.object(prov, "wait_ready"),
        patch.object(prov, "push_guest_scripts"),
    ):
        record = prov.provision(ctx, "run1")
    tf = [c for c in calls if c[0] == "terraform"]
    roots = [c[1].rsplit("/", 1)[-1] for c in tf]
    assert roots == ["dev-network"] * 2 + ["dev-manual"] * 2
    assert [c[2] for c in tf] == ["init", "apply", "init", "apply"]
    manual = json.loads((ctx.paths.terraform("dev-manual") / "terraform.tfvars.json").read_text())
    assert manual["storage_pool"] == STORAGE_POOL and manual["image_fingerprint"] == "fp123"
    assert manual["ssh_key"].startswith("ssh-ed25519")
    assert record.image_fingerprint == "fp123"
    net_tfvars = ctx.paths.terraform("dev-network") / "terraform.tfvars.json"
    net_vars = json.loads(net_tfvars.read_text())
    assert "ubuntu_image_alias" not in net_vars  # the image is referenced, never copied
    assert (record.provisioning_state, record.vm_uuid, record.vm_address) == (
        "ready", "vm-uuid-1", "10.213.0.7")
    assert LabStore(ctx.paths).load().provisioning_state == "ready"
    assert all(f"-chdir={ctx.paths.root}" in c[1] for c in tf)  # per-lab copies only


def test_failed_provision_records_failed_and_keeps_state(tmp_path) -> None:
    ctx, _ = make_ctx(tmp_path)

    def boom(argv, *, log_path, **_kw):
        raise ProvisioningError("terraform exploded")

    with (
        patch.object(prov, "run_logged", boom),
        patch.object(prov, "ensure_key", return_value="k"),
    ):
        with pytest.raises(ProvisioningError):
            prov.provision(ctx, "run1")
    assert LabStore(ctx.paths).load().provisioning_state == "failed"
    assert ctx.paths.terraform("dev-network").exists()  # kept for a resume or a destroy


def test_missing_source_roots_are_a_host_error(tmp_path, monkeypatch) -> None:
    ctx, _ = make_ctx(tmp_path)
    monkeypatch.setattr(prov, "repo_root", lambda: tmp_path / "nowhere")
    with pytest.raises(HostError, match="source checkout"):
        prov.prepare_working_copies(ctx)


def test_working_copies_exclude_state_and_provider_caches(tmp_path) -> None:
    ctx, _ = make_ctx(tmp_path)
    prov.prepare_working_copies(ctx)
    for root in prov.TF_ROOTS:
        names = {p.name for p in ctx.paths.terraform(root).iterdir()}
        assert "main.tf" in names and ".terraform" not in names