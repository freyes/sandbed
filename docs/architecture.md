# Architecture

## Repo layout

```
sandbed                        # entry point (console script)
sandbed/               # Python package: one module per concern
  cli.py config.py state.py shell.py manifest.py
  preflight.py install.py terraform.py maas.py sunbeam.py destroy.py hooks.py
terraform/
  network/   manual/   maas/   maas-config/   # independent roots, cf. -chdir
manifests/                       # Jinja2 sunbeam deployment manifests
templates/cloud-init/            # user cloud-init overrides
hooks/                           # user hook scripts
docs/                            # this documentation
tests/                           # pytest suite (+ terraform validate in CI)
```

Each Terraform directory is an independent root with its own provider pin
(`terraform-lxd/lxd ~> 3.0`) and local state; `sandbed` applies `network/` first, reads its
outputs, then applies `manual/` or `maas/`. For maas, `maas-config/` is applied in the maas-setup
phase and reads `maas/`'s local state.

## Host-storage model

```
bare-metal host (Ubuntu 26.04, KVM)
├─ LXD (snap)                     ← VMs: Sunbeam control + compute/storage nodes
├─ microceph (snap, single node)  ← file-backed loop OSD
│   └─ "host-ceph" → LXD `ceph` pool (RBD: VM root disks + /dev/sdb)
└─ sandbed + Terraform
```

Inside each VM, Sunbeam deploys its **own** microceph and uses `/dev/sdb` as its OSD — a nested
Ceph arrangement (host Ceph feeds the guest Ceph's disks).

## Phases

1. **preflight** — KVM, LXD, microceph, `--shared-dir` checks (fail fast with remediation).
2. **install-tools** — idempotent install of LXD, microceph (+ loop OSD), Terraform, `openstack`;
   `verify_ceph` asserts Ceph is functional **before** the ceph LXD pool is created.
3. **terraform** — `network` then `manual`/`maas` apply (maas deploys MAAS itself).
4. **maas-setup** (maas) — applies `maas-config`: images (blocks until synced), network and DHCP,
   machines, tags.
5. **sunbeam** — manifest-driven, non-interactive deploy (manual or maas flow).
6. **destroy** — sunbeam teardown then `terraform destroy`.

Each phase is wrapped by the [hooks](hooks.md) system (`<phase>.pre`/`.post`, `on-fail`).

## Data flow

```
CLI flags / env ──► Config ──► PhaseState (resume) ──► phases
terraform outputs (network) ──► manual/maas module inputs (image, mgmt, ceph)
terraform outputs (maas) ──► maas-config (remote state) and the sunbeam phase (URL, API key, client IP)
Config + manifests/*.j2 ──► rendered Sunbeam manifest ──► sunbeam CLI (via SSH)
```