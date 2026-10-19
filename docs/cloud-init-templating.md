# Cloud-init templating

User cloud-init templates in `templates/cloud-init/` override the built-in ones, resolved per VM by
Terraform at plan time (`fileexists()` + nested ternary). Templates use Terraform template syntax.

## Template variables

- `hostname` — the node name (`cloud-1`, `sunbeam-controller-2`, …)
- `user` — the sudo user (`--vm-user`)
- `ssh_key` — authorized SSH public key
- `shared_enabled` — `true` when `--shared-dir` is set
- `provider` — `manual` or `maas`

## Resolution order — user-data (per VM)

1. `templates/cloud-init/<node>.user-data.yaml.tpl`
2. `templates/cloud-init/default.user-data.yaml.tpl`
3. embedded `terraform/{manual,maas}/cloud-init/user-data.yaml.tpl`

## Resolution order — network-config (per VM)

1. `templates/cloud-init/<node>.network-config.yaml.tpl`
2. `templates/cloud-init/default.network-config.yaml.tpl`
3. (none) — `user.network-config` is omitted, so LXD's default (auto-DHCP) applies.

## Notes

- When you override `user-data`, you own the entire cloud-init user-data — including the `/shared`
  mount `runcmd` (see [shared directory](shared-directory.md)).
- MAAS `cloud-*` nodes are empty PXE machines (no cloud-init), so a `<node>` override named
  `cloud-1` in the maas module is ignored.
- `sandbed` resolves the directory to an absolute path before passing it as a Terraform variable,
  so it works regardless of which module directory Terraform runs in.

## Examples

Per-node user-data for `cloud-1`:

```yaml
# templates/cloud-init/cloud-1.user-data.yaml.tpl
#cloud-config
hostname: ${hostname}
users:
  - name: ${user}
    shell: /bin/bash
    ssh_authorized_keys:
      - ${ssh_key}
```

A default network-config (static IP on eth0):

```yaml
# templates/cloud-init/default.network-config.yaml.tpl
version: 2
ethernets:
  eth0:
    dhcp4: true
```