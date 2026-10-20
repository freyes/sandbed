# Managed MAAS Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `sandbed sunbeam maas up` deploys MAAS itself (maas snap + maas-test-db snap, region and rack controllers on one LXD VM) with Terraform, configures it with the Canonical `maas` Terraform provider, enlists every Sunbeam node except the client, and then deploys Sunbeam. `sandbed sunbeam manual` must behave exactly as before.

**Architecture:** Three Terraform roots. `terraform/maas/` (lxd provider) builds the networks, `maas-1`, the client VM, the empty PXE VMs and the LXD HTTPS/trust that MAAS needs for power control. `terraform/maas-config/` (new, `canonical/maas` provider) reads the first root's local state and configures MAAS: images (blocking until synced), network/DHCP, machines, tags. Python only orchestrates: it applies the roots in order and reads the MAAS URL, API key and client IP from Terraform outputs. The phase list is unchanged; `maas-setup` now applies `maas-config`.

**Tech Stack:** Python >=3.10, click, pytest, ruff; Terraform >=1.6 for `maas-config` (>=1.5 elsewhere); providers `terraform-lxd/lxd ~> 3.0`, `hashicorp/tls ~> 4.0`, `canonical/maas ~> 2.10`; snaps `maas` and `maas-test-db`; bash scripts run inside `maas-1`.

## Global Constraints

- Python >=3.10. Ruff: line length 100, rules E/F/I. Keep `uv run ruff check .` and `uv run pytest` green.
- Terraform: `terraform -chdir=terraform/<root> validate` and `fmt -check` must pass for `network`, `manual`, `maas` and `maas-config`. The lxd provider 3.x takes `timeouts = { ... }` (attribute); the maas provider takes `timeouts { ... }` (block).
- **`manual` must not change.** `tests/test_manual_unchanged.py` (Task 3) pins its behaviour and must pass, unmodified, after every task. Never edit it to make a task pass. `terraform/network/`, `manifests/manual.yaml.j2`, `scripts/prep-host.sh` and `sandbed/testflinger.py` are not touched. The only edit to `terraform/manual/` is the one-line syntax fix in Task 2.
- Every shared code path touched for MAAS is guarded by `provider == "maas"` (cli resolve step, preflight LXD check, terraform env/destroy/plan note, destroy resolve, `maas_name` default).
- Hook env vars are a public interface: never remove or rename a `Config` field. Phase names and order stay `preflight, install-tools, terraform, maas-setup, sunbeam`.
- Tests mock `run`. Never call real `terraform`, `snap`, `ssh`, `lxc` or `testflinger-cli` from tests.
- `.omo/` is historical: do not edit it. Never commit `tmp*.yaml` or `.sandbed.testflinger.job_id`.
- Commit messages: conventional style (`feat(scope): ...`, `fix(scope): ...`).
- Update `docs/` and `sandbed-design.md` for CLI flag, phase and path changes (Task 11).
- Fixed values (use exactly): generic gateway `.1`, `maas-1` `.2`, `sunbeam-client-1` `.3`, MAAS dynamic range `.100-.200`, reserved `<name>-internal-api` `.20-.39`, `<name>-public-api` `.40-.59`; MAAS URL `http://<.2>:5240/MAAS`; LXD listens on `<.1>:8443`; defaults `maas_channel=3.6/stable`, `maas_admin_password=changeme`, `deploy_series=noble`, `image_sync_timeout=60m`, `upstream_dns=1.1.1.1`, maas-1 `4 vCPU / 8192 MiB / 50 GiB`; PXE VM MACs `00:16:3e:5a:01:<hex>` (generic) and `00:16:3e:5a:02:<hex>` (external) with index bases cloud 0, sunbeam-controller 16, juju-controller 32, observability 48, landscape 64; maas-1 MAC `00:16:3e:5a:00:02`, client generic `...:00:03`, client mgmt `...:00:04`.

## Environment notes (read first)

- In this container `uv` is not installed and `tenacity` is missing from the system Python. Run tests with:
  `PYTHONPATH=/tmp/opencode/deps python3 -m pytest tests -q -p no:cacheprovider`
  (create the deps dir once with `pip install --target /tmp/opencode/deps tenacity`). On a normal checkout use `uv run pytest` and `uv run ruff check .`. `ruff` could not be installed here; check line length with `awk 'length>100{print FILENAME":"FNR}' sandbed/*.py tests/*.py` and import order by eye (isort style: stdlib, third party, first party).
- `terraform` is not installed here either. A binary can be fetched with:
  `curl -sfL -o /tmp/opencode/tf.zip https://releases.hashicorp.com/terraform/1.16.6/terraform_1.16.6_linux_amd64.zip && python3 -c "import zipfile;zipfile.ZipFile('/tmp/opencode/tf.zip').extractall('/tmp/opencode/tfbin')"` then `export PATH=/tmp/opencode/tfbin:$PATH`. `terraform init -backend=false` needs registry access.
- `git commit` fails in this container (no `user.name`/`user.email`; do not change git config). Commit with the identity the repository owner approves, for example one-off `GIT_AUTHOR_NAME/EMAIL` and `GIT_COMMITTER_NAME/EMAIL` environment variables.
- Baseline before any change: 53 tests pass and 1 fails (`tests/test_cli.py::test_cli_plan_manual`), at HEAD, independent of this work. Task 1 fixes it.
- Every code block below was exercised in a scratch copy: the full pytest suite (89 tests) passes and `terraform validate` plus `fmt -check` succeed for all four roots with Terraform 1.16.6. The parts that can only be proven against a live LXD/MAAS host are listed in Task 12.

## File Structure

| Path | Change | Responsibility |
|---|---|---|
| `tests/test_manual_unchanged.py` | create | pins `manual` behaviour |
| `sandbed/config.py` | modify | new MAAS fields, `maas_name` default for maas only |
| `sandbed/terraform.py` | modify | env/vars plumbing, `module_outputs`, `resolve_maas`, `apply_maas_config`, maas destroy |
| `sandbed/preflight.py` | modify | maas-only LXD `core.https_address` check |
| `sandbed/install.py` | modify | drop host `maas` snap |
| `sandbed/cli.py` | modify | new flags, drop `--maas-url/--maas-token`, resolve step, plan note |
| `sandbed/maas.py` | rewrite | `setup_maas` applies `maas-config` |
| `sandbed/sunbeam.py`, `sandbed/destroy.py`, `manifests/maas.yaml.j2` | modify | series templating, client host resolution on destroy |
| `terraform/maas/*.tf`, `files/*.sh`, `cloud-init/*.tpl` | rewrite/create | LXD side of managed MAAS |
| `terraform/maas-config/*.tf` | create | MAAS configuration via the `maas` provider |
| `.github/workflows/ci.yml`, `AGENTS.md`, `docs/*`, `sandbed-design.md` | modify | CI + documentation |

---

### Task 1: Fix the pre-existing `test_cli_plan_manual` failure

`Text.from_ansi(MagicMock)` raises, so the test fails at HEAD. This is a test-only fix: give the mocked `run` a real `stdout` and `returncode`.

**Files:**
- Modify: `tests/test_cli.py` (the `test_cli_plan_manual` function)

- [ ] **Step 1: Confirm the failure**

Run: `PYTHONPATH=/tmp/opencode/deps python3 -m pytest tests/test_cli.py::test_cli_plan_manual -q -p no:cacheprovider`
Expected: FAIL (`TypeError: expected string or bytes-like object, got 'MagicMock'`)

- [ ] **Step 2: Apply the fix**

```python
@patch("sandbed.terraform.run")
def test_cli_plan_manual(fake_run) -> None:
    fake_run.return_value.stdout = ""
    fake_run.return_value.returncode = 0
    result = CliRunner().invoke(cli, ["sunbeam", "manual", "plan"])
    assert result.exit_code == 0
```

- [ ] **Step 3: Run the whole suite**

Run: `PYTHONPATH=/tmp/opencode/deps python3 -m pytest tests -q -p no:cacheprovider`
Expected: `54 passed`

- [ ] **Step 4: Commit**

```bash
git add tests/test_cli.py
git commit -m "test(cli): give the mocked terraform run a real stdout in test_cli_plan_manual"
```


---

### Task 2: Make `terraform/manual` valid with the current lxd provider (prerequisite fix)

At HEAD `terraform -chdir=terraform/manual validate` (and `terraform/maas`) fails with `Unsupported block type ... timeouts`: the lxd provider 3.x (the pin `~> 3.0` resolves to 3.1.1) takes `timeouts` as an attribute. The repo's CI terraform job therefore cannot pass today. This is a one-token syntax fix with no semantic change (the intended 10 minute create timeout). It is a separate commit so it can be dropped or reviewed on its own; `terraform/maas` is fixed by the rewrite in Task 9.

**Files:**
- Modify: `terraform/manual/main.tf` (line 66)

- [ ] **Step 1: Reproduce**

Run: `terraform -chdir=terraform/manual init -backend=false && terraform -chdir=terraform/manual validate`
Expected: FAIL, `Unsupported block type` on `timeouts`.

- [ ] **Step 2: Fix**

```diff
--- a/terraform/manual/main.tf
+++ b/terraform/manual/main.tf
@@ -63,7 +63,7 @@
   type  = "virtual-machine"
   image = var.image_fingerprint
 
-  timeouts {
+  timeouts = {
     create = "10m"
   }
 
```

- [ ] **Step 3: Verify**

Run: `terraform -chdir=terraform/manual validate && terraform -chdir=terraform/manual fmt -check`
Expected: `Success! The configuration is valid.` and exit 0.

- [ ] **Step 4: Commit**

```bash
git add terraform/manual/main.tf
git commit -m "fix(terraform): use the timeouts attribute syntax required by lxd provider 3.x"
```

---

### Task 3: Pin `manual` behaviour with characterization tests

These tests must pass on the current code (they describe it) and keep passing after every later task. They cover: the exact Terraform command sequence and variable set for `manual` apply and plan, no custom environment passed to Terraform, the preflight command order, the install commands (no `maas` anything), the Sunbeam SSH commands, every existing hook env key, the phase list, and that `manual up` runs every phase and never resolves MAAS.

**Files:**
- Create: `tests/test_manual_unchanged.py`

**Interfaces:**
- Produces: a guard suite that must pass, unmodified, after every later task.

- [ ] **Step 1: Write the tests**

```python
"""Characterization tests: `manual` behaviour that the managed-MAAS work must not alter."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from sandbed import terraform
from sandbed.cli import PHASES, cli
from sandbed.config import load_config
from sandbed.hooks import _env
from sandbed.install import install
from sandbed.preflight import check
from sandbed.sunbeam import deploy_sunbeam

# Every Config field that exists today. Hooks export each as SDB_CFG_<NAME> (public interface),
# so none may disappear.
EXISTING_CONFIG_FIELDS = [
    "provider", "action", "mgmt_cidr", "external_cidr", "generic_cidr", "vcpus", "memory", "disk",
    "obs_count", "landscape_count", "ubuntu_image_remote", "ubuntu_image_alias", "ceph_pool_name",
    "ceph_rbd_pool", "ceph_cluster_name", "ceph_user_name", "microceph_osd_size",
    "microceph_osd_img", "microceph_disk_size", "shared_dir", "maas_name", "maas_url", "maas_token",
    "maas_space", "lxd_https_port", "internal_api_range", "public_api_range", "floating_ip_range",
    "region", "db_topology", "project_network", "ssh_key", "ssh_host", "vm_user", "hooks_dir",
    "no_hooks", "cloud_init_templates_dir", "use_testflinger", "testflinger_series",
    "testflinger_queue", "testflinger_timeout", "testflinger_job", "testflinger_job_file",
    "testflinger_key", "terraform_dir", "state_file",
]

MANUAL_INSTANCE_VARS = {
    "ceph_pool_name", "cloud_init_templates_dir", "disk", "external_cidr", "external_network_name",
    "image_fingerprint", "memory", "mgmt_network_name", "sdb_size", "shared_dir", "ssh_key",
    "vcpus", "vm_count", "vm_user",
}


def _out(stdout: str = "") -> MagicMock:
    m = MagicMock()
    m.stdout = stdout
    m.returncode = 0
    return m


def _var_names(cmd: list[str]) -> set[str]:
    return {a.removeprefix("-var=").split("=", 1)[0] for a in cmd if a.startswith("-var=")}


@patch("sandbed.terraform.run")
def test_manual_apply_runs_network_then_manual(fake_run) -> None:
    fake_run.return_value = _out()
    terraform.apply(load_config("manual", "up", ssh_key="k"))
    cmds = [c.args[0] for c in fake_run.call_args_list]
    heads = [c[1:4] for c in cmds]
    assert heads == [
        ["-chdir=terraform/network", "init", "-backend=false"],
        ["-chdir=terraform/network", "apply", "-auto-approve"],
        ["-chdir=terraform/manual", "init", "-backend=false"],
        ["-chdir=terraform/network", "output", "-json"],
        ["-chdir=terraform/manual", "apply", "-auto-approve"],
    ]
    assert _var_names(cmds[-1]) == MANUAL_INSTANCE_VARS
    # manual never passes a custom environment to terraform
    assert all("env" not in c.kwargs for c in fake_run.call_args_list)


@patch("sandbed.terraform.run")
def test_manual_plan_targets_only_the_manual_module(fake_run) -> None:
    fake_run.return_value = _out()
    terraform.plan(load_config("manual", "plan", ssh_key="k"), "manual")
    cmds = [c.args[0] for c in fake_run.call_args_list]
    assert {c[1] for c in cmds} == {"-chdir=terraform/manual", "-chdir=terraform/network"}
    assert [c[2] for c in cmds if c[1] == "-chdir=terraform/manual"] == ["init", "plan"]


@patch("sandbed.preflight.os.path.exists", return_value=True)
@patch("sandbed.preflight.run")
def test_manual_preflight_checks_lxd_and_microceph_only(fake_run, _exists) -> None:
    fake_run.return_value = _out()
    check(load_config("manual", "up"))
    assert [c.args[0] for c in fake_run.call_args_list] == [
        ["lxc", "query", "/1.0"],
        ["sudo", "microceph", "cluster", "list"],
    ]


@patch("sandbed.install.verify_ceph")
@patch("sandbed.install._installed", return_value=False)
@patch("sandbed.install.run")
def test_manual_install_never_touches_maas(fake_run, _installed, _verify) -> None:
    fake_run.return_value = _out("/dev/loop9")
    install(load_config("manual", "up"))
    cmds = [c.args[0] for c in fake_run.call_args_list]
    assert ["sudo", "snap", "install", "openstack"] in cmds
    assert not any("maas" in part for cmd in cmds for part in cmd)


@patch("sandbed.sunbeam.render", return_value="MANIFEST")
@patch("sandbed.sunbeam.run")
def test_manual_sunbeam_commands(fake_run, _render) -> None:
    ctx = load_config("manual", "sunbeam")
    ctx.ssh_host = "10.0.0.5"
    deploy_sunbeam(ctx)
    assert [c.args[0] for c in fake_run.call_args_list] == [
        ["ssh", "ubuntu@10.0.0.5", "sudo snap install openstack"],
        ["ssh", "ubuntu@10.0.0.5", "sunbeam prepare-node-script --bootstrap | bash"],
        [
            "ssh",
            "ubuntu@10.0.0.5",
            "sunbeam cluster bootstrap --role control,compute,storage --manifest MANIFEST",
        ],
        ["ssh", "ubuntu@10.0.0.5", "sunbeam configure --accept-defaults --openrc"],
    ]


def test_manual_hook_env_keeps_every_existing_key() -> None:
    ctx = load_config("manual", "up")
    env = _env(ctx, "terraform", "terraform.pre")
    for field in EXISTING_CONFIG_FIELDS:
        assert f"SDB_CFG_{field.upper()}" in env, field
    assert env["SDB_PROVIDER"] == "manual"
    # the maas deployment name only gets a default for the maas provider
    assert env["SDB_CFG_MAAS_NAME"] == ""
    assert env["SDB_CFG_MAAS_URL"] == ""
    assert env["SDB_CFG_MAAS_TOKEN"] == ""


def test_phase_list_is_stable() -> None:
    assert PHASES == ["preflight", "install-tools", "terraform", "maas-setup", "sunbeam"]


def test_manual_up_runs_every_phase_and_never_resolves_maas(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    with (
        patch("sandbed.cli._dispatch") as dispatch,
        patch("sandbed.terraform.resolve_maas", create=True) as resolve,
    ):
        result = CliRunner().invoke(cli, ["sunbeam", "manual", "up", "--no-hooks"])
    assert result.exit_code == 0, result.output
    assert [c.args[1] for c in dispatch.call_args_list] == PHASES
    assert "done maas-setup" in result.output
    resolve.assert_not_called()
```

