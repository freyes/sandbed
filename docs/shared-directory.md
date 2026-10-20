# Shared directory

`--shared-dir=<DIR>` mounts a host directory at `/shared` in **every** VM, using an LXD `disk`
device (a directory source is served to the VM over virtiofs/9p). In the maas provider the
directory is mounted on `maas-1` and `sunbeam-client-1` only; the other nodes are installed by MAAS.

## Requirements

- The host path must exist (verified in preflight).
- No separate `virtiofsd` is needed — LXD serves it directly.
- Guest kernel ≥ 5.4 (satisfied by Ubuntu 26.04).

## Mechanics

Terraform adds, per VM (only when `shared_dir != ""`):

```hcl
device {
  name = "shared"
  type = "disk"
  properties = {
    source = var.shared_dir
    path   = "/shared"
  }
}
```

The built-in cloud-init template mounts it:

```yaml
runcmd:
  - mkdir -p /shared
  - mount -t virtiofs shared /shared
  - echo "shared /shared virtiofs defaults 0 0" >> /etc/fstab
```

If you override the user-data template (see [cloud-init templating](cloud-init-templating.md)), you
own this `runcmd` — reproduce it using the `shared_enabled` template variable.