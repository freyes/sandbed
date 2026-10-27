# Everything here is owned by one lab. The shared storage pool is deliberately NOT managed here:
# it belongs to `sandbed dev host prepare` and is only referenced by name from dev-manual.

resource "lxd_project" "lab" {
  name        = var.project
  description = "sandbed dev lab ${var.project}"

  config = {
    # The pinned VM image lives in LXD's default project (staged by `sandbed dev host prepare`)
    # and is shared: the lab's instance references it by fingerprint. Copying it into the project
    # would need LXD to listen on the network (the provider cannot copy over the unix socket).
    "features.images"          = "false"
    "features.profiles"        = "true"
    "features.storage.volumes" = "true"
    # Bridge networks cannot be project-scoped (only OVN can), so they stay global and are
    # told apart by name.
    "features.networks" = "false"
  }
}

resource "lxd_network" "mgmt" {
  name = "${var.project}-m"
  type = "bridge"

  config = {
    "ipv4.address" = "${cidrhost(var.mgmt_cidr, 1)}/${split("/", var.mgmt_cidr)[1]}"
    "ipv4.nat"     = "true"
    "ipv4.dhcp"    = "true"
    "ipv6.address" = "none"
  }
}
