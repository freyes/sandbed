# Usage

## CLI shape

```
sandbed {manual|maas} {up|plan|destroy|sunbeam} [flags]
```

| Action | Effect |
|--------|--------|
| `plan` | dry-run: render `terraform plan` for the selected provider module |
| `up`    | run the full gated pipeline (preflight → install-tools → terraform → maas-setup → sunbeam) |
| `sunbeam` | re-run only the sunbeam phase |
| `destroy` | sunbeam teardown (best-effort) → `terraform destroy` |

Phases are gated and resumable via a phase-state file at `~/.cache/sandbed/state.json`;
re-runs skip completed phases.

## Common flags

| Flag | Default | Meaning |
|------|---------|---------|
| `--mgmt-cidr` | `192.168.200.0/24` | CIDR of the `mgmt` LXD network |
| `--vcpus` / `--memory` / `--disk` | `4` / `8192` / `100` | VM resources |
| `--shared-dir` | `""` | host dir mounted into every VM at `/shared` |
| `--ssh-key` | `$SUNBEAM_SSH_KEY` | public key injected via cloud-init |
| `--ssh-host` | `""` | (sunbeam phase) target host override |
| `--microceph-osd-img` | (snap common path) | file backing the loop OSD |
| `--maas-name` / `--maas-url` / `--maas-token` | — | MAAS connection (required for maas) |
| `--hooks-dir` / `--no-hooks` | `hooks` / off | hook control (see [hooks](hooks.md)) |
| `--cloud-init-templates-dir` | `templates/cloud-init` | Cloud-init template dir (see [cloud-init templating](cloud-init-templating.md)) |
| `--use-testflinger` | off | run remotely on a Testflinger-allocated node (see [testflinger](testflinger.md)) |

Run `sandbed --help` for the full list.

## Environment variables

- `SUNBEAM_SSH_KEY` — default SSH public key when `--ssh-key` is omitted.
- `LXD_TRUST_TOKEN` — trust token used by the maas-setup phase to let MAAS reach LXD over HTTPS.