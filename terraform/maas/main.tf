# Networks: `generic` is the PXE network served by MAAS (LXD DHCP stays off); `external` is NAT'd.
resource "lxd_network" "generic" {
  name = var.generic_network_name
  type = "bridge"

  config = {
    "ipv4.address" = "${local.gateway_ip}/${local.generic_prefix}"
    "ipv4.nat"     = "true"
    "ipv4.dhcp"    = "false"
    "ipv6.address" = "none"
  }
}

resource "lxd_network" "external" {
  name = var.external_network_name
  type = "bridge"

  config = {
    "ipv4.address" = "${cidrhost(var.external_cidr, 1)}/${split("/", var.external_cidr)[1]}"
    "ipv4.nat"     = "true"
    "ipv4.dhcp"    = "true"
    "ipv6.address" = "none"
  }
}

locals {
  generic_prefix = split("/", var.generic_cidr)[1]
  gateway_ip     = cidrhost(var.generic_cidr, 1)
  maas_ip        = cidrhost(var.generic_cidr, 2)
  client_ip      = cidrhost(var.generic_cidr, 3)
  maas_url       = "http://${local.maas_ip}:5240/MAAS"
}
