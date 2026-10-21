# Everything here is owned by one lab. The shared storage pool is deliberately NOT managed here:
# it belongs to `sandbed dev host prepare` and is only referenced by name from dev-manual.

resource "lxd_project" "lab" {
  name        = var.project
  description = "sandbed dev lab ${var.project}"

  config = {
    "features.images"          = "true"
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

resource "lxd_image" "ubuntu" {
  project = lxd_project.lab.name

  source_image = {
    image        = "${var.ubuntu_image_remote}:${var.ubuntu_image_alias}"
    type         = "virtual-machine"
    architecture = "x86_64"
    copy_aliases = true
  }
}
