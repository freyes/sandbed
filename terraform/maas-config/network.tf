# The rack controller discovers the generic fabric, VLAN and subnet by itself; import them so they
# can be configured (space, gateway, DNS) instead of colliding with the discovered objects.
data "maas_fabric" "generic" {
  name = var.generic_fabric
}

import {
  to = maas_vlan.generic
  id = "${var.generic_fabric}:${var.generic_vid}"
}

import {
  to = maas_subnet.generic
  id = var.generic_cidr
}

resource "maas_space" "sunbeam" {
  name = var.maas_space
}

resource "maas_vlan" "generic" {
  fabric = data.maas_fabric.generic.id
  vid    = var.generic_vid
  space  = maas_space.sunbeam.name
}

resource "maas_subnet" "generic" {
  cidr        = var.generic_cidr
  name        = "generic"
  gateway_ip  = local.gateway_ip
  dns_servers = [local.maas_ip]
}

resource "maas_subnet_ip_range" "dynamic" {
  subnet   = maas_subnet.generic.id
  type     = "dynamic"
  start_ip = cidrhost(var.generic_cidr, 100)
  end_ip   = cidrhost(var.generic_cidr, 200)
  comment  = "sandbed dynamic range (enlist and commission)"
}

resource "maas_subnet_ip_range" "internal_api" {
  subnet   = maas_subnet.generic.id
  type     = "reserved"
  start_ip = cidrhost(var.generic_cidr, 20)
  end_ip   = cidrhost(var.generic_cidr, 39)
  comment  = "${var.maas_name}-internal-api"
}

resource "maas_subnet_ip_range" "public_api" {
  subnet   = maas_subnet.generic.id
  type     = "reserved"
  start_ip = cidrhost(var.generic_cidr, 40)
  end_ip   = cidrhost(var.generic_cidr, 59)
  comment  = "${var.maas_name}-public-api"
}

data "maas_rack_controller" "maas" {
  hostname = local.deployed.maas_instance
}

resource "maas_vlan_dhcp" "generic" {
  fabric                  = tonumber(data.maas_fabric.generic.id)
  vlan                    = var.generic_vid
  primary_rack_controller = data.maas_rack_controller.maas.id
  ip_ranges               = [maas_subnet_ip_range.dynamic.id]

  depends_on = [maas_vlan.generic, maas_subnet.generic]
}