- [ ] **Step 2: Run them against the unchanged code**

Run: `PYTHONPATH=/tmp/opencode/deps python3 -m pytest tests/test_manual_unchanged.py -q -p no:cacheprovider`
Expected: `8 passed`. If anything fails here, stop: the description of current behaviour is wrong and must be fixed before any change.

- [ ] **Step 3: Commit**

```bash
git add tests/test_manual_unchanged.py
git commit -m "test: pin the manual provider behaviour"
```

---



### Task 4: Config fields for managed MAAS

**Files:**
- Modify: `sandbed/config.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Produces (new `Config` fields, all with defaults; nothing is removed): `maas_admin_password: str = "changeme"`, `maas_channel: str = "3.6/stable"`, `maas_vcpus: int = 4`, `maas_memory: int = 8192`, `maas_disk: int = 50`, `maas_deploy_series: str = "noble"`, `maas_image_sync_timeout: str = "60m"`, `maas_upstream_dns: str = "1.1.1.1"`, `maas_parallelism: int = 10`.
- `load_config("maas", ...)` sets `maas_name` to `"sandbed"` when empty. For `manual` it stays `""` (it is exported to hooks as `SDB_CFG_MAAS_NAME`).
- `maas_url` and `maas_token` stay as fields, but are now filled from Terraform outputs.

- [ ] **Step 1: Add the tests**

```diff
--- a/tests/test_config.py
+++ b/tests/test_config.py
@@ -37,4 +37,21 @@
     assert cfg.testflinger_queue == "q"
     assert cfg.testflinger_series == "noble"
     assert cfg.testflinger_timeout == "30m"
-    assert cfg.testflinger_key == ["lp:user"]
\ No newline at end of file
+    assert cfg.testflinger_key == ["lp:user"]
+
+def test_maas_defaults() -> None:
+    cfg = load_config("maas", "up")
+    assert cfg.maas_name == "sandbed"
+    assert cfg.maas_admin_password == "changeme"
+    assert cfg.maas_deploy_series == "noble"
+    assert cfg.maas_channel == "3.6/stable"
+    assert cfg.maas_image_sync_timeout == "60m"
+    assert (cfg.maas_vcpus, cfg.maas_memory, cfg.maas_disk) == (4, 8192, 50)
+
+
+def test_maas_name_override_wins() -> None:
+    assert load_config("maas", "up", maas_name="mycloud").maas_name == "mycloud"
+
+
+def test_manual_keeps_empty_maas_name() -> None:
+    assert load_config("manual", "up").maas_name == ""
```

- [ ] **Step 2: Run them, expect failure**

Run: `PYTHONPATH=/tmp/opencode/deps python3 -m pytest tests/test_config.py -q -p no:cacheprovider`
Expected: 3 FAIL (`AttributeError: 'Config' object has no attribute 'maas_admin_password'`, and the `maas_name == "sandbed"` assertions).

- [ ] **Step 3: Implement**

```diff
--- a/sandbed/config.py
+++ b/sandbed/config.py
@@ -36,6 +36,15 @@
     maas_token: str = ""
     maas_space: str = "myspace"
     lxd_https_port: int = 8443
+    maas_admin_password: str = "changeme"
+    maas_channel: str = "3.6/stable"
+    maas_vcpus: int = 4
+    maas_memory: int = 8192
+    maas_disk: int = 50
+    maas_deploy_series: str = "noble"
+    maas_image_sync_timeout: str = "60m"
+    maas_upstream_dns: str = "1.1.1.1"
+    maas_parallelism: int = 10
     internal_api_range: str = ""
     public_api_range: str = ""
     floating_ip_range: str = ""
@@ -75,4 +84,6 @@
             if isinstance(value, tuple):
                 value = list(value)
             setattr(cfg, key, value)
+    if provider == "maas" and not cfg.maas_name:
+        cfg.maas_name = "sandbed"
     return cfg
\ No newline at end of file
```

- [ ] **Step 4: Run the whole suite**

Run: `PYTHONPATH=/tmp/opencode/deps python3 -m pytest tests -q -p no:cacheprovider`
Expected: all pass, including `tests/test_manual_unchanged.py`.

- [ ] **Step 5: Commit**

```bash
git add sandbed/config.py tests/test_config.py
git commit -m "feat(config): add managed MAAS settings"
```

---

### Task 5: Terraform plumbing (vars, env, outputs, maas-config apply, destroy)

**Files:**
- Modify: `sandbed/terraform.py`
- Test: `tests/test_terraform.py`

**Interfaces:**
- Consumes: Task 4 `Config` fields.
- Produces:
  - `_tf(ctx, module, args, vars_map=None, capture_output=True, env=None)`: passes `env=` to `run` only when it is not `None`, so every `manual` call is unchanged.
  - `_maas_env(ctx) -> dict`: `os.environ` plus `TF_VAR_maas_admin_password`.
  - `module_outputs(ctx, module) -> dict`: `terraform output -json` as `{name: value}`, `{}` on failure.
  - `resolve_maas(ctx, required: bool = True) -> None`: sets `ctx.maas_url`, `ctx.maas_token`, `ctx.maas_admin_password`, and `ctx.ssh_host` (only if empty) from the `maas` root outputs `maas_url`, `maas_api_key`, `maas_admin_password`, `client_mgmt_ip`. Raises `RuntimeError` mentioning `maas up` when required and outputs are missing.
  - `apply_maas_config(ctx) -> None`: `init -backend=false` then `apply -auto-approve [-parallelism=N]` on `terraform/maas-config` with vars `deploy_series, maas_name, maas_space, generic_cidr, ssh_key, upstream_dns, image_sync_timeout`, streaming output.
  - `destroy_terraform(ctx)`: for `maas`, destroy the `maas` root (with the network outputs as extra vars and the password env), then delete `terraform/maas-config/terraform.tfstate*`; `manual` is unchanged.

- [ ] **Step 1: Add the tests**

```diff
--- a/tests/test_terraform.py
+++ b/tests/test_terraform.py
@@ -1,5 +1,10 @@
+import json
 import os
+from unittest.mock import MagicMock, patch
 
+import pytest
+
+from sandbed import terraform
 from sandbed.config import load_config
 from sandbed.terraform import terraform_vars
 
@@ -19,4 +24,120 @@
 def test_terraform_vars_cloud_init_dir_absolute() -> None:
     v = terraform_vars(load_config("manual", "up"))
     assert os.path.isabs(v["cloud_init_templates_dir"])
-    assert v["cloud_init_templates_dir"].endswith("templates/cloud-init")
\ No newline at end of file
+    assert v["cloud_init_templates_dir"].endswith("templates/cloud-init")
+
+# --- managed MAAS -------------------------------------------------------------------------------
+
+
+def _out(stdout: str = "", returncode: int = 0) -> MagicMock:
+    m = MagicMock()
+    m.stdout = stdout
+    m.returncode = returncode
+    return m
+
+
+def _var_names(cmd: list[str]) -> set[str]:
+    return {a.removeprefix("-var=").split("=", 1)[0] for a in cmd if a.startswith("-var=")}
+
+
+def test_terraform_vars_maas() -> None:
+    v = terraform_vars(load_config("maas", "up", maas_deploy_series="jammy", maas_disk=80))
+    assert v["deploy_series"] == "jammy"
+    assert v["maas_disk"] == "80"
+    assert v["maas_name"] == "sandbed"
+    assert v["image_sync_timeout"] == "60m"
+
+
+@patch("sandbed.terraform.run")
+def test_maas_apply_passes_password_via_env_not_var(fake_run) -> None:
+    fake_run.return_value = _out()
+    ctx = load_config("maas", "up", ssh_key="k", maas_admin_password="s3cret")
+    terraform.apply(ctx)
+    apply_call = [c for c in fake_run.call_args_list if c.args[0][1] == "-chdir=terraform/maas"][-1]
+    assert apply_call.kwargs["env"]["TF_VAR_maas_admin_password"] == "s3cret"
+    assert "maas_admin_password" not in _var_names(apply_call.args[0])
+    assert not any("s3cret" in a for a in apply_call.args[0])
+    assert "maas_vcpus" in _var_names(apply_call.args[0])
+
+
+@patch("sandbed.terraform.run")
+def test_apply_maas_config_command(fake_run) -> None:
+    fake_run.return_value = _out()
+    ctx = load_config("maas", "up", ssh_key="k", maas_parallelism=4)
+    terraform.apply_maas_config(ctx)
+    init, apply_ = [c.args[0] for c in fake_run.call_args_list]
+    assert init == ["terraform", "-chdir=terraform/maas-config", "init", "-backend=false"]
+    assert apply_[:5] == [
+        "terraform", "-chdir=terraform/maas-config", "apply", "-auto-approve", "-parallelism=4",
+    ]
+    assert _var_names(apply_) == {
+        "deploy_series", "maas_name", "maas_space", "generic_cidr", "ssh_key", "upstream_dns",
+        "image_sync_timeout",
+    }
+
+
+@patch("sandbed.terraform.run")
+def test_resolve_maas_fills_ctx_from_outputs(fake_run) -> None:
+    outputs = {
+        "maas_url": {"value": "http://172.16.1.2:5240/MAAS"},
+        "maas_api_key": {"value": "ck:tk:sk", "sensitive": True},
+        "maas_admin_password": {"value": "s3cret", "sensitive": True},
+        "client_mgmt_ip": {"value": "192.168.200.7"},
+    }
+    fake_run.return_value = _out(json.dumps(outputs))
+    ctx = load_config("maas", "up")
+    terraform.resolve_maas(ctx)
+    assert ctx.maas_url == "http://172.16.1.2:5240/MAAS"
+    assert ctx.maas_token == "ck:tk:sk"
+    assert ctx.maas_admin_password == "s3cret"
+    assert ctx.ssh_host == "192.168.200.7"
+
+
+@patch("sandbed.terraform.run")
+def test_resolve_maas_keeps_explicit_ssh_host(fake_run) -> None:
+    fake_run.return_value = _out(json.dumps({
+        "maas_url": {"value": "u"}, "maas_api_key": {"value": "t"},
+        "client_mgmt_ip": {"value": "192.168.200.7"},
+    }))
+    ctx = load_config("maas", "up", ssh_host="10.9.9.9")
+    terraform.resolve_maas(ctx)
+    assert ctx.ssh_host == "10.9.9.9"
+
+
+@patch("sandbed.terraform.run")
+def test_resolve_maas_required_raises_when_outputs_missing(fake_run) -> None:
+    fake_run.return_value = _out("{}")
+    with pytest.raises(RuntimeError, match="maas up"):
+        terraform.resolve_maas(load_config("maas", "up"))
+
+
+@patch("sandbed.terraform.run")
+def test_resolve_maas_optional_is_silent_when_outputs_missing(fake_run) -> None:
+    fake_run.return_value = _out("", returncode=1)
+    ctx = load_config("maas", "destroy")
+    terraform.resolve_maas(ctx, required=False)
+    assert ctx.maas_url == ""
+
+
+@patch("sandbed.terraform.run")
+def test_maas_destroy_skips_maas_config_and_drops_its_state(fake_run, tmp_path) -> None:
+    fake_run.return_value = _out()
+    tf_dir = tmp_path / "terraform"
+    (tf_dir / "maas-config").mkdir(parents=True)
+    state = tf_dir / "maas-config" / "terraform.tfstate"
+    state.write_text("{}")
+    ctx = load_config("maas", "destroy", ssh_key="k")
+    ctx.terraform_dir = str(tf_dir)
+    terraform.destroy_terraform(ctx)
+    modules = [c.args[0][1] for c in fake_run.call_args_list if "destroy" in c.args[0]]
+    assert modules == [f"-chdir={tf_dir}/maas", f"-chdir={tf_dir}/network"]
+    assert not state.exists()
+
+
+@patch("sandbed.terraform.run")
+def test_manual_destroy_unchanged(fake_run) -> None:
+    fake_run.return_value = _out()
+    terraform.destroy_terraform(load_config("manual", "destroy", ssh_key="k"))
+    calls = fake_run.call_args_list
+    assert [c.args[0][1] for c in calls] == ["-chdir=terraform/manual", "-chdir=terraform/network"]
+    assert all("env" not in c.kwargs for c in calls)
```

- [ ] **Step 2: Run them, expect failure**

Run: `PYTHONPATH=/tmp/opencode/deps python3 -m pytest tests/test_terraform.py -q -p no:cacheprovider`
Expected: FAIL (`AttributeError: module 'sandbed.terraform' has no attribute 'apply_maas_config'` / `resolve_maas`, and missing `env`).

- [ ] **Step 3: Implement**

Add the new `INSTANCE_VARS` entries and `terraform_vars` keys, and replace everything from `def _tf(` to the end of the file:

```diff
--- a/sandbed/terraform.py
+++ b/sandbed/terraform.py
@@ -26,7 +26,12 @@
         "mgmt_network_name", "ceph_pool_name", "image_fingerprint", "shared_dir",
         "ssh_key", "vm_user", "generic_network_name", "generic_cidr",
         "external_network_name", "external_cidr", "generic_parent_bridge",
-        "cloud_init_templates_dir",
+        "cloud_init_templates_dir", "maas_vcpus", "maas_memory", "maas_disk", "maas_channel",
+        "upstream_dns", "lxd_https_port",
+    ),
+    "maas-config": (
+        "deploy_series", "maas_name", "maas_space", "generic_cidr", "ssh_key", "upstream_dns",
+        "image_sync_timeout",
     ),
 }
 
