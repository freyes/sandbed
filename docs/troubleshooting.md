# Troubleshooting

## Log locations

- Phase commands: `/tmp/sandbed.log` (also piped to `on-fail` hooks).
- Terraform state/logs: `terraform/{network,manual,maas,maas-config}/` (local state).
- Phase-state file: `~/.cache/sandbed/state.json`.

## Common failures

| Symptom | Cause / fix |
|---------|-------------|
| `verify_ceph` fails | No OSD `up` or RBD pool missing — ensure `microceph disk add` succeeded and the loop OSD is healthy (`microceph.ceph -s`). |
| Terraform `ceph` pool creation fails | microceph not ready or `ceph.conf`/keyring not visible to LXD; re-run `install-tools` phase. |
| VM hangs on `wait_for ipv4` | DHCP not served on `mgmt`; check the LXD `mgmt` network config (`ipv4.dhcp`). |
| `/shared` mount missing | If you overrode user-data, re-add the `runcmd` (see [shared-directory](shared-directory.md)). Network-config 9p vs virtiofs: Ubuntu 26.04 uses virtiofs (`mount -t virtiofs shared /shared`). |
| MAAS can't power VMs | LXD `core.https_address` is not `<generic gateway>:8443`, or the `sandbed-maas` certificate is not trusted (`lxc config trust list`). |
| `maas-setup` waits on images | Image sync can take 10-30 min; progress is the streamed Terraform output. Raise `--maas-image-sync-timeout` on slow links, then re-run `up`. |
| Machine stuck in PXE / Commissioning | DHCP on `generic` is served by `maas-1`: check `lxc exec maas-1 -- snap logs maas`, and that the VM has `security.secureboot=false`. |
| `preflight` says `core.https_address` is already set | Unset it (`lxc config unset core.https_address`) or set it to the address shown. |
| `cluster bootstrap` hangs | Sunbeam manifest not passed or `--ssh-host` wrong; re-run `sunbeam` phase after fixing. |

## Teardown order

Always tear down Sunbeam **before** the VMs: `sandbed sunbeam manual destroy` (or `maas`) warns on
ordering. The harness runs sunbeam teardown, then `terraform destroy`.