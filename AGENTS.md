# AGENTS.md

sandbed: CLI that deploys Canonical OpenStack (Sunbeam) on LXD VMs via Terraform. Python >=3.10, click.

## Commands
- Setup: `uv sync`
- Test: `uv run pytest`
- Lint: `uv run ruff check .` (line length 100, rules E/F/I)
- Terraform: `terraform -chdir=terraform/<network|manual|maas|maas-config> validate` and `fmt -check`
- Run: `uv run sandbed sunbeam {manual|maas} {up|plan|destroy|sunbeam} [flags]`

CI (`.github/workflows/ci.yml`) runs all of the above. Keep them green.

## Layout
- `sandbed/`: package, one module per concern. `cli.py` is a click group with a single `sunbeam` subcommand.
- `terraform/`: `network/` is shared. `manual/` and `maas/` are provider modules. `maas-config/` configures MAAS (Canonical `maas` provider) from `maas/`'s local state.
- `manifests/`, `templates/`: Jinja2 templates. `hooks/`: user hook scripts. `docs/`: user docs.
- `sandbed-design.md`: design doc.

## Rules
- Update `docs/` and `sandbed-design.md` when changing CLI flags, phases, or paths.
- `testflinger.remote_command` rebuilds the remote command from raw `sys.argv`. Keep it in sync with CLI changes.
- Tests mock `run`. Never call real `terraform`, `snap`, `ssh` or `testflinger-cli` from tests.
- Hook env vars use the `SDB_` prefix. Treat them as a public interface.
- `tests/test_manual_unchanged.py` pins the `manual` provider. Do not edit it to make a change pass.
- `terraform/maas` outputs (`maas_url`, `maas_api_key`, `maas_admin_password`, `maas_instance`, `lxd_power_*`, `machines`, `client_mgmt_ip`) are read by `maas-config` and `sandbed/terraform.py`. Keep them in sync.
- `.omo/` holds historical plans. Do not edit it.
- Don't commit `tmp*.yaml` or `.sandbed.testflinger.job_id`.
- Commit messages: conventional style (`feat(scope): ...`, `fix(scope): ...`).
