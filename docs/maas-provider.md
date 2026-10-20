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
| SSH | the deployment public key (`.sandbed_ed25519.pub` unless `--ssh-key`) added to MAAS |
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
