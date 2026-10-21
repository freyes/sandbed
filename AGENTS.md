# AGENTS.md

sandbed: CLI that deploys Canonical OpenStack (Sunbeam) on LXD VMs via Terraform. Python >=3.10, click.

## Commands
- Setup: `uv sync`
- Test: `uv run pytest`
- Lint: `uv run ruff check .` (line length 100, rules E/F/I)
- Terraform: `terraform -chdir=terraform/<network|manual|maas|maas-config|dev-network|dev-manual> validate` and `fmt -check`
- Run: `uv run sandbed sunbeam {manual|maas} {up|plan|destroy|sunbeam} [flags]`
- Run (development labs): `uv run sandbed dev capabilities --json`, see `docs/development-labs.md`

CI (`.github/workflows/ci.yml`) runs all of the above. Keep them green.

## Layout
- `sandbed/`: package, one module per concern. `cli.py` is a click group with two subcommands: `sunbeam` (legacy phases) and `dev` (development labs, `sandbed/dev/`).
- `terraform/`: `network/` is shared. `manual/` and `maas/` are provider modules. `maas-config` configures MAAS (Canonical `maas` provider) from `maas/`'s local state. `dev-network/` and `dev-manual/` are copied per lab by `sandbed dev` and own only that lab's resources.
- `manifests/`, `templates/`: Jinja2 templates. `hooks/`: user hook scripts. `docs/`: user docs.
- `skills/snap-openstack-dev/`: agent skill for using `sandbed dev`. `sandbed-design.md`: design doc.

## Rules
- Update `docs/` and `sandbed-design.md` when changing CLI flags, phases, or paths.
- `testflinger.remote_command` rebuilds the remote command from raw `sys.argv`. Keep it in sync with CLI changes.
- Tests mock `run`. Never call real `terraform`, `snap`, `ssh` or `testflinger-cli` from tests. The one exception is `tests/test_dev_acceptance_integration.py`: real host, only with `SANDBED_DEV_INTEGRATION=1`, never set in CI.
- Hook env vars use the `SDB_` prefix. Treat them as a public interface.
- `tests/test_manual_unchanged.py` pins the `manual` provider. Do not edit it to make a change pass.
- `terraform/maas` outputs (`maas_url`, `maas_api_key`, `maas_admin_password`, `maas_instance`, `lxd_power_*`, `machines`, `client_mgmt_ip`) are read by `maas-config` and `sandbed/terraform.py`. Keep them in sync.
- `.omo/` holds historical plans. Do not edit it.
- Don't commit `tmp*.yaml`, `.sandbed.testflinger.job_id` or `.sandbed_ed25519*` (the deployment key).
- Commit messages: conventional style (`feat(scope): ...`, `fix(scope): ...`).
- `sandbed/dev/` stays independent of the legacy phase machinery: it may import only `shell.run` and `sshkey.ensure_key` from the rest of the package (`tests/test_dev_isolation.py` enforces it). Dev state lives under `$XDG_STATE_HOME/sandbed/dev`, never in the legacy state file.
- The `sandbed dev` JSON `schema_version`, exit codes, failure categories and `INTERFACE_VERSION` (`sandbed/dev/capabilities.py`) are a public interface. Change one, bump the version, and update `skills/snap-openstack-dev/` and `docs/development-labs.md`.
- `terraform/dev-*` must never manage the shared LXD pool (`sandbed-dev`) nor read `terraform/maas*` state (`tests/test_dev_terraform.py`).
