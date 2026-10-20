# Getting started

## Host prerequisites

- Ubuntu **26.04** (no other release is supported).
- KVM enabled (`/dev/kvm` present).
- `snapd` running (LXD and microceph are installed as snaps).
- RAM/CPU/disk to host **3** VMs (manual) or up to **17** VMs (maas: MAAS, the Sunbeam client and 15 enlisted nodes; fewer with `--obs-count`/`--landscape-count`), each default 4 vCPU / 8 GB RAM /
  100 GB disk, plus a single-node Ceph OSD.

## Install

```bash
git clone <this-repo> && cd sandbed
uv sync
```

`uv sync` installs the `sandbed` console script (registered in `pyproject.toml`) into the
virtual environment; invoke it as `uv run sandbed` or, once activated, directly as
`sandbed`.

The tool self-installs the host tools it needs (LXD, microceph, Terraform from HashiCorp's apt
repo, the `openstack` snap) during the `install-tools` phase — you don't need to pre-install them.

## Quickstart — manual provider

```bash
sandbed sunbeam manual up
```

This provisions 3 hyperconverged VMs (`cloud-1/2/3`) and drives Sunbeam: bootstrap on `cloud-1`,
scale out `cloud-2/3`, then `configure`. On success it prints the dashboard URL and `openrc`.

## Quickstart — maas provider

```bash
sandbed sunbeam maas up \
  --maas-admin-password 'choose-one'
```

This deploys MAAS in an LXD VM (`maas` and `maas-test-db` snaps), configures it with Terraform,
waits for the boot images to sync and for every node to commission, then deploys Sunbeam from
`sunbeam-client-1`. A cold run can take an hour or more. See [MAAS provider](maas-provider.md).

## Quickstart — Testflinger (remote execution)

```bash
sandbed sunbeam --use-testflinger \
  --testflinger-queue my-queue --testflinger-key lp:youruser \
  manual plan
```

This allocates a node on the queue, waits for it to become reachable, `scp`s the repo to it, and
runs `manual plan` remotely. See [testflinger](testflinger.md) for the full flag reference.