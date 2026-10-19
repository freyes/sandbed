# Cloud-init templates

User cloud-init templates override the built-in ones, resolved per VM by Terraform at plan time.
Templates use Terraform template syntax and receive these variables:

- `hostname` — the node name (e.g. `cloud-1`, `sunbeam-controller-2`)
- `user` — the sudo user (`--vm-user`)
- `ssh_key` — the authorized SSH public key
- `shared_enabled` — `true` when `--shared-dir` is set
- `provider` — `manual` or `maas`

| File | Role |
|------|------|
| `<node>.user-data.yaml.tpl` | per-node user-data (e.g. `cloud-1`, `sunbeam-controller-2`) |
| `default.user-data.yaml.tpl` | user default user-data |
| `<node>.network-config.yaml.tpl` | per-node network-config |
| `default.network-config.yaml.tpl` | user default network-config |

Resolution (per VM):

1. `<node>.user-data.yaml.tpl` → `default.user-data.yaml.tpl` → built-in template
2. `<node>.network-config.yaml.tpl` → `default.network-config.yaml.tpl` → (LXD auto-DHCP)

Files in this directory are gitignored except this README. When you override `user-data`, you own
the whole cloud-init user-data — including the `/shared` mount `runcmd` (reproduce it with the
`shared_enabled` var if needed).