@@ -62,6 +67,16 @@
         "obs_count": str(ctx.obs_count),
         "landscape_count": str(ctx.landscape_count),
         "cloud_init_templates_dir": os.path.abspath(ctx.cloud_init_templates_dir),
+        "maas_vcpus": str(ctx.maas_vcpus),
+        "maas_memory": str(ctx.maas_memory),
+        "maas_disk": str(ctx.maas_disk),
+        "maas_channel": ctx.maas_channel,
+        "upstream_dns": ctx.maas_upstream_dns,
+        "lxd_https_port": str(ctx.lxd_https_port),
+        "deploy_series": ctx.maas_deploy_series,
+        "maas_name": ctx.maas_name,
+        "maas_space": ctx.maas_space,
+        "image_sync_timeout": ctx.maas_image_sync_timeout,
     }
 
 
@@ -96,17 +111,30 @@
     }
 
 
+def _maas_env(ctx: Config) -> dict:
+    # The admin password reaches terraform through the environment, not `-var`, so it never
+    # shows up in `ps`.
+    return {**os.environ, "TF_VAR_maas_admin_password": ctx.maas_admin_password}
+
+
+def _env_for(ctx: Config) -> dict | None:
+    return _maas_env(ctx) if ctx.provider == "maas" else None
+
+
 def _tf(
     ctx: Config,
     module: str,
     args: list[str],
     vars_map: dict | None = None,
     capture_output: bool = True,
+    env: dict | None = None,
 ) -> subprocess.CompletedProcess | None:
     cmd = ["terraform", f"-chdir={ctx.terraform_dir}/{module}", *args]
     for key, value in sorted((vars_map or {}).items()):
         cmd += [f"-var={key}={value}"]
-    return run(cmd, capture_output=capture_output)
+    # Only pass `env` when set so provider-agnostic calls stay byte-identical.
+    kwargs = {"env": env} if env is not None else {}
+    return run(cmd, capture_output=capture_output, **kwargs)
 
 
 def apply(ctx: Config) -> None:
@@ -122,6 +150,7 @@
             ["apply", "-auto-approve"],
             _vars_for(ctx, ctx.provider, _instance_extra(ctx)),
             capture_output=False,
+            env=_env_for(ctx),
         )
 
 
@@ -130,12 +159,71 @@
     if name == "network":
         p = _tf(ctx, name, ["plan"], _vars_for(ctx, "network"))
     else:
-        p = _tf(ctx, name, ["plan"], _vars_for(ctx, name, _instance_extra(ctx)))
+        p = _tf(
+            ctx, name, ["plan"], _vars_for(ctx, name, _instance_extra(ctx)), env=_env_for(ctx)
+        )
 
     console.print(f"[bold]Terraform plan for {name}[/bold]")
     console.print(Text.from_ansi(p.stdout))
 
+
+def module_outputs(ctx: Config, module: str) -> dict:
+    """Return `terraform output -json` of a module as {name: value}; empty if unavailable."""
+    result = run(
+        ["terraform", f"-chdir={ctx.terraform_dir}/{module}", "output", "-json"],
+        check=False,
+    )
+    if result.returncode != 0:
+        return {}
+    try:
+        return {k: v["value"] for k, v in json.loads(result.stdout).items()}
+    except (json.JSONDecodeError, KeyError, TypeError):
+        return {}
+
+
+def resolve_maas(ctx: Config, required: bool = True) -> None:
+    """Fill the MAAS connection details (and the client SSH host) from terraform outputs."""
+    out = module_outputs(ctx, "maas")
+    if "maas_url" not in out or "maas_api_key" not in out:
+        if required:
+            raise RuntimeError(
+                "MAAS terraform outputs not found; run `sandbed sunbeam maas up` first"
+            )
+        return
+    ctx.maas_url = out["maas_url"]
+    ctx.maas_token = out["maas_api_key"]
+    ctx.maas_admin_password = out.get("maas_admin_password", ctx.maas_admin_password)
+    if not ctx.ssh_host and out.get("client_mgmt_ip"):
+        ctx.ssh_host = out["client_mgmt_ip"]
+
+
+def apply_maas_config(ctx: Config) -> None:
+    _tf(ctx, "maas-config", ["init", "-backend=false"])
+    args = ["apply", "-auto-approve"]
+    if ctx.maas_parallelism:
+        args.append(f"-parallelism={ctx.maas_parallelism}")
+    _tf(ctx, "maas-config", args, _vars_for(ctx, "maas-config"), capture_output=False)
+
+
+def _forget_maas_config_state(ctx: Config) -> None:
+    # maas-config resources live inside the MAAS VM and disappear with it; destroying them
+    # through the API after the VM is gone would fail, so only drop the local state.
+    for name in ("terraform.tfstate", "terraform.tfstate.backup"):
+        path = os.path.join(ctx.terraform_dir, "maas-config", name)
+        if os.path.exists(path):
+            os.remove(path)
+
+
 def destroy_terraform(ctx: Config) -> None:
-    if ctx.provider in ("manual", "maas"):
-        _tf(ctx, ctx.provider, ["destroy", "-auto-approve"], _vars_for(ctx, ctx.provider))
+    if ctx.provider == "maas":
+        _tf(
+            ctx,
+            "maas",
+            ["destroy", "-auto-approve"],
+            _vars_for(ctx, "maas", _instance_extra(ctx)),
+            env=_maas_env(ctx),
+        )
+        _forget_maas_config_state(ctx)
+    elif ctx.provider == "manual":
+        _tf(ctx, "manual", ["destroy", "-auto-approve"], _vars_for(ctx, "manual"))
     _tf(ctx, "network", ["destroy", "-auto-approve"], _vars_for(ctx, "network"))
```

- [ ] **Step 4: Run the whole suite**

Run: `PYTHONPATH=/tmp/opencode/deps python3 -m pytest tests -q -p no:cacheprovider`
Expected: all pass. `test_manual_apply_runs_network_then_manual` and `test_manual_destroy_unchanged` prove `manual` still sees identical commands and no `env`.

- [ ] **Step 5: Commit**

```bash
git add sandbed/terraform.py tests/test_terraform.py
git commit -m "feat(terraform): read MAAS outputs and apply the maas-config root"
```

---

### Task 6: Preflight LXD address check and no host `maas` snap

**Files:**
- Modify: `sandbed/preflight.py`, `sandbed/install.py`
- Test: `tests/test_preflight.py`, `tests/test_install.py`

**Interfaces:**
- Produces: `preflight.lxd_https_address(ctx) -> str` (first host of `generic_cidr` + `:` + `lxd_https_port`, e.g. `172.16.1.1:8443`). `check(ctx)` for `maas` fails when LXD `core.https_address` is set to a different value; unset or equal passes. For `manual` no `lxc config get` call is made.

- [ ] **Step 1: Add the tests**

```diff
--- a/tests/test_preflight.py
+++ b/tests/test_preflight.py
@@ -20,4 +20,49 @@
 @patch("sandbed.preflight.os.path.exists", return_value=False)
 def test_check_no_kvm(fake_exists) -> None:
     with pytest.raises(PreflightError):
-        check(load_config("manual", "up"))
\ No newline at end of file
+        check(load_config("manual", "up"))
+
+class _Out:
+    def __init__(self, stdout: str = "", returncode: int = 0) -> None:
+        self.stdout = stdout
+        self.returncode = returncode
+
+
+def _lxc_https(current: str):
+    def fake(cmd, **_kwargs):
+        return _Out(current if cmd[:3] == ["lxc", "config", "get"] else "")
+
+    return fake
+
+
+@patch("sandbed.preflight.os.path.exists", return_value=True)
+def test_maas_preflight_accepts_unset_https_address(_exists) -> None:
+    with patch("sandbed.preflight.run", side_effect=_lxc_https("")):
+        check(load_config("maas", "up"))
+
+
+@patch("sandbed.preflight.os.path.exists", return_value=True)
+def test_maas_preflight_accepts_matching_https_address(_exists) -> None:
+    with patch("sandbed.preflight.run", side_effect=_lxc_https("172.16.1.1:8443")):
+        check(load_config("maas", "up"))
+
+
+@patch("sandbed.preflight.os.path.exists", return_value=True)
+def test_maas_preflight_rejects_foreign_https_address(_exists) -> None:
+    with patch("sandbed.preflight.run", side_effect=_lxc_https("[::]:8443")):
+        with pytest.raises(PreflightError, match="core.https_address"):
+            check(load_config("maas", "up"))
+
+
+@patch("sandbed.preflight.os.path.exists", return_value=True)
+def test_maas_https_address_follows_generic_cidr_and_port(_exists) -> None:
+    ctx = load_config("maas", "up", generic_cidr="10.9.0.0/24", lxd_https_port=9443)
+    with patch("sandbed.preflight.run", side_effect=_lxc_https("10.9.0.1:9443")):
+        check(ctx)
+
+
+@patch("sandbed.preflight.os.path.exists", return_value=True)
+def test_manual_preflight_ignores_https_address(_exists) -> None:
+    with patch("sandbed.preflight.run", side_effect=_lxc_https("[::]:8443")) as fake_run:
+        check(load_config("manual", "up"))
+    assert not any(c.args[0][:3] == ["lxc", "config", "get"] for c in fake_run.call_args_list)
```

```diff
--- a/tests/test_install.py
+++ b/tests/test_install.py
@@ -60,4 +60,14 @@
 )
 def test_verify_ceph_no_osd_raises(fake_run) -> None:
     with pytest.raises(PhaseError):
-        verify_ceph(load_config("manual", "up"))
\ No newline at end of file
+        verify_ceph(load_config("manual", "up"))
+
+@patch("sandbed.install.verify_ceph")
+@patch("sandbed.install._installed", return_value=False)
+@patch("sandbed.install.run")
+def test_install_maas_does_not_install_host_maas_snap(fake_run, _installed, _verify) -> None:
+    fake_run.return_value = _Out("/dev/loop9")
+    install(load_config("maas", "up"))
+    cmds = [c.args[0] for c in fake_run.call_args_list]
+    assert ["sudo", "snap", "install", "openstack"] in cmds
+    assert ["sudo", "snap", "install", "maas"] not in cmds
```

- [ ] **Step 2: Run them, expect failure**

Run: `PYTHONPATH=/tmp/opencode/deps python3 -m pytest tests/test_preflight.py tests/test_install.py -q -p no:cacheprovider`
Expected: FAIL (`DID NOT RAISE PreflightError` for the foreign address; install test asserts `["sudo","snap","install","maas"] not in cmds`).

- [ ] **Step 3: Implement**

```diff
--- a/sandbed/preflight.py
+++ b/sandbed/preflight.py
@@ -1,5 +1,6 @@
 from __future__ import annotations
 
+import ipaddress
 import os
 
 from .config import Config
@@ -16,6 +17,23 @@
     return os.path.exists("/dev/kvm")
 
 
+def lxd_https_address(ctx: Config) -> str:
+    """Address LXD must listen on so MAAS can power-drive the VMs: <generic gateway>:<port>."""
+    gateway = next(ipaddress.ip_network(ctx.generic_cidr).hosts())
+    return f"{gateway}:{ctx.lxd_https_port}"
+
+
+def _check_lxd_https(ctx: Config) -> None:
+    expected = lxd_https_address(ctx)
+    result = run(["lxc", "config", "get", "core.https_address"], check=False)
+    current = (result.stdout or "").strip()
+    if current and current != expected:
+        raise PreflightError(
+            f"LXD core.https_address is already set to {current}",
+            f"unset it (`lxc config unset core.https_address`) or set it to {expected}",
+        )
+
+
 def check(ctx: Config) -> None:
     if not _kvm_present():
         raise PreflightError("no /dev/kvm", "enable nested virtualization / KVM on the host")
@@ -23,6 +41,8 @@
         raise PreflightError("LXD not reachable", "run `snap install lxd && lxd init --auto`")
     if run(["sudo", "microceph", "cluster", "list"], check=False).returncode != 0:
         raise PreflightError("microceph not reachable", "snap install microceph + bootstrap")
