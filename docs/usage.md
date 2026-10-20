# Usage

## CLI shape

```
sandbed sunbeam {manual|maas} {up|plan|destroy|sunbeam} [flags]
```

| Action | Effect |
|--------|--------|
| `plan` | dry-run: render `terraform plan` for the selected provider module (for `maas`: `network` and `maas` only; `maas-config` needs a running MAAS) |
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
| `--ssh-key` | `$SUNBEAM_SSH_KEY` | public key injected via cloud-init. For maas it falls back to the first of `~/.ssh/id_ed25519.pub`, `id_ecdsa.pub`, `id_rsa.pub`, and `preflight` fails if there is none (the private half must be on the machine running sandbed, which ssh-es into `sunbeam-client-1`) |
| `--ssh-host` | `""` | (sunbeam phase) target host override; maas defaults to `sunbeam-client-1` |
| `--microceph-osd-img` | (snap common path) | file backing the loop OSD |
| `--maas-name` | `sandbed` (maas) | Sunbeam deployment name; machines are tagged `openstack-<name>` |
| `--maas-admin-password` | `changeme` | MAAS `admin` password (first bootstrap only) |
| `--maas-deploy-series` | `noble` | Ubuntu series MAAS deploys; also the Juju bootstrap series |
| `--maas-channel` | `3.6/stable` | `maas` snap channel |
| `--maas-vcpus` / `--maas-memory` / `--maas-disk` | `4` / `8192` / `50` | `maas-1` resources (MiB, GiB) |
| `--maas-image-sync-timeout` | `60m` | how long to wait for boot images (`<N>m` or `<N>h`) |
| `--maas-upstream-dns` | `1.1.1.1` | upstream DNS for MAAS and `maas-1` |
| `--maas-parallelism` | `10` | `terraform apply -parallelism` for `maas-config` |
| `--hooks-dir` / `--no-hooks` | `hooks` / off | hook control (see [hooks](hooks.md)) |
| `--cloud-init-templates-dir` | `templates/cloud-init` | Cloud-init template dir (see [cloud-init templating](cloud-init-templating.md)) |
| `--use-testflinger` | off | run remotely on a Testflinger-allocated node (see [testflinger](testflinger.md)) |

Run `sandbed sunbeam --help` for the full list.

## Environment variables

- `SUNBEAM_SSH_KEY` — default SSH public key when `--ssh-key` is omitted.
