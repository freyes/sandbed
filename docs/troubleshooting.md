# Troubleshooting

## Log locations

- Phase commands: `/tmp/sandbed.log` (also piped to `on-fail` hooks).
- Terraform state/logs: `terraform/{network,manual,maas}/` (local state).
- Phase-state file: `~/.cache/sandbed/state.json`.

## Common failures

| Symptom | Cause / fix |
|---------|-------------|
| `verify_ceph` fails | No OSD `up` or RBD pool missing — ensure `microceph disk add` succeeded and the loop OSD is healthy (`microceph.ceph -s`). |
| Terraform `ceph` pool creation fails | microceph not ready or `ceph.conf`/keyring not visible to LXD; re-run `install-tools` phase. |
| VM hangs on `wait_for ipv4` | DHCP not served on `mgmt`; check the LXD `mgmt` network config (`ipv4.dhcp`). |
| `/shared` mount missing | If you overrode user-data, re-add the `runcmd` (see [shared-directory](shared-directory.md)). Network-config 9p vs virtiofs: Ubuntu 26.04 uses virtiofs (`mount -t virtiofs shared /shared`). |
| MAAS can't power VMs | LXD not exposed over HTTPS or missing trust; check `core.https_address` + `LXD_TRUST_TOKEN`. |
| MAAS enlist/commission fails | `generic` network doesn't bridge to the MAAS PXE space; verify the uplink bridge. |
| `cluster bootstrap` hangs | Sunbeam manifest not passed or `--ssh-host` wrong; re-run `sunbeam` phase after fixing. |

## Teardown order

Always tear down Sunbeam **before** the VMs: `sandbed manual destroy` (or `maas`) warns on
ordering. The harness runs sunbeam teardown, then `terraform destroy`.