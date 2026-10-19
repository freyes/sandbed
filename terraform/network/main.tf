resource "lxd_network" "mgmt" {
  name = var.mgmt_name
  type = "bridge"

  config = {
    "ipv4.address" = "${cidrhost(var.mgmt_cidr, 1)}/${split("/", var.mgmt_cidr)[1]}"
    "ipv4.nat"     = "true"
    "ipv4.dhcp"    = "true"
    "ipv6.address" = "none"
  }
}

resource "lxd_storage_pool" "ceph" {
  name   = var.ceph_pool_name
  driver = "ceph"

  config = {
    "ceph.cluster_name"  = var.ceph_cluster_name
    "ceph.user.name"     = var.ceph_user_name
    "ceph.osd.pool_name" = var.ceph_rbd_pool
  }
}

resource "lxd_image" "ubuntu" {
  source_image = {
    image        = "${var.ubuntu_image_remote}:${var.ubuntu_image_alias}"
    type         = "virtual-machine"
    architecture = "x86_64"
    copy_aliases = true
  }
}