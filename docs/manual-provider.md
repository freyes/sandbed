# Manual provider

## Topology

3 hyperconverged VMs: `cloud-1`, `cloud-2`, `cloud-3`.

| Resource | Default |
|----------|---------|
| vCPU | 4 |
| RAM | 8 GiB |
| root disk | 100 GiB (RBD, `ceph` pool) |
| `/dev/sdb` | secondary RBD block volume (Sunbeam storage role) |

Each VM has two NICs: `mgmt` (management + SSH/cloud-init) and `external` (floating IPs), plus an
optional `/shared` mount (see [shared directory](shared-directory.md)).

## Sunbeam flow

1. `cloud-1`: `snap install openstack` → `prepare-node-script --bootstrap | bash`.
2. `cluster bootstrap --role control,compute,storage` (manifest-driven).
3. Scale out: `cloud-2`/`cloud-3`.
4. `configure` → dashboard URL + `openrc`.

Roles `control,compute,storage` run on each node; `/dev/sdb` backs the guest microceph OSD.