+    if ctx.provider == "maas":
+        _check_lxd_https(ctx)
     if ctx.shared_dir and not os.path.isdir(ctx.shared_dir):
         raise PreflightError(
             f"shared dir not found: {ctx.shared_dir}",
```

```diff
--- a/sandbed/install.py
+++ b/sandbed/install.py
@@ -61,5 +61,3 @@
         run(["sudo", "apt-get", "install", "-y", "terraform"])
     if not _installed(["snap", "list", "openstack"]):
         run(["sudo", "snap", "install", "openstack"])
-    if ctx.provider == "maas" and not _installed(["maas", "--version"]):
-        run(["sudo", "snap", "install", "maas"])  # CLI only; region/rack assumed external
\ No newline at end of file
```

- [ ] **Step 4: Run the whole suite**

Run: `PYTHONPATH=/tmp/opencode/deps python3 -m pytest tests -q -p no:cacheprovider`
Expected: all pass (`test_manual_preflight_checks_lxd_and_microceph_only` still sees exactly two commands).

- [ ] **Step 5: Commit**

```bash
git add sandbed/preflight.py sandbed/install.py tests/test_preflight.py tests/test_install.py
git commit -m "feat(preflight): guard LXD core.https_address for the maas provider"
```

---

### Task 7: CLI flags, removal of the external-MAAS flags, MAAS resolve step

**Files:**
- Modify: `sandbed/cli.py`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `terraform.resolve_maas(ctx)` (Task 5).
- Produces: flags `--maas-channel`, `--maas-vcpus`, `--maas-memory`, `--maas-disk`, `--maas-admin-password`, `--maas-deploy-series`, `--maas-image-sync-timeout` (validated `<N>m`/`<N>h`), `--maas-upstream-dns`, `--maas-parallelism`, `--lxd-https-port` (the docs already listed it but no flag existed). Removed: `--maas-url`, `--maas-token`. For `maas`, `terraform.resolve_maas(ctx)` runs right before the `maas-setup` and `sunbeam` phases (before their `.pre` hooks), never for `manual`. `plan` for `maas` prints `note: maas-config is not planned; it needs a running MAAS`.

Note the old test `test_cli_maas_requires_credentials` is replaced: with credentials no longer required it would otherwise run the real phases.

- [ ] **Step 1: Update the tests**

```diff
--- a/tests/test_cli.py
+++ b/tests/test_cli.py
@@ -1,8 +1,9 @@
 from unittest.mock import patch
 
+import pytest
 from click.testing import CliRunner
 
-from sandbed.cli import cli
+from sandbed.cli import PHASES, cli
 
 
 @patch("sandbed.terraform.run")
@@ -13,10 +14,46 @@
     assert result.exit_code == 0
 
 
-def test_cli_maas_requires_credentials() -> None:
-    result = CliRunner().invoke(cli, ["sunbeam", "maas", "up"])
-    assert result.exit_code != 0
-    assert "requires" in result.output.lower() or result.exception is not None
+def test_cli_maas_up_needs_no_credentials(tmp_path, monkeypatch) -> None:
+    monkeypatch.setenv("HOME", str(tmp_path))
+    with patch("sandbed.cli._dispatch") as dispatch, patch("sandbed.cli.terraform") as tf:
+        result = CliRunner().invoke(cli, ["sunbeam", "maas", "up", "--no-hooks"])
+    assert result.exit_code == 0, result.output
+    assert [c.args[1] for c in dispatch.call_args_list] == PHASES
+    # the MAAS connection is read from terraform outputs right before the phases that need it
+    assert tf.resolve_maas.call_count == 2
+
+
+def test_cli_maas_sunbeam_action_resolves_maas(tmp_path, monkeypatch) -> None:
+    monkeypatch.setenv("HOME", str(tmp_path))
+    with patch("sandbed.cli._dispatch"), patch("sandbed.cli.terraform") as tf:
+        result = CliRunner().invoke(cli, ["sunbeam", "maas", "sunbeam", "--no-hooks"])
+    assert result.exit_code == 0, result.output
+    tf.resolve_maas.assert_called_once()
+
+
+@pytest.mark.parametrize("flag", ["--maas-url", "--maas-token"])
+def test_cli_external_maas_flags_removed(flag) -> None:
+    result = CliRunner().invoke(cli, ["sunbeam", "maas", "plan", flag, "x"])
+    assert result.exit_code == 2
+    assert "No such option" in result.output
+
+
+def test_cli_rejects_bad_image_sync_timeout() -> None:
+    result = CliRunner().invoke(
+        cli, ["sunbeam", "maas", "plan", "--maas-image-sync-timeout", "soon"]
+    )
+    assert result.exit_code == 2
+    assert "<N>m or <N>h" in result.output
+
+
+@patch("sandbed.terraform.run")
+def test_cli_plan_maas_notes_that_maas_config_is_skipped(fake_run) -> None:
+    fake_run.return_value.stdout = ""
+    fake_run.return_value.returncode = 0
+    result = CliRunner().invoke(cli, ["sunbeam", "maas", "plan"])
+    assert result.exit_code == 0, result.output
+    assert "maas-config is not planned" in result.output
 
 
 @patch("sandbed.testflinger.run_", return_value=0)
@@ -93,4 +130,18 @@
         ],
     )
     assert result.exit_code == 0
-    fake_run.assert_called_once()
\ No newline at end of file
+    fake_run.assert_called_once()
+
+def test_cli_lxd_https_port_flag(tmp_path, monkeypatch) -> None:
+    monkeypatch.setenv("HOME", str(tmp_path))
+    seen = {}
+
+    def fake_dispatch(ctx, phase):
+        seen["port"] = ctx.lxd_https_port
+
+    with patch("sandbed.cli._dispatch", side_effect=fake_dispatch), patch("sandbed.cli.terraform"):
+        result = CliRunner().invoke(
+            cli, ["sunbeam", "maas", "sunbeam", "--no-hooks", "--lxd-https-port", "9443"]
+        )
+    assert result.exit_code == 0, result.output
+    assert seen["port"] == 9443
```

- [ ] **Step 2: Run them, expect failure**

Run: `PYTHONPATH=/tmp/opencode/deps python3 -m pytest tests/test_cli.py -q -p no:cacheprovider`
Expected: FAIL (the new flags are unknown; `maas up` still demands credentials).

- [ ] **Step 3: Implement**

```diff
--- a/sandbed/cli.py
+++ b/sandbed/cli.py
@@ -1,5 +1,6 @@
 from __future__ import annotations
 
+import re
 import sys
 
 import click
@@ -15,6 +16,14 @@
 from .sunbeam import deploy_sunbeam
 
 PHASES = ["preflight", "install-tools", "terraform", "maas-setup", "sunbeam"]
+# Phases that need the MAAS URL/token/client host, which only exist once `terraform` has run.
+MAAS_RESOLVED_PHASES = ("maas-setup", "sunbeam")
+
+
+def _timeout(_ctx: click.Context, _param: click.Parameter, value: str | None) -> str | None:
+    if value is not None and not re.fullmatch(r"\d+[mh]", value):
+        raise click.BadParameter("use <N>m or <N>h, e.g. 60m or 2h")
+    return value
 
 
 CONTEXT_SETTINGS = {"help_option_names": ["-h", "--help"]}
@@ -43,9 +52,17 @@
 @click.option("--ssh-key", envvar="SUNBEAM_SSH_KEY")
 @click.option("--ssh-host")
 @click.option("--maas-name")
-@click.option("--maas-url")
-@click.option("--maas-token")
 @click.option("--maas-space")
+@click.option("--maas-channel")
+@click.option("--maas-vcpus", type=int)
+@click.option("--maas-memory", type=int)
+@click.option("--maas-disk", type=int)
+@click.option("--maas-admin-password")
+@click.option("--maas-deploy-series")
+@click.option("--maas-image-sync-timeout", callback=_timeout)
+@click.option("--maas-upstream-dns")
+@click.option("--maas-parallelism", type=int)
+@click.option("--lxd-https-port", type=int)
 @click.option("--hooks-dir", default="hooks")
 @click.option("--no-hooks", is_flag=True)
 @click.option("--cloud-init-templates-dir")
@@ -74,11 +91,10 @@
 
     if action == "plan":
         terraform.plan(ctx, provider)
+        if provider == "maas":
+            click.echo("note: maas-config is not planned; it needs a running MAAS")
         return
 
-    if action == "up" and provider == "maas" and not (ctx.maas_url and ctx.maas_token):
-        raise click.UsageError("maas provider requires --maas-url and --maas-token")
-
     if action == "up":
         steps = PHASES
     elif action == "sunbeam":
@@ -94,6 +110,8 @@
             if state.is_done(phase):
                 click.echo(f"skip {phase} (already done)")
                 continue
+            if provider == "maas" and phase in MAAS_RESOLVED_PHASES:
+                terraform.resolve_maas(ctx)
             if not ctx.no_hooks:
                 run_hook(f"{phase}.pre", ctx)
             try:
```

- [ ] **Step 4: Run the whole suite**

Run: `PYTHONPATH=/tmp/opencode/deps python3 -m pytest tests -q -p no:cacheprovider`
Expected: all pass. `test_manual_up_runs_every_phase_and_never_resolves_maas` confirms `manual up` is unchanged.

- [ ] **Step 5: Commit**

```bash
git add sandbed/cli.py tests/test_cli.py
git commit -m "feat(cli): managed MAAS flags; drop --maas-url and --maas-token"
```

---

### Task 8: `maas-setup` applies `maas-config`; series templating; destroy finds the client

**Files:**
- Rewrite: `sandbed/maas.py`
- Modify: `sandbed/sunbeam.py`, `sandbed/destroy.py`, `manifests/maas.yaml.j2`
- Test: `tests/test_maas.py` (rewrite), `tests/test_sunbeam.py`

**Interfaces:**
- Consumes: `terraform.resolve_maas`, `terraform.apply_maas_config` (Task 5).
- Produces: `maas.setup_maas(ctx)` resolves, applies `maas-config`, prints `MAAS <url>  user: admin  password: <pw>` and a warning while the password is still `changeme`. `deploy_sunbeam` passes `deploy_series` to the manifest renderer. `destroy(ctx)` calls `resolve_maas(ctx, required=False)` for `maas` so the client host is known for the Sunbeam teardown.

The old `_ensure_maas_machine`, `_tag`, `CORE_INSTANCES` and every `maas ...` CLI call are deleted; their job moves to `terraform/maas-config/`.

- [ ] **Step 1: Write the tests**

`tests/test_maas.py` (replace the file):

```python
from unittest.mock import patch

from sandbed.config import load_config
from sandbed.maas import setup_maas


@patch("sandbed.maas.console")
@patch("sandbed.maas.terraform")
def test_setup_maas_resolves_then_applies_maas_config(fake_tf, _console) -> None:
    ctx = load_config("maas", "up")
    setup_maas(ctx)
    assert [c[0] for c in fake_tf.method_calls] == ["resolve_maas", "apply_maas_config"]
    fake_tf.resolve_maas.assert_called_once_with(ctx)
    fake_tf.apply_maas_config.assert_called_once_with(ctx)


@patch("sandbed.maas.console")
@patch("sandbed.maas.terraform")
def test_setup_maas_prints_login_and_warns_on_default_password(fake_tf, fake_console) -> None:
    ctx = load_config("maas", "up")
    ctx.maas_url = "http://172.16.1.2:5240/MAAS"
    setup_maas(ctx)
    printed = " ".join(str(c.args[0]) for c in fake_console.print.call_args_list)
    assert "http://172.16.1.2:5240/MAAS" in printed
    assert "admin" in printed
    assert "default" in printed


@patch("sandbed.maas.console")
@patch("sandbed.maas.terraform")
def test_setup_maas_no_warning_for_custom_password(fake_tf, fake_console) -> None:
    ctx = load_config("maas", "up", maas_admin_password="s3cret")
    setup_maas(ctx)
    printed = " ".join(str(c.args[0]) for c in fake_console.print.call_args_list)
    assert "warning" not in printed
```

`tests/test_sunbeam.py`:

```diff
--- a/tests/test_sunbeam.py
+++ b/tests/test_sunbeam.py
@@ -12,4 +12,18 @@
     deploy_sunbeam(ctx)
     all_cmds = [" ".join(c[0][0]) for c in fake_run.call_args_list]
     assert any("cluster bootstrap" in c for c in all_cmds)
-    assert any("prepare-node-script --bootstrap" in c for c in all_cmds)
\ No newline at end of file
+    assert any("prepare-node-script --bootstrap" in c for c in all_cmds)
+
+@patch("sandbed.sunbeam.render", return_value="MANIFEST")
+@patch("sandbed.sunbeam.run")
+def test_maas_flow_uses_resolved_connection_and_series(fake_run, fake_render) -> None:
+    ctx = load_config("maas", "sunbeam", maas_deploy_series="jammy")
+    ctx.ssh_host = "192.168.200.7"
+    ctx.maas_url = "http://172.16.1.2:5240/MAAS"
+    ctx.maas_token = "ck:tk:sk"
+    deploy_sunbeam(ctx)
+    cmds = [c.args[0][-1] for c in fake_run.call_args_list]
+    assert "sunbeam deployment add maas sandbed ck:tk:sk http://172.16.1.2:5240/MAAS" in cmds
+    render_ctx = fake_render.call_args.args[1]
+    assert render_ctx["deploy_series"] == "jammy"
+    assert render_ctx["maas_url"] == "http://172.16.1.2:5240/MAAS"
```

- [ ] **Step 2: Run them, expect failure**

Run: `PYTHONPATH=/tmp/opencode/deps python3 -m pytest tests/test_maas.py tests/test_sunbeam.py -q -p no:cacheprovider`
Expected: FAIL (`AttributeError: <module 'sandbed.maas'> does not have the attribute 'terraform'`; `KeyError: 'deploy_series'`).

- [ ] **Step 3: Implement**

`sandbed/maas.py` (replace the file):

```python
from __future__ import annotations

from . import terraform
from .config import Config
from .console import console


def setup_maas(ctx: Config) -> None:
    """Configure the freshly deployed MAAS (images, network, machines, tags) via terraform."""
    terraform.resolve_maas(ctx)
    terraform.apply_maas_config(ctx)
    console.print(
        f"[bold]MAAS[/bold] {ctx.maas_url}  user: admin  password: {ctx.maas_admin_password}"
    )
    if ctx.maas_admin_password == "changeme":
        console.print(
            "[yellow]warning: MAAS admin password is the default; "
            "set --maas-admin-password[/yellow]"
        )
```

```diff
--- a/sandbed/sunbeam.py
+++ b/sandbed/sunbeam.py
@@ -23,6 +23,7 @@
             "deployment_name": ctx.maas_name,
             "maas_url": ctx.maas_url,
             "maas_token": ctx.maas_token,
+            "deploy_series": ctx.maas_deploy_series,
         },
     )
 
```

```diff
--- a/sandbed/destroy.py
+++ b/sandbed/destroy.py
@@ -2,11 +2,13 @@
 
 from .config import Config
 from .sunbeam import sunbeam_destroy
-from .terraform import destroy_terraform
+from .terraform import destroy_terraform, resolve_maas
 
 
 def destroy(ctx: Config) -> None:
     print("WARNING: sunbeam teardown must precede VM deletion.")
+    if ctx.provider == "maas":
+        resolve_maas(ctx, required=False)  # finds the client VM to run the teardown on
     try:
         sunbeam_destroy(ctx)
     except Exception:
