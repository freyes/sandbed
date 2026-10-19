# MAAS provider

## Topology

LXD networks: `generic` (MAAS PXE), `external` (floating IPs), `mgmt`.

Core HA VMs (10):

| VM | NICs | Notes |
|----|------|-------|
| `cloud-1/2/3` | `generic` + `external` | empty (`--empty`), PXE-booted, MAAS-deployed; `/dev/sdb` |
| `sunbeam-controller-1/2/3` | `mgmt` | cloud-init provisioned |
| `juju-controller-1/2/3` | `mgmt` | cloud-init provisioned |
| `sunbeam-client-1` | `mgmt` | cloud-init provisioned; drives sunbeam |

Optional (variable counts, default 3): `observability-1/2/3`, `landscape-1/2/3`. Every provisioned
VM also receives a secondary `/dev/sdb` RBD block volume.

## MAAS automation (maas-setup phase)

The harness drives the MAAS API/CLI:

1. Expose LXD over HTTPS (`core.https_address`) + trust token.
2. Create each VM as a MAAS machine with `power_type=lxd`
   (`power_address`, `power_parameters_instance_name`, `project`).
3. Enlist + commission the VMs (PXE off `generic`).
4. Reserved IP ranges (`<name>-internal-api`, `<name>-public-api`).
5. Machine tags: `openstack-<name>`, `control`/`compute`/`storage`, `sunbeam`, `juju-controller`;
   network tag `neutron:physnet1`; `ceph` storage tag on `/dev/sdb`.

> The exact per-node space/tag/range map is environment-specific and finalized against the live
> MAAS CLI (`python-libmaas`); see the machine-creation call shape in `sandbed/maas.py`.

## Sunbeam flow

From `sunbeam-client-1`: `snap install openstack` → `prepare-node-script --client | bash` →
`deployment add maas …` → `deployment space map` → `deployment validate` → `cluster bootstrap` →
`cluster deploy` → `configure`.