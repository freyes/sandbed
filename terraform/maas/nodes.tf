# Every node MAAS manages is an empty VM that PXE-boots off `generic`. They stay stopped until
# MAAS powers them on through its `lxd` power driver. MACs are fixed so MAAS can be told about
# each machine before it ever runs.
locals {
  groups = {
    "cloud"              = { names = [for i in range(3) : "cloud-${i + 1}"], base = 0 }
    "sunbeam-controller" = { names = [for i in range(3) : "sunbeam-controller-${i + 1}"], base = 16 }
    "juju-controller"    = { names = [for i in range(3) : "juju-controller-${i + 1}"], base = 32 }
    "observability"      = { names = [for i in range(var.obs_count) : "observability-${i + 1}"], base = 48 }
    "landscape"          = { names = [for i in range(var.landscape_count) : "landscape-${i + 1}"], base = 64 }
  }

  pxe_nodes = merge([
    for role, group in local.groups : {
      for i, name in group.names : name => {
        role         = role
        generic_mac  = format("00:16:3e:5a:01:%02x", group.base + i)
        external_mac = format("00:16:3e:5a:02:%02x", group.base + i)
        external     = role == "cloud"
      }
    }
  ]...)
}

resource "lxd_storage_volume" "sdb" {
  for_each     = local.pxe_nodes
  name         = "${each.key}-sdb"
  pool         = var.ceph_pool_name
  content_type = "block"

  config = {
    size = var.sdb_size
  }
}

resource "lxd_instance" "pxe" {
  for_each = local.pxe_nodes
  name     = each.key
  type     = "virtual-machine"
  running  = false

  config = {
    "limits.cpu"          = tostring(var.vcpus)
    "limits.memory"       = "${var.memory}MiB"
    "boot.autostart"      = "false"
    "security.secureboot" = "false"
  }

  device {
    name = "root"
    type = "disk"
    properties = {
      path = "/"
      pool = var.ceph_pool_name
      size = "${var.disk}GiB"
    }
  }

  device {
    name = "eth0"
    type = "nic"
    properties = {
      network         = lxd_network.generic.name
      hwaddr          = each.value.generic_mac
      "boot.priority" = "10"
    }
  }

  dynamic "device" {
    for_each = each.value.external ? [1] : []
    content {
      name = "eth1"
      type = "nic"
      properties = {
        network = lxd_network.external.name
        hwaddr  = each.value.external_mac
      }
    }
  }

  device {
    name = "sdb"
    type = "disk"
    properties = {
      pool   = var.ceph_pool_name
      source = lxd_storage_volume.sdb[each.key].name
    }
  }
}