```

```diff
--- a/manifests/maas.yaml.j2
+++ b/manifests/maas.yaml.j2
@@ -7,7 +7,7 @@
   software:
     juju:
       bootstrap:
-        series: noble
+        series: {{ deploy_series }}
 deployment:
   name: "{{ deployment_name }}"
   provider: maas
```

`manifests/manual.yaml.j2` is intentionally left alone (its `series: noble` stays hard-coded).

- [ ] **Step 4: Run the whole suite and the line-length check**

Run: `PYTHONPATH=/tmp/opencode/deps python3 -m pytest tests -q -p no:cacheprovider && awk 'length>100{print FILENAME":"FNR}' sandbed/*.py tests/*.py`
Expected: all pass (89 tests in total) and no line-length output.

- [ ] **Step 5: Commit**

```bash
git add sandbed/maas.py sandbed/sunbeam.py sandbed/destroy.py manifests/maas.yaml.j2 tests/test_maas.py tests/test_sunbeam.py
git commit -m "feat(maas): configure MAAS through the maas-config terraform root"
```

---

### Task 9: `terraform/maas` root (LXD side of managed MAAS)

Rewrites the module so that: every MAAS-managed node (`cloud-*`, `sunbeam-controller-*`, `juju-controller-*`, `observability-*`, `landscape-*`) is an empty, stopped PXE VM on `generic` with fixed MACs; `maas-1` runs MAAS; `sunbeam-client-1` is the only other cloud-init VM; LXD listens on the `generic` gateway and trusts a Terraform-generated client certificate.

Breaking change: resource addresses change (`lxd_instance.cloud` and `lxd_instance.provisioned` become `lxd_instance.pxe`, `.client`, `.maas`; the sdb volumes merge into one `for_each`). An existing `maas` deployment must be destroyed before applying this.

**Files:**
- Modify: `terraform/maas/main.tf` (replace), `terraform/maas/variables.tf`, `terraform/maas/providers.tf`, `terraform/maas/outputs.tf` (replace)
- Create: `terraform/maas/nodes.tf`, `client.tf`, `controller.tf`, `cloud-init.tf`, `files/maas-bootstrap.sh`, `files/maas-wait-rack-images.sh`, `files/maas-tag-nic.sh`, `cloud-init/network-config-maas.yaml.tpl`, `cloud-init/network-config-client.yaml.tpl`
- Unchanged: `terraform/maas/cloud-init/user-data.yaml.tpl`

**Interfaces (Terraform outputs consumed by `maas-config` and by Python):**
- `maas_url` (string), `maas_api_key` (sensitive), `maas_admin_password` (sensitive, also a variable defaulting to `changeme`), `maas_instance` (`maas-1`), `lxd_power_address` (`<gateway>:8443`), `lxd_power_cert` / `lxd_power_key` (sensitive), `machines` (map name -> `{role, generic_mac, external_mac|null}`), `client_name`, `client_mgmt_ip`.
- `maas-1` also holds `/usr/local/sbin/maas-bootstrap.sh`, `maas-wait-rack-images.sh <seconds>` and `maas-tag-nic.sh <hostname> <mac> <tag>`, which `maas-config` runs through `lxc exec`.

- [ ] **Step 1: Write the Terraform files**

`maas/providers.tf`:

```hcl
terraform {
  required_version = ">= 1.5.0"

  required_providers {
    lxd = {
      source  = "terraform-lxd/lxd"
      version = "~> 3.0"
    }
    tls = {
      source  = "hashicorp/tls"
      version = "~> 4.0"
    }
  }
}

provider "lxd" {
  remote {
    name    = "local"
    address = "unix://"
  }
}
```

`maas/main.tf`:

```hcl
# Networks: `generic` is the PXE network served by MAAS (LXD DHCP stays off); `external` is NAT'd.
resource "lxd_network" "generic" {
  name = var.generic_network_name
  type = "bridge"

  config = {
    "ipv4.address" = "${local.gateway_ip}/${local.generic_prefix}"
    "ipv4.nat"     = "true"
    "ipv4.dhcp"    = "false"
    "ipv6.address" = "none"
  }
}

resource "lxd_network" "external" {
  name = var.external_network_name
  type = "bridge"

  config = {
    "ipv4.address" = "${cidrhost(var.external_cidr, 1)}/${split("/", var.external_cidr)[1]}"
    "ipv4.nat"     = "true"
    "ipv4.dhcp"    = "true"
    "ipv6.address" = "none"
  }
}

locals {
  generic_prefix = split("/", var.generic_cidr)[1]
  gateway_ip     = cidrhost(var.generic_cidr, 1)
  maas_ip        = cidrhost(var.generic_cidr, 2)
  client_ip      = cidrhost(var.generic_cidr, 3)
  maas_url       = "http://${local.maas_ip}:5240/MAAS"
}
```

`maas/nodes.tf`:

```hcl
# Every node MAAS manages is an empty VM that PXE-boots off `generic`. They stay stopped until
# MAAS powers them on through its `lxd` power driver. MACs are fixed so MAAS can be told about
# each machine before it ever runs.
locals {
  groups = {
    "cloud"              = { names = [for i in range(3) : "cloud-${i + 1}"], base = 0 }
    "sunbeam-controller" = { names = [for i in range(3) : "sunbeam-controller-${i + 1}"], base = 16 }
    "juju-controller"    = { names = [for i in range(3) : "juju-controller-${i + 1}"], base = 32 }
    "observability"      = { names = [for i in range(var.obs_count) : "observability-${i + 1}"], base = 48 }
    "landscape"          = { names = [for i in range(var.landscape_count) : "landscape-${i + 1}"], base = 64 }
  }

  pxe_nodes = merge([
    for role, group in local.groups : {
      for i, name in group.names : name => {
        role         = role
        generic_mac  = format("00:16:3e:5a:01:%02x", group.base + i)
        external_mac = format("00:16:3e:5a:02:%02x", group.base + i)
        external     = role == "cloud"
      }
    }
  ]...)
}

resource "lxd_storage_volume" "sdb" {
  for_each     = local.pxe_nodes
  name         = "${each.key}-sdb"
  pool         = var.ceph_pool_name
  content_type = "block"

  config = {
    size = var.sdb_size
  }
}

resource "lxd_instance" "pxe" {
  for_each = local.pxe_nodes
  name     = each.key
  type     = "virtual-machine"
  running  = false

  config = {
    "limits.cpu"          = tostring(var.vcpus)
    "limits.memory"       = "${var.memory}MiB"
    "boot.autostart"      = "false"
    "security.secureboot" = "false"
  }

  device {
    name = "root"
    type = "disk"
    properties = {
      path = "/"
      pool = var.ceph_pool_name
      size = "${var.disk}GiB"
    }
  }

  device {
    name = "eth0"
    type = "nic"
    properties = {
      network         = lxd_network.generic.name
      hwaddr          = each.value.generic_mac
      "boot.priority" = "10"
    }
  }

  dynamic "device" {
    for_each = each.value.external ? [1] : []
    content {
      name = "eth1"
      type = "nic"
      properties = {
        network = lxd_network.external.name
        hwaddr  = each.value.external_mac
      }
    }
  }

  device {
    name = "sdb"
    type = "disk"
    properties = {
      pool   = var.ceph_pool_name
      source = lxd_storage_volume.sdb[each.key].name
    }
  }
}
```

`maas/cloud-init.tf`:

```hcl
# cloud-init is only used by the two VMs that are not enlisted in MAAS: maas-1 and the Sunbeam
# client. Both need static addressing on `generic` (nothing serves DHCP there until MAAS is up),
# so network-config always has an embedded fallback and `default.*` user templates are ignored
# for it; only a per-node `<node>.network-config.yaml.tpl` overrides it.
locals {
  maas_mac           = "00:16:3e:5a:00:02"
  client_generic_mac = "00:16:3e:5a:00:03"
  client_mgmt_mac    = "00:16:3e:5a:00:04"

  cloud_init_vars = {
    user           = var.vm_user
    ssh_key        = var.ssh_key
    shared_enabled = var.shared_dir != ""
    provider       = "maas"
  }

  cloud_init_nodes = {
    "maas-1" = {
      network_template = "${path.module}/cloud-init/network-config-maas.yaml.tpl"
      vars = {
        mac     = local.maas_mac
        address = local.maas_ip
        prefix  = local.generic_prefix
        gateway = local.gateway_ip
        dns     = var.upstream_dns
      }
    }
    "sunbeam-client-1" = {
      network_template = "${path.module}/cloud-init/network-config-client.yaml.tpl"
      vars = {
        mgmt_mac    = local.client_mgmt_mac
        generic_mac = local.client_generic_mac
        address     = local.client_ip
        prefix      = local.generic_prefix
      }
    }
  }

  cloud_init = {
    for name, node in local.cloud_init_nodes : name => {
      user_data = (
        fileexists("${var.cloud_init_templates_dir}/${name}.user-data.yaml.tpl")
        ? templatefile("${var.cloud_init_templates_dir}/${name}.user-data.yaml.tpl",
        merge(local.cloud_init_vars, { hostname = name }))
        : fileexists("${var.cloud_init_templates_dir}/default.user-data.yaml.tpl")
        ? templatefile("${var.cloud_init_templates_dir}/default.user-data.yaml.tpl",
        merge(local.cloud_init_vars, { hostname = name }))
        : templatefile("${path.module}/cloud-init/user-data.yaml.tpl",
        merge(local.cloud_init_vars, { hostname = name }))
      )
      network_config = templatefile(
        fileexists("${var.cloud_init_templates_dir}/${name}.network-config.yaml.tpl")
        ? "${var.cloud_init_templates_dir}/${name}.network-config.yaml.tpl"
        : node.network_template,
        merge(local.cloud_init_vars, node.vars, { hostname = name })
      )
    }
  }
}
```

`maas/client.tf`:

```hcl
# The Sunbeam client is the one machine that is not enlisted in MAAS. eth0 (`mgmt`) is for host
# access; eth1 (`generic`) keeps its MAAS and Juju traffic on the PXE network.
resource "lxd_instance" "client" {
  name  = "sunbeam-client-1"
  type  = "virtual-machine"
  image = var.image_fingerprint

  timeouts = {
    create = "10m"
  }

  config = {
    "limits.cpu"            = tostring(var.vcpus)
    "limits.memory"         = "${var.memory}MiB"
    "boot.autostart"        = "true"
    "user.access_interface" = "eth0"
    "user.user-data"        = local.cloud_init["sunbeam-client-1"].user_data
    "user.network-config"   = local.cloud_init["sunbeam-client-1"].network_config
  }

  device {
    name = "root"
    type = "disk"
    properties = {
      path = "/"
      pool = var.ceph_pool_name
      size = "${var.disk}GiB"
    }
  }

  device {
    name = "eth0"
    type = "nic"
    properties = {
      network = var.mgmt_network_name
      hwaddr  = local.client_mgmt_mac
    }
  }

  device {
    name = "eth1"
    type = "nic"
    properties = {
      network = lxd_network.generic.name
      hwaddr  = local.client_generic_mac
    }
  }

  dynamic "device" {
    for_each = var.shared_dir != "" ? [1] : []
    content {
      name = "shared"
      type = "disk"
      properties = {
        source = var.shared_dir
        path   = "/shared"
      }
    }
  }

  wait_for {
    type = "ipv4"
    nic  = "eth0"
  }
}
```

`maas/controller.tf`:

```hcl
# LXD side of MAAS power control: LXD listens on the `generic` gateway address and trusts a
# client certificate that MAAS presents through its `lxd` power driver.
resource "lxd_server" "https" {
  config = {
    "core.https_address" = "${local.gateway_ip}:${var.lxd_https_port}"
  }

  depends_on = [lxd_network.generic]
}

resource "tls_private_key" "lxd_power" {
  algorithm = "RSA"
  rsa_bits  = 4096
}

resource "tls_self_signed_cert" "lxd_power" {
  private_key_pem       = tls_private_key.lxd_power.private_key_pem
  validity_period_hours = 87600
  allowed_uses          = ["client_auth", "digital_signature", "key_encipherment"]

  subject {
    common_name = "sandbed-maas"
  }
}

resource "lxd_trust_certificate" "maas" {
  name    = "sandbed-maas"
  content = tls_self_signed_cert.lxd_power.cert_pem

  depends_on = [lxd_server.https]
}

# One machine runs the MAAS region and rack controllers; maas-test-db provides PostgreSQL.
resource "lxd_instance" "maas" {
  name  = "maas-1"
  type  = "virtual-machine"
  image = var.image_fingerprint

  timeouts = {
    create = "45m"
  }

  config = {
    "limits.cpu"          = tostring(var.maas_vcpus)
    "limits.memory"       = "${var.maas_memory}MiB"
    "boot.autostart"      = "true"
    "user.user-data"      = local.cloud_init["maas-1"].user_data
    "user.network-config" = local.cloud_init["maas-1"].network_config
  }

  device {
    name = "root"
    type = "disk"
    properties = {
      path = "/"
      pool = var.ceph_pool_name
      size = "${var.maas_disk}GiB"
    }
  }

  device {
    name = "eth0"
    type = "nic"
    properties = {
      network = lxd_network.generic.name
      hwaddr  = local.maas_mac
    }
  }

  dynamic "device" {
    for_each = var.shared_dir != "" ? [1] : []
    content {
      name = "shared"
      type = "disk"
      properties = {
        source = var.shared_dir
        path   = "/shared"
      }
    }
  }

  file {
    content            = file("${path.module}/files/maas-bootstrap.sh")
    target_path        = "/usr/local/sbin/maas-bootstrap.sh"
    mode               = "0755"
    create_directories = true
  }

  file {
    content            = file("${path.module}/files/maas-wait-rack-images.sh")
    target_path        = "/usr/local/sbin/maas-wait-rack-images.sh"
    mode               = "0755"
    create_directories = true
  }

  file {
    content            = file("${path.module}/files/maas-tag-nic.sh")
    target_path        = "/usr/local/sbin/maas-tag-nic.sh"
    mode               = "0755"
    create_directories = true
  }

  wait_for {
    type = "agent"
  }

  execs = {
    "10-bootstrap" = {
      command       = ["/usr/local/sbin/maas-bootstrap.sh"]
      trigger       = "once"
      fail_on_error = true
      environment = {
        MAAS_ADMIN_PASSWORD = var.maas_admin_password
        MAAS_CHANNEL        = var.maas_channel
        MAAS_URL            = local.maas_url
        MAAS_GENERIC_CIDR   = var.generic_cidr
      }
    }
    "20-apikey" = {
      command       = ["/snap/bin/maas", "apikey", "--username", "admin"]
      trigger       = "once"
      record_output = true
      fail_on_error = true
    }
  }

  depends_on = [lxd_network.generic]
}
```

`maas/outputs.tf`:

```hcl
output "maas_url" {
  value = local.maas_url
}

output "maas_api_key" {
  value     = trimspace(lxd_instance.maas.execs["20-apikey"].stdout)
  sensitive = true
}

output "maas_admin_password" {
  value     = var.maas_admin_password
  sensitive = true
}

output "maas_instance" {
  value = lxd_instance.maas.name
}

output "lxd_power_address" {
  value = "${local.gateway_ip}:${var.lxd_https_port}"
}

output "lxd_power_cert" {
  value     = tls_self_signed_cert.lxd_power.cert_pem
  sensitive = true
}

output "lxd_power_key" {
  value     = tls_private_key.lxd_power.private_key_pem
  sensitive = true
}

output "machines" {
  description = "Machines MAAS enlists: role and the fixed MACs of their NICs."
  value = {
    for name, node in local.pxe_nodes : name => {
      role         = node.role
      generic_mac  = node.generic_mac
      external_mac = node.external ? node.external_mac : null
    }
  }
}

output "client_name" {
  value = lxd_instance.client.name
}

output "client_mgmt_ip" {
  value = lxd_instance.client.ipv4_address
}
```

`maas/cloud-init/network-config-maas.yaml.tpl`:

```yaml
version: 2
ethernets:
  generic0:
    match:
      macaddress: "${mac}"
    set-name: generic0
    dhcp4: false
    dhcp6: false
    addresses:
      - ${address}/${prefix}
    routes:
      - to: default
        via: ${gateway}
    nameservers:
      addresses:
        - ${dns}
```

`maas/cloud-init/network-config-client.yaml.tpl`:

```yaml
version: 2
ethernets:
  mgmt0:
    match:
      macaddress: "${mgmt_mac}"
    set-name: mgmt0
    dhcp4: true
  generic0:
    match:
      macaddress: "${generic_mac}"
    set-name: generic0
    dhcp4: false
    dhcp6: false
    addresses:
      - ${address}/${prefix}
```

`terraform/maas/variables.tf` (keep every existing variable; add the validations and the new variables):

```diff
--- a/terraform/maas/variables.tf
+++ b/terraform/maas/variables.tf
@@ -21,11 +21,21 @@
 variable "obs_count" {
   type    = number
   default = 3
+
+  validation {
+    condition     = var.obs_count >= 0 && var.obs_count <= 16
+    error_message = "obs_count must be between 0 and 16."
+  }
 }
 
 variable "landscape_count" {
   type    = number
   default = 3
+
+  validation {
+    condition     = var.landscape_count >= 0 && var.landscape_count <= 16
+    error_message = "landscape_count must be between 0 and 16."
+  }
 }
 
 variable "mgmt_network_name" {
@@ -82,4 +92,39 @@
 variable "cloud_init_templates_dir" {
   type    = string
   default = "templates/cloud-init"
-}
\ No newline at end of file
+}
+variable "maas_vcpus" {
+  type    = number
+  default = 4
+}
+
+variable "maas_memory" {
+  type    = number
+  default = 8192
+}
+
+variable "maas_disk" {
+  type    = number
+  default = 50
+}
+
+variable "maas_channel" {
+  type    = string
+  default = "3.6/stable"
+}
+
+variable "maas_admin_password" {
+  type      = string
+  default   = "changeme"
+  sensitive = true
+}
+
+variable "upstream_dns" {
+  type    = string
+  default = "1.1.1.1"
+}
+
+variable "lxd_https_port" {
+  type    = number
+  default = 8443
+}
```

- [ ] **Step 2: Write the scripts that run inside `maas-1`**

`maas/files/maas-bootstrap.sh`:

```bash
#!/usr/bin/env bash
# Installs and initialises a single-node MAAS (region+rack, maas-test-db as the database), creates
# the admin user and logs in as profile `admin`. Idempotent. Runs inside maas-1, driven by
# terraform through the lxd_instance `execs` block.
#
# Required environment: MAAS_ADMIN_PASSWORD MAAS_CHANNEL MAAS_URL MAAS_GENERIC_CIDR
set -euo pipefail
export PATH="$PATH:/snap/bin"
export DEBIAN_FRONTEND=noninteractive

: "${MAAS_ADMIN_PASSWORD:?}" "${MAAS_CHANNEL:?}" "${MAAS_URL:?}" "${MAAS_GENERIC_CIDR:?}"

MARKER=/var/lib/sandbed/maas-initialised
log() { echo "[maas-bootstrap] $*"; }

wait_for() { # wait_for <seconds> <description> <command...>
    local deadline=$(($(date +%s) + $1)) description=$2
    shift 2
    until "$@" >/dev/null 2>&1; do
        if [ "$(date +%s)" -ge "$deadline" ]; then
            log "timed out waiting for ${description}" >&2
            return 1
        fi
        sleep 5
    done
}

log "waiting for cloud-init and snapd"
cloud-init status --wait >/dev/null 2>&1 || true # non-zero when degraded; not fatal here
snap wait system seed.loaded

if ! command -v jq >/dev/null; then
    apt-get update -qq
    apt-get install -y -qq jq
fi

snap list maas-test-db >/dev/null 2>&1 || snap install maas-test-db
snap list maas >/dev/null 2>&1 || snap install maas --channel="${MAAS_CHANNEL}"

if [ ! -f "${MARKER}" ]; then
    log "initialising MAAS (region+rack)"
    maas init region+rack --database-uri maas-test-db:/// --maas-url "${MAAS_URL}" --force
    mkdir -p "$(dirname "${MARKER}")"
    touch "${MARKER}"
fi

log "waiting for the MAAS API"
wait_for 600 "the MAAS API" curl -fsS "${MAAS_URL}/api/2.0/version/"

if ! maas apikey --username admin >/dev/null 2>&1; then
    log "creating the admin user"
    maas createadmin --username admin --password "${MAAS_ADMIN_PASSWORD}" \
        --email admin@sandbed.local
fi

maas login admin "${MAAS_URL}" "$(maas apikey --username admin)" >/dev/null

log "waiting for the rack controller and the generic subnet"
wait_for 900 "the rack controller" \
    bash -c "maas admin rack-controllers read | jq -e 'length > 0'"
wait_for 900 "subnet ${MAAS_GENERIC_CIDR}" \
    bash -c "maas admin subnets read | jq -e --arg c '${MAAS_GENERIC_CIDR}' 'any(.[]; .cidr == \$c)'"

log "done"
```

`maas/files/maas-wait-rack-images.sh`:

```bash
#!/usr/bin/env bash
# Blocks until the rack controller has synced its boot images from the region (the terraform
# maas provider only waits for the region side). Usage: maas-wait-rack-images.sh <timeout-seconds>
set -euo pipefail
export PATH="$PATH:/snap/bin"

timeout_seconds=${1:?usage: maas-wait-rack-images.sh <timeout-seconds>}
deadline=$(($(date +%s) + timeout_seconds))

system_id=$(maas admin rack-controllers read | jq -r '.[0].system_id')
while :; do
    status=$(maas admin rack-controller list-boot-images "${system_id}" | jq -r '.status')
    echo "[maas-wait-rack-images] rack ${system_id}: ${status}"
    [ "${status}" = "synced" ] && exit 0
    if [ "$(date +%s)" -ge "${deadline}" ]; then
        echo "[maas-wait-rack-images] timed out" >&2
        exit 1
    fi
    sleep 30
done
```

`maas/files/maas-tag-nic.sh`:

```bash
#!/usr/bin/env bash
# Tags the NIC with the given MAC on a MAAS machine and leaves it unconfigured (no IP), as Sunbeam
# expects for the external NIC. Idempotent.
# Usage: maas-tag-nic.sh <hostname> <mac-address> <tag>
set -euo pipefail
export PATH="$PATH:/snap/bin"

hostname=${1:?usage: maas-tag-nic.sh <hostname> <mac-address> <tag>}
mac=${2:?}
tag=${3:?}

system_id=$(maas admin machines read hostname="${hostname}" | jq -r '.[0].system_id')
interface=$(maas admin interfaces read "${system_id}" |
    jq -c --arg mac "${mac}" '.[] | select(.mac_address == $mac)')
[ -n "${interface}" ] || { echo "no interface ${mac} on ${hostname}" >&2; exit 1; }
interface_id=$(jq -r '.id' <<<"${interface}")

for link_id in $(jq -r '.links[].id' <<<"${interface}"); do
    maas admin interface unlink-subnet "${system_id}" "${interface_id}" id="${link_id}" >/dev/null
done
maas admin interface link-subnet "${system_id}" "${interface_id}" mode=LINK_UP >/dev/null
maas admin interface update "${system_id}" "${interface_id}" tags="${tag}" >/dev/null
echo "[maas-tag-nic] ${hostname} ${mac}: tagged ${tag}, unconfigured"
```

Make them executable: `chmod +x terraform/maas/files/*.sh`.

- [ ] **Step 3: Validate**

Run:
```bash
for f in terraform/maas/files/*.sh; do bash -n "$f"; done
terraform -chdir=terraform/maas init -backend=false
terraform -chdir=terraform/maas validate
terraform -chdir=terraform/maas fmt -check
```
Expected: `Success! The configuration is valid.`, `fmt` exits 0, no `bash -n` output. (`init` downloads `terraform-lxd/lxd` and `hashicorp/tls`.)

- [ ] **Step 4: Commit**

```bash
git add terraform/maas
git commit -m "feat(terraform): deploy MAAS in an LXD VM and enlist every node but the client"
```

---

### Task 10: `terraform/maas-config` root (MAAS configuration via the `maas` provider)

Applied by the `maas-setup` phase after `maas` is up. It reads `../maas/terraform.tfstate` (`terraform_remote_state`, local backend; the repo runs `init -backend=false`, which uses the default local state file), so no secret goes through the command line.

What it manages, in dependency order: server settings and SSH key; the image selection (the provider blocks until every selected image is fully synced, bounded by `image_sync_timeout`); a rack-side image gate; the `generic` fabric/VLAN/subnet (imported, then given space, gateway and DNS); dynamic and reserved ranges; DHCP on the VLAN served by the rack controller; one `maas_machine` per node (commissioned to `Ready`); role tags; the `ceph` tag on `/dev/sdb` of the cloud nodes; and the `neutron:physnet1` tag with no IP on the cloud nodes' external NIC (the provider cannot look a NIC up by MAC, so `maas-tag-nic.sh` does that part through the MAAS CLI inside `maas-1`).

**Files:**
- Create: `terraform/maas-config/providers.tf`, `variables.tf`, `main.tf`, `network.tf`, `machines.tf`

**Interfaces:**
- Consumes: the `maas` root outputs from Task 9 (`maas_url`, `maas_api_key`, `maas_instance`, `lxd_power_address`, `lxd_power_cert`, `lxd_power_key`, `machines`).
- Consumes (variables passed by Python in `apply_maas_config`): `deploy_series, maas_name, maas_space, generic_cidr, ssh_key, upstream_dns, image_sync_timeout`. The others have defaults.

- [ ] **Step 1: Write the files**

`maas-config/providers.tf`:

```hcl
terraform {
  # `import` blocks with an expression id need Terraform >= 1.6.
  required_version = ">= 1.6.0"

  required_providers {
    maas = {
      source  = "canonical/maas"
      version = "~> 2.10"
    }
  }
}

# Connection details, machines and LXD power credentials come straight from the `maas` root's
# local state, so no secret has to travel through the command line.
data "terraform_remote_state" "maas" {
  backend = "local"

  config = {
    path = "${path.module}/../maas/terraform.tfstate"
  }
}

locals {
  deployed = data.terraform_remote_state.maas.outputs
}

provider "maas" {
  api_url             = local.deployed.maas_url
  api_key             = local.deployed.maas_api_key
  api_version         = "2.0"
  installation_method = "snap"
}
```

`maas-config/variables.tf`:

```hcl
variable "deploy_series" {
  description = "Ubuntu series MAAS deploys on the machines."
  type        = string
  default     = "noble"
}

variable "maas_name" {
  description = "Sunbeam deployment name; machines get the openstack-<name> tag."
  type        = string
  default     = "sandbed"
}

variable "maas_space" {
  type    = string
  default = "myspace"
}

variable "generic_cidr" {
  type    = string
  default = "172.16.1.0/24"
}

variable "generic_fabric" {
  description = "Name MAAS gave the fabric it discovered for the generic network."
  type        = string
  default     = "fabric-0"
}

variable "generic_vid" {
  type    = number
  default = 0
}

variable "ssh_key" {
  type = string
}

variable "upstream_dns" {
  type    = string
  default = "1.1.1.1"
}

variable "lxd_project" {
  type    = string
  default = "default"
}

variable "image_sync_timeout" {
  description = "How long to wait for boot images to sync, as <N>m or <N>h."
  type        = string
  default     = "60m"

  validation {
    condition     = can(regex("^[0-9]+[mh]$", var.image_sync_timeout))
    error_message = "image_sync_timeout must look like 60m or 2h."
  }
}

variable "machine_timeout" {
  description = "How long each machine may take to commission."
  type        = string
  default     = "45m"
}

variable "role_tags" {
  description = "MAAS machine tags per role, besides openstack-<maas_name>."
  type        = map(list(string))
  default = {
    "cloud"              = ["control", "compute", "storage"]
    "sunbeam-controller" = ["sunbeam"]
    "juju-controller"    = ["juju-controller"]
    "observability"      = ["observability"]
    "landscape"          = ["landscape"]
  }
}

variable "deployment_roles" {
  description = "Roles Sunbeam deploys onto; they get the openstack-<maas_name> tag."
  type        = list(string)
  default     = ["cloud", "sunbeam-controller", "juju-controller"]
}
```

`maas-config/main.tf`:

```hcl
locals {
  sync_unit    = substr(var.image_sync_timeout, length(var.image_sync_timeout) - 1, 1)
  sync_amount  = tonumber(substr(var.image_sync_timeout, 0, length(var.image_sync_timeout) - 1))
  sync_seconds = local.sync_amount * (local.sync_unit == "h" ? 3600 : 60)

  gateway_ip = cidrhost(var.generic_cidr, 1)
  maas_ip    = cidrhost(var.generic_cidr, 2)
}

# --- server settings -------------------------------------------------------------------------

resource "maas_configuration" "default_osystem" {
  key   = "default_osystem"
  value = "ubuntu"
}

resource "maas_configuration" "default_distro_series" {
  key   = "default_distro_series"
  value = var.deploy_series
}

resource "maas_configuration" "upstream_dns" {
  key   = "upstream_dns"
  value = var.upstream_dns
}

resource "maas_ssh_keys" "admin" {
  keys = [var.ssh_key]
}

# --- images: blocks until every selected image has synced -------------------------------------

data "maas_boot_source" "default" {}

data "maas_configuration" "commissioning_series" {
  key = "commissioning_distro_series"
}

resource "maas_boot_source_selection" "deploy" {
  boot_source = tonumber(data.maas_boot_source.default.id)
  os          = "ubuntu"
  release     = var.deploy_series
  arches      = ["amd64"]

  timeouts {
    create = var.image_sync_timeout
    update = var.image_sync_timeout
  }
}

# The commissioning image is selected by MAAS itself; wait for it too when it is a different series.
resource "maas_boot_source_selection" "commissioning" {
  count = data.maas_configuration.commissioning_series.value == var.deploy_series ? 0 : 1

  boot_source = tonumber(data.maas_boot_source.default.id)
  os          = "ubuntu"
  release     = data.maas_configuration.commissioning_series.value
  arches      = ["amd64"]

  timeouts {
    create = var.image_sync_timeout
    update = var.image_sync_timeout
  }
}

# The provider only waits for the region; the rack controller must have the images before it
# can PXE-boot machines.
resource "terraform_data" "rack_images_synced" {
  provisioner "local-exec" {
    command = "lxc exec ${local.deployed.maas_instance} -- /usr/local/sbin/maas-wait-rack-images.sh ${local.sync_seconds}"
  }

  depends_on = [
    maas_boot_source_selection.deploy,
    maas_boot_source_selection.commissioning,
  ]
}
```

`maas-config/network.tf`:

```hcl
# The rack controller discovers the generic fabric, VLAN and subnet by itself; import them so they
# can be configured (space, gateway, DNS) instead of colliding with the discovered objects.
data "maas_fabric" "generic" {
  name = var.generic_fabric
}

import {
  to = maas_vlan.generic
  id = "${var.generic_fabric}:${var.generic_vid}"
}

import {
  to = maas_subnet.generic
  id = var.generic_cidr
}

resource "maas_space" "sunbeam" {
  name = var.maas_space
}

resource "maas_vlan" "generic" {
  fabric = data.maas_fabric.generic.id
  vid    = var.generic_vid
  space  = maas_space.sunbeam.name
}

resource "maas_subnet" "generic" {
  cidr        = var.generic_cidr
  name        = "generic"
  gateway_ip  = local.gateway_ip
  dns_servers = [local.maas_ip]
}

resource "maas_subnet_ip_range" "dynamic" {
  subnet   = maas_subnet.generic.id
  type     = "dynamic"
  start_ip = cidrhost(var.generic_cidr, 100)
  end_ip   = cidrhost(var.generic_cidr, 200)
  comment  = "sandbed dynamic range (enlist and commission)"
}

resource "maas_subnet_ip_range" "internal_api" {
  subnet   = maas_subnet.generic.id
  type     = "reserved"
  start_ip = cidrhost(var.generic_cidr, 20)
  end_ip   = cidrhost(var.generic_cidr, 39)
  comment  = "${var.maas_name}-internal-api"
}

resource "maas_subnet_ip_range" "public_api" {
  subnet   = maas_subnet.generic.id
  type     = "reserved"
  start_ip = cidrhost(var.generic_cidr, 40)
  end_ip   = cidrhost(var.generic_cidr, 59)
  comment  = "${var.maas_name}-public-api"
}

data "maas_rack_controller" "maas" {
  hostname = local.deployed.maas_instance
}

resource "maas_vlan_dhcp" "generic" {
  fabric                  = tonumber(data.maas_fabric.generic.id)
  vlan                    = var.generic_vid
  primary_rack_controller = data.maas_rack_controller.maas.id
  ip_ranges               = [maas_subnet_ip_range.dynamic.id]

  depends_on = [maas_vlan.generic, maas_subnet.generic]
}
```

`maas-config/machines.tf`:

```hcl
locals {
  machines = local.deployed.machines

  node_tags = {
    for name, machine in local.machines : name => concat(
      contains(var.deployment_roles, machine.role) ? ["openstack-${var.maas_name}"] : [],
      var.role_tags[machine.role],
    )
  }

  tag_names = toset(flatten(values(local.node_tags)))

  cloud_machines = { for name, machine in local.machines : name => machine if machine.role == "cloud" }
}

# Adding a machine commissions it (MAAS powers the VM on over the lxd driver) and waits for Ready.
resource "maas_machine" "node" {
  for_each = local.machines

  hostname        = each.key
  architecture    = "amd64/generic"
  power_type      = "lxd"
  pxe_mac_address = each.value.generic_mac
  power_parameters = jsonencode({
    power_address = local.deployed.lxd_power_address
    instance_name = each.key
    project       = var.lxd_project
    certificate   = local.deployed.lxd_power_cert
    key           = local.deployed.lxd_power_key
  })

  timeouts {
    create = var.machine_timeout
  }

  depends_on = [
    maas_vlan_dhcp.generic,
    maas_ssh_keys.admin,
    maas_configuration.default_distro_series,
    terraform_data.rack_images_synced,
  ]
}

resource "maas_tag" "role" {
  for_each = local.tag_names

  name     = each.key
  machines = [for name, tags in local.node_tags : maas_machine.node[name].id if contains(tags, each.key)]
}

# Ceph OSD disk: /dev/sdb on the cloud nodes.
resource "maas_block_device_tag" "ceph" {
  for_each = local.cloud_machines

  machine         = maas_machine.node[each.key].id
  block_device_id = one([for device in maas_machine.node[each.key].block_devices : device.id if device.name == "sdb"])
  tags            = ["ceph"]
}

# External NIC: neutron:physnet1 tag, no IP. The provider cannot look an interface up by MAC,
# so a small script does it through the MAAS CLI inside maas-1.
resource "terraform_data" "external_nic" {
  for_each = local.cloud_machines

  provisioner "local-exec" {
    command = "lxc exec ${local.deployed.maas_instance} -- /usr/local/sbin/maas-tag-nic.sh ${each.key} ${each.value.external_mac} neutron:physnet1"
  }

  depends_on = [maas_machine.node]
}
```

- [ ] **Step 2: Validate**

Run:
```bash
terraform -chdir=terraform/maas-config init -backend=false
terraform -chdir=terraform/maas-config validate
terraform -chdir=terraform/maas-config fmt -check
```
Expected: `Success! The configuration is valid.` and `fmt` exits 0. (`init` downloads `canonical/maas`; `validate` does not read the remote state, so no MAAS is needed.)

- [ ] **Step 3: Commit**

```bash
git add terraform/maas-config
git commit -m "feat(terraform): configure MAAS with the canonical maas provider"
```

---

### Task 11: CI, AGENTS.md and documentation

**Files:**
- Modify: `.github/workflows/ci.yml`, `AGENTS.md`, `docs/maas-provider.md` (rewrite), `docs/usage.md`, `docs/configuration.md`, `docs/getting-started.md`, `docs/troubleshooting.md`, `docs/architecture.md`, `docs/cloud-init-templating.md`, `docs/shared-directory.md`, `docs/reference-architecture.md`, `docs/hooks.md`, `sandbed-design.md`

- [ ] **Step 1: CI** - append after the `terraform/maas` steps in the `terraform` job:

```yaml
      - run: terraform -chdir=terraform/maas-config init -backend=false
      - run: terraform -chdir=terraform/maas-config validate
      - run: terraform -chdir=terraform/maas-config fmt -check
```

- [ ] **Step 2: AGENTS.md**

Replace the Terraform command line with:
```
- Terraform: `terraform -chdir=terraform/<network|manual|maas|maas-config> validate` and `fmt -check`
```
Replace the `terraform/` layout bullet with:
```
- `terraform/`: `network/` is shared. `manual/` and `maas/` are provider modules. `maas-config/` configures MAAS (Canonical `maas` provider) from `maas/`'s local state.
```
Add to `## Rules`:
```
- `tests/test_manual_unchanged.py` pins the `manual` provider. Do not edit it to make a change pass.
- `terraform/maas` outputs (`maas_url`, `maas_api_key`, `maas_admin_password`, `maas_instance`, `lxd_power_*`, `machines`, `client_mgmt_ip`) are read by `maas-config` and `sandbed/terraform.py`. Keep them in sync.
```

- [ ] **Step 3: `docs/maas-provider.md`** - replace the whole file with:

````markdown
# MAAS provider

`sandbed sunbeam maas up` deploys MAAS itself, enlists every Sunbeam node except the client, and
then deploys Sunbeam. There is no external-MAAS mode: `--maas-url` and `--maas-token` no longer
exist (see [Upgrading](#upgrading-from-the-external-maas-version)).

## Topology

```
generic 172.16.1.0/24   LXD DHCP off; MAAS serves DHCP, PXE and DNS
  .1  host bridge / gateway          .2  maas-1 (static)        .3  sunbeam-client-1 (static)
  .20-.39 reserved <name>-internal-api   .40-.59 reserved <name>-public-api
  .100-.200 dynamic range (enlist / commission)
mgmt     192.168.200.0/24   sunbeam-client-1 eth0 (host access)
external 172.16.2.0/24      cloud-* eth1 (floating IPs, `neutron:physnet1`)
```

| VM | NICs | Provisioned by |
|----|------|----------------|
| `maas-1` | `generic` (static `.2`) | cloud-init, then `maas-bootstrap.sh`: `maas` snap (region + rack) and `maas-test-db` snap |
| `sunbeam-client-1` | `mgmt` + `generic` (static `.3`) | cloud-init; drives Sunbeam |
| `cloud-1/2/3` | `generic` + `external` | empty PXE VM, stopped until MAAS powers it on |
| `sunbeam-controller-1/2/3`, `juju-controller-1/2/3` | `generic` | empty PXE VM |
| `observability-N`, `landscape-N` (default 3 each, 0 to 16) | `generic` | empty PXE VM |

Every PXE VM has a secondary `/dev/sdb` RBD volume (only the cloud nodes use it). Their MACs are
fixed (`00:16:3e:5a:01:<n>` on `generic`, `00:16:3e:5a:02:<n>` on `external`) so MAAS can be told
about them before they ever run. `maas-1` and the client have no `/dev/sdb`. `--shared-dir` is
mounted on `maas-1` and the client only (MAAS installs the other nodes, so cloud-init never runs
there).

## Phases

1. **terraform** applies `network`, then `maas` (`terraform/maas/`): networks, `maas-1` (bootstrapped
   through `lxd_instance` `execs`), the client, the PXE VMs, `core.https_address` on the `generic`
   gateway and a generated client certificate that LXD trusts for MAAS power control.
2. **maas-setup** applies `terraform/maas-config/` with the Canonical `maas` provider. It reads
   `maas`'s local state, so no secret goes through the command line.
3. **sunbeam** runs on `sunbeam-client-1`.

## What `maas-setup` configures

| Area | Result |
|------|--------|
| Server | `default_osystem=ubuntu`, `default_distro_series=<--maas-deploy-series>`, `upstream_dns` |
| SSH | `--ssh-key` added to MAAS |
| Images | `ubuntu/<deploy series>` `amd64` selected; the commissioning series too if it differs. **Terraform blocks until every image is fully synced** (`--maas-image-sync-timeout`, default `60m`), then until the rack controller has them |
| Network | the discovered `generic` fabric, VLAN and subnet are imported and given space `--maas-space`, gateway `.1`, DNS `.2`; DHCP on, served by `maas-1` |
| Ranges | dynamic `.100-.200`; reserved `<name>-internal-api` and `<name>-public-api` |
| Machines | each PXE VM is added with the `lxd` power driver, commissioned and waited to `Ready` |
| Tags | see below; `ceph` storage tag on `/dev/sdb` of the cloud nodes; `neutron:physnet1` network tag, unconfigured, on their `external` NIC |

| Role | Machine tags |
|------|--------------|
| cloud | `openstack-<name>`, `control`, `compute`, `storage` |
| sunbeam-controller | `openstack-<name>`, `sunbeam` |
| juju-controller | `openstack-<name>`, `juju-controller` |
| observability, landscape | their own name only (no Sunbeam role) |

`<name>` is `--maas-name` (default `sandbed`).

## Credentials

MAAS UI: `http://172.16.1.2:5240/MAAS`, user `admin`, password `--maas-admin-password` (default
`changeme`, printed at the end of `maas-setup`; also the `maas_admin_password` Terraform output).
The password only applies when MAAS is first bootstrapped. The API key is the `maas_api_key`
output and is what Sunbeam uses.

## Sunbeam flow

From `sunbeam-client-1`: `snap install openstack` → `prepare-node-script --client | bash` →
`deployment add maas <name> <key> <url>` → `deployment space map` → `deployment validate` →
`cluster bootstrap` → `cluster deploy` → `configure`.

## Plan, resume and destroy

- `plan` covers `network` and `maas`; `maas-config` needs a live MAAS and is skipped (a note is
  printed).
- A failed `maas-setup` is resumed by re-running `up`; only `maas-config` is applied again.
- `destroy` tears Sunbeam down, then destroys `maas` and `network`. `maas-config` resources live
  inside `maas-1`, so only its local state is removed. LXD's `core.https_address` is left set.

## Upgrading from the external-MAAS version

`--maas-url`, `--maas-token`, `LXD_TRUST_TOKEN` and the host `maas` snap are gone, and the `maas`
Terraform root was restructured. Destroy an existing `maas` deployment before running the new
`up`.
````

- [ ] **Step 4: Targeted edits in the other docs** (old text -> new text)

`docs/usage.md`:
- Replace the row `| `--maas-name` / `--maas-url` / `--maas-token` | — | MAAS connection (required for maas) |` with these rows:
```
| `--maas-name` | `sandbed` (maas) | Sunbeam deployment name; machines are tagged `openstack-<name>` |
| `--maas-admin-password` | `changeme` | MAAS `admin` password (first bootstrap only) |
| `--maas-deploy-series` | `noble` | Ubuntu series MAAS deploys; also the Juju bootstrap series |
| `--maas-channel` | `3.6/stable` | `maas` snap channel |
| `--maas-vcpus` / `--maas-memory` / `--maas-disk` | `4` / `8192` / `50` | `maas-1` resources (MiB, GiB) |
| `--maas-image-sync-timeout` | `60m` | how long to wait for boot images (`<N>m` or `<N>h`) |
| `--maas-upstream-dns` | `1.1.1.1` | upstream DNS for MAAS and `maas-1` |
| `--maas-parallelism` | `10` | `terraform apply -parallelism` for `maas-config` |
```
- Change the `--ssh-host` row meaning to `(sunbeam phase) target host override; maas defaults to sunbeam-client-1`.
- Delete the `LXD_TRUST_TOKEN` bullet under Environment variables.
- Under the action table add: `plan` for `maas` covers `network` and `maas` only; `maas-config` needs a running MAAS.

`docs/configuration.md` - replace the `## MAAS` table with:
```
| Flag | Default |
|------|---------|
| `--maas-name` | `sandbed` |
| `--maas-space` | `myspace` |
| `--maas-admin-password` | `changeme` |
| `--maas-deploy-series` | `noble` |
| `--maas-channel` | `3.6/stable` |
| `--maas-vcpus` / `--maas-memory` / `--maas-disk` | `4` / `8192` / `50` |
| `--maas-image-sync-timeout` | `60m` |
| `--maas-upstream-dns` | `1.1.1.1` |
| `--maas-parallelism` | `10` |
| `--lxd-https-port` | `8443` |
```
and change the `--obs-count` / `--landscape-count` row to `3` each (maas only; 0 to 16).

`docs/getting-started.md`:
- RAM/CPU line: `**10-16** VMs (maas)` becomes `up to **17** VMs (maas: MAAS, the Sunbeam client and 15 enlisted nodes; fewer with --obs-count/--landscape-count)`.
- Replace the maas quickstart with:
````
```bash
sandbed sunbeam maas up \
  --maas-admin-password 'choose-one' \
  --ssh-key "$(cat ~/.ssh/id_rsa.pub)"
```

This deploys MAAS in an LXD VM (`maas` and `maas-test-db` snaps), configures it with Terraform,
waits for the boot images to sync and for every node to commission, then deploys Sunbeam from
`sunbeam-client-1`. A cold run can take an hour or more. See [MAAS provider](maas-provider.md).
````
- Delete the note about `--maas-url` and `--maas-token` being required.

`docs/troubleshooting.md`: replace the two MAAS rows with
```
| MAAS can't power VMs | LXD `core.https_address` is not `<generic gateway>:8443`, or the `sandbed-maas` certificate is not trusted (`lxc config trust list`). |
| `maas-setup` waits on images | image sync can take 10-30 min; progress is the streamed Terraform output. Raise `--maas-image-sync-timeout` on slow links, then re-run `up`. |
| machine stuck in PXE / Commissioning | DHCP on `generic` is served by `maas-1`: check `lxc exec maas-1 -- snap logs maas`, and that the VM has `security.secureboot=false`. |
| `preflight` says `core.https_address` is already set | unset it (`lxc config unset core.https_address`) or set it to the address shown. |
```
and change the state-location bullet to `terraform/{network,manual,maas,maas-config}/`.

`docs/architecture.md`:
- layout line: `network/   manual/   maas/     # independent LXD roots, cf. -chdir` becomes `network/   manual/   maas/   maas-config/   # independent roots, cf. -chdir`.
- paragraph ending `then applies `manual/` or `maas/`.` add: ` For maas, `maas-config/` is applied in the maas-setup phase and reads `maas/`'s local state.`
- phase 3: `**terraform** - `network` then `manual`/`maas` apply (maas deploys MAAS itself).`
- phase 4: `**maas-setup** (maas) - applies `maas-config`: images (blocks until synced), network and DHCP, machines, tags.`
- data-flow line: add `terraform outputs (maas) ──► maas-config (via remote state) and the sunbeam phase (URL, API key, client IP)`.

`docs/cloud-init-templating.md`:
- fallback line 3 becomes `3. embedded `terraform/{manual,maas}/cloud-init/user-data.yaml.tpl``.
- Replace the MAAS `cloud-*` bullet with: `In the maas module only `maas-1` and `sunbeam-client-1` consume cloud-init (every other node is an empty PXE VM installed by MAAS), so overrides named after other nodes are ignored. Their network-config is static and always has an embedded fallback, and `default.network-config.yaml.tpl` is not applied to them; only `<node>.network-config.yaml.tpl` overrides it.`

`docs/shared-directory.md`: after the first paragraph add: `In the maas provider the directory is mounted on `maas-1` and `sunbeam-client-1` only; the other nodes are installed by MAAS.`

`docs/reference-architecture.md`:
- Implemented: replace the MAAS bullet with `- MAAS-driven lifecycle: MAAS is deployed by the harness; every node except the Sunbeam client is enlisted and commissioned with the `lxd` power driver and tagged as in Canonical's Sunbeam-on-MAAS guide.`
- Divergences: replace the MAAS topology bullet with `- **MAAS topology**: one MAAS VM (region + rack, maas-test-db) instead of three, and 9 core nodes plus optional observability/landscape instead of the 21-machine physical reference.`
- Out of scope: remove `MAAS region/rack install, ` (it now reads: standalone OpenStack operations ..., MAAS HA, and any non-LXD target).

`docs/hooks.md`: add under the env-var list: `For the maas provider, `SDB_CFG_MAAS_URL`, `SDB_CFG_MAAS_TOKEN`, `SDB_CFG_MAAS_ADMIN_PASSWORD` and `SDB_CFG_SSH_HOST` are filled from Terraform outputs just before the maas-setup and sunbeam phases; hooks for earlier phases see them empty (or default).`

- [ ] **Step 5: `sandbed-design.md`** (update by section heading)

- section 2, bullet "MAAS setup fully automated": `- **MAAS deployed and configured by the harness:** `maas-1` (maas + maas-test-db snaps) built by Terraform; MAAS configured by Terraform (Canonical `maas` provider): images, network/DHCP, machines, tags, LXD power driver.`
- section 3 table rows become:
  - `| Scope boundary | Provision VMs (Terraform/LXD), deploy MAAS itself in an LXD VM, configure it with the Canonical `maas` Terraform provider, and drive Sunbeam (script) |`
  - `| Module structure | Shared `network/` + `manual/`, `maas/` (lxd) and `maas-config/` (maas provider) modules; `sandbed sunbeam manual\|maas` selects |`
  - `| MAAS VM set | `maas-1` + `sunbeam-client-1` (cloud-init) + 9 core nodes + observability/landscape variable counts (default 3, 0 to 16); all but the client are empty PXE VMs enlisted in MAAS |`
  - `| MAAS control | MAAS `lxd` power driver over HTTPS to `<generic gateway>:8443` with a Terraform-generated client certificate trusted by LXD; PXE enlist/commission off `generic` served by maas-1 |`
  - `| MAAS-side setup | Terraform `maas-config` root; images blocking until synced; admin password is a variable (default `changeme`) and an output |`
- section 4 layout: add `maas-config/` under `terraform/` and `files/` under `maas/`.
- section 7: replace its body with the topology, phase and `maas-config` description from `docs/maas-provider.md` (Topology, Phases and What maas-setup configures).
- section 9, phase 2: delete `install MAAS CLI / python-libmaas (MAAS path)`. Phase 4: `**maas setup (MAAS path only)** - terraform apply of maas-config/ (see section 7); blocks until images are synced and machines are Ready.`
- section 10, maas bullet: replace with `- maas: `obs_count = 3`, `landscape_count = 3`, `generic_cidr`, `external_cidr`, `maas_name = sandbed`, `maas_space = myspace`, `maas_admin_password = changeme`, `maas_channel = 3.6/stable`, `maas_vcpus/memory/disk = 4/8192/50`, `deploy_series = noble`, `image_sync_timeout = 60m`, `upstream_dns = 1.1.1.1`, `lxd_https_port = 8443`; `maas_url`/`maas_token` are derived, not inputs.`
- section 14: replace the "MAAS infrastructure external" bullet with `- **MAAS in one VM (accepted):** region and rack controllers share one LXD VM with the single-node, non-HA maas-test-db. The `maas` snap channel must support the 26.04 guest (default `3.6/stable`).`
- section 15: replace the first bullet with `- MAAS high availability (several region/rack controllers, external PostgreSQL).`

- [ ] **Step 6: Check nothing stale is left**

Run: `grep -rn "maas-url\|maas-token\|LXD_TRUST_TOKEN\|assumed running externally\|MAAS CLI" docs README.md AGENTS.md sandbed-design.md`
Expected: only the intentional mentions inside the "Upgrading from the external-MAAS version" section of `docs/maas-provider.md`.

- [ ] **Step 7: Commit**

```bash
git add .github/workflows/ci.yml AGENTS.md docs sandbed-design.md
git commit -m "docs: document managed MAAS and add maas-config to CI"
```

---

### Task 12: Final verification and real-host spike

**Part A - automated (do before declaring done)**

- [ ] **Step 1: Full test suite and style**

Run: `PYTHONPATH=/tmp/opencode/deps python3 -m pytest tests -q -p no:cacheprovider` (or `uv run pytest`)
Expected: all pass (89 tests), including all 8 in `tests/test_manual_unchanged.py`.
Run: `awk 'length>100{print FILENAME":"FNR}' sandbed/*.py tests/*.py` (or `uv run ruff check .`)
Expected: no output / no findings.

- [ ] **Step 2: Terraform**

Run, for each of `network manual maas maas-config`: `terraform -chdir=terraform/$m init -backend=false && terraform -chdir=terraform/$m validate && terraform -chdir=terraform/$m fmt -check`
Expected: all valid, `fmt` exits 0.

- [ ] **Step 3: Prove `manual` did not move**

Run: `git diff --stat <commit-before-Task-2>..HEAD -- terraform/network manifests/manual.yaml.j2 scripts/prep-host.sh sandbed/testflinger.py hooks`
Expected: no output.
Run: `git diff <commit-before-Task-2>..HEAD -- terraform/manual`
Expected: only the `timeouts {` to `timeouts = {` line.

**Part B - on a real host (cannot run in CI; each item names where to fix a failure)**

Run `sandbed sunbeam maas up --ssh-key ... --maas-admin-password ... --obs-count 0 --landscape-count 0` (9 PXE nodes) on a host with LXD and microceph, and check:

- [ ] `lxd_instance` `wait_for { type = "agent" }` and `execs` with `trigger = "once"` run on the 26.04 image; `maas-bootstrap.sh` finishes (`terraform/maas/controller.tf`). If `execs` run too early, add a `00-wait` exec (`cloud-init status --wait`).
- [ ] `maas init region+rack --database-uri maas-test-db:/// --maas-url ... --force` works on `3.6/stable` in a 26.04 VM; otherwise raise `--maas-channel` (`files/maas-bootstrap.sh`).
- [ ] The rack controller registers as hostname `maas-1` (`data "maas_rack_controller"`); if MAAS appends a domain, set `hostname` accordingly (`maas-config/network.tf`).
- [ ] `import` of `maas_vlan.generic` (`fabric-0:0`) and `maas_subnet.generic` succeeds and a second `maas-config` apply shows no changes. If the fabric name differs, pass `generic_fabric`; if import blocks cannot be used, replace them with `terraform import` calls in `apply_maas_config` (`sandbed/terraform.py`) guarded by `terraform state list`.
- [ ] `maas_boot_source_selection` blocks until `ubuntu/noble amd64` is fully synced; `maas-wait-rack-images.sh` sees `status == "synced"` (the JSON field name of `rack-controller list-boot-images`).
- [ ] LXD power: `power_address = 172.16.1.1:8443` is accepted by the MAAS `lxd` driver (else use `https://172.16.1.1:8443` in `outputs.tf`); PXE boot works with `security.secureboot=false`; every machine reaches `Ready`.
- [ ] `/dev/sdb` has MAAS name `sdb` on commissioned VMs (`maas-config/machines.tf`).
- [ ] `maas-tag-nic.sh`: after it runs, the `external` NIC shows the `neutron:physnet1` tag and `Unconfigured`; adjust the `unlink-subnet` / `link-subnet mode=LINK_UP` calls if MAAS 3.6 wants different parameters.
- [ ] `maas-1` has working DNS with `--maas-upstream-dns` (default `1.1.1.1`); change it on networks without public DNS.
- [ ] Tags: `openstack-sandbed`, `control`, `compute`, `storage`, `sunbeam`, `juju-controller` land on the right machines; `sunbeam deployment validate` passes from `sunbeam-client-1`.
- [ ] Re-run `up`: completed phases are skipped; interrupt `maas-setup` and re-run: only `maas-config` is applied again.
- [ ] `destroy`: Sunbeam teardown runs on the client (host resolved from outputs), `maas` and `network` are destroyed, `terraform/maas-config/terraform.tfstate*` is gone. Note `lxd_server` leaves `core.https_address` set; a following `up` must still pass preflight.
- [ ] Capacity: with the default counts (15 PXE VMs) watch host memory during commissioning; lower `--maas-parallelism`, `--obs-count`, `--landscape-count` or `--memory` if the host swaps.
- [ ] `sandbed sunbeam manual plan` / `up` on a host with an existing manual deployment shows no Terraform changes for `network` and `manual`.

---

## Self-review (spec coverage)

| Requirement | Task |
|---|---|
| maas snap + maas-test-db snap, Terraform-driven | 9 (`maas-bootstrap.sh`, `controller.tf`) |
| single machine runs region and rack controllers | 9 (`maas init region+rack` on `maas-1`) |
| admin password is a Terraform variable, default `changeme`, and an output | 9 (`variables.tf`, `outputs.tf`), 4/5 (Python side, env not `-var`) |
| block until images are fully synced | 10 (`maas_boot_source_selection` + rack gate) |
| `cloud-*` deploy `noble` by default, variable | 4, 5, 7, 8, 10 (`deploy_series`) |
| external MAAS dropped | 7 (flags removed), 6 (host snap removed), 8 |
| configuration via the Canonical `maas` provider | 10 |
| option A: all but the client enlisted | 9 (`nodes.tf`), 10 (`machines.tf`) |
| `manual` behaviour unchanged | 3 (guard), guards in 4-8, 12 Part A step 3 |
