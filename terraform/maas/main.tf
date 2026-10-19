# Networks: `generic` serves the MAAS PXE space; `external` is NAT'd.
resource "lxd_network" "generic" {
  name = var.generic_network_name
  type = "bridge"

  config = {
    "ipv4.address" = "${cidrhost(var.generic_cidr, 1)}/${split("/", var.generic_cidr)[1]}"
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

# Raw block disk per cloud compute/storage node (MAAS-deployed, gets /dev/sdb).
resource "lxd_storage_volume" "cloud_sdb" {
  count        = 3
  name         = "cloud-${count.index + 1}-sdb"
  pool         = var.ceph_pool_name
  content_type = "block"

  config = {
    size = var.sdb_size
  }
}

locals {
  controllers = [
    "sunbeam-controller-1", "sunbeam-controller-2", "sunbeam-controller-3",
    "juju-controller-1", "juju-controller-2", "juju-controller-3",
  ]
  obs         = [for i in range(var.obs_count) : "observability-${i + 1}"]
  landscape   = [for i in range(var.landscape_count) : "landscape-${i + 1}"]
  provisioned = concat(local.controllers, local.obs, local.landscape, ["sunbeam-client-1"])

  cloud_init_vars = {
    user           = var.vm_user
    ssh_key        = var.ssh_key
    shared_enabled = var.shared_dir != ""
    provider       = "maas"
  }
}

# The cloud-* nodes are emitted empty (image omitted => --empty) with a PXE NIC on
# `generic`, an `external` NIC, and the /dev/sdb disk. MAAS enlists and deploys them.
resource "lxd_instance" "cloud" {
  count = 3
  name  = "cloud-${count.index + 1}"
  type  = "virtual-machine"

  config = {
    "limits.cpu"          = tostring(var.vcpus)
    "limits.memory"       = "${var.memory}MiB"
    "boot.autostart"      = "false"
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
      network         = var.generic_network_name
      "boot.priority" = "10"
    }
  }

  device {
    name = "eth1"
    type = "nic"
    properties = {
      network = var.external_network_name
    }
  }

  device {
    name = "sdb"
    type = "disk"
    properties = {
      pool   = var.ceph_pool_name
      source = lxd_storage_volume.cloud_sdb[count.index].name
    }
  }
}

# Secondary block disk for every provisioned VM (controllers, client, obs, landscape).
resource "lxd_storage_volume" "provisioned_sdb" {
  for_each     = toset(local.provisioned)
  name         = "${each.key}-sdb"
  pool         = var.ceph_pool_name
  content_type = "block"

  config = {
    size = var.sdb_size
  }
}

resource "lxd_instance" "provisioned" {
  for_each = toset(local.provisioned)
  name     = each.key
  type     = "virtual-machine"
  image    = var.image_fingerprint

  timeouts {
    create = "10m"
  }

  config = merge(
    {
      "limits.cpu"            = tostring(var.vcpus)
      "limits.memory"         = "${var.memory}MiB"
      "boot.autostart"        = "true"
      "user.access_interface" = "eth0"
    },
    {
      "user.user-data" = (
        fileexists("${var.cloud_init_templates_dir}/${each.key}.user-data.yaml.tpl")
        ? templatefile("${var.cloud_init_templates_dir}/${each.key}.user-data.yaml.tpl",
        merge(local.cloud_init_vars, { hostname = each.key }))
        : fileexists("${var.cloud_init_templates_dir}/default.user-data.yaml.tpl")
        ? templatefile("${var.cloud_init_templates_dir}/default.user-data.yaml.tpl",
        merge(local.cloud_init_vars, { hostname = each.key }))
        : templatefile("${path.module}/cloud-init/user-data.yaml.tpl",
        merge(local.cloud_init_vars, { hostname = each.key }))
      )
    },
    (
      fileexists("${var.cloud_init_templates_dir}/${each.key}.network-config.yaml.tpl")
      ? {
        "user.network-config" = templatefile("${var.cloud_init_templates_dir}/${each.key}.network-config.yaml.tpl",
        merge(local.cloud_init_vars, { hostname = each.key }))
      }
      : fileexists("${var.cloud_init_templates_dir}/default.network-config.yaml.tpl")
      ? {
        "user.network-config" = templatefile("${var.cloud_init_templates_dir}/default.network-config.yaml.tpl",
        merge(local.cloud_init_vars, { hostname = each.key }))
      }
      : {}
    )
  )

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
      network = var.mgmt_network_name
    }
  }

  device {
    name = "sdb"
    type = "disk"
    properties = {
      pool   = var.ceph_pool_name
      source = lxd_storage_volume.provisioned_sdb[each.key].name
    }
  }

  dynamic "device" {
    for_each = var.shared_dir != "" ? [1] : []
    content {
      name = "shared"
      type = "disk"
      properties = {
        source = var.shared_dir
        path   = "/shared"
      }
    }
  }

  wait_for {
    type = "ipv4"
    nic  = "eth0"
  }
}