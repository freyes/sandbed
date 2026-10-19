# Configuration

Configuration is resolved from three sources, in increasing precedence: built-in defaults →
environment variables → CLI flags (a `config.yaml` reader is provided for custom integrations).

## Networking

| Flag | Default | Meaning |
|------|---------|---------|
| `--mgmt-cidr` | `192.168.200.0/24` | LXD `mgmt` network (cloud-init + Sunbeam mgmt) |
| `--external-cidr` | `172.16.2.0/24` | LXD `external` network (floating IPs) |
| `--generic-cidr` | `172.16.1.0/24` | LXD `generic` network (MAAS PXE, maas only) |

## VM resources

| Flag | Default |
|------|---------|
| `--vcpus` | `4` |
| `--memory` | `8192` (MiB) |
| `--disk` | `100` (GiB, root) |
| `--obs-count` / `--landscape-count` | `3` (maas only; settable 0/1) |

## Storage — host microceph

| Flag | Default |
|------|---------|
| `--microceph-osd-size` | `50` (GiB sparse file) |
| `--microceph-osd-img` | `/var/snap/microceph/common/osd.img` (loop-backed OSD file) |

The microceph cluster is single-node; LXD's `ceph` storage pool (VM root disks + `/dev/sdb`) points
at `ceph.cluster_name=ceph`, `ceph.user.name=admin`, and the `rbd` pool.

## Shared directory

| Flag | Default |
|------|---------|
| `--shared-dir` | `""` (disabled) |

## MAAS

| Flag | Default |
|------|---------|
| `--maas-name` / `--maas-url` / `--maas-token` | — (required for maas) |
| `--maas-space` | `myspace` |
| `--lxd-https-port` | `8443` |

## Sunbeam

| Flag | Default |
|------|---------|
| `--region` | `RegionOne` |
| `--db-topology` | `single` |
| `--project-network` | `192.168.0.0/24` |

## Hooks / cloud-init

| Flag | Default |
|------|---------|
| `--hooks-dir` / `--no-hooks` | `hooks` / off |
| `--cloud-init-templates-dir` | `templates/cloud-init` |

## Testflinger

| Flag | Default |
|------|---------|
| `--use-testflinger` | off |
| `--testflinger-series` | `resolute` (→ `reserve --distro`) |
| `--testflinger-queue` | — (required when `--use-testflinger`) |
| `--testflinger-timeout` | `6h` (humanized; → seconds) |
| `--testflinger-job` | — (reuse an existing job id) |
| `--testflinger-job-file` | — (Jinja2 YAML with `queue`/`series`/`timeout` vars) |
| `--testflinger-key` | — (repeatable, required when allocating) |

See [testflinger](testflinger.md) for the full flow.