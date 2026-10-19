locals {
  cloud_init_vars = {
    user           = var.vm_user
    ssh_key        = var.ssh_key
    shared_enabled = var.shared_dir != ""
    provider       = "manual"
  }

  # Per-node cloud-init resolution. user-data has an embedded fallback;
  # network-config falls back to LXD default (null => omitted).
  cloud_init = {
    for i in range(var.vm_count) : tostring(i) => {
      hostname = "cloud-${i + 1}"
      user_data = (
        fileexists("${var.cloud_init_templates_dir}/cloud-${i + 1}.user-data.yaml.tpl")
        ? templatefile("${var.cloud_init_templates_dir}/cloud-${i + 1}.user-data.yaml.tpl",
        merge(local.cloud_init_vars, { hostname = "cloud-${i + 1}" }))
        : fileexists("${var.cloud_init_templates_dir}/default.user-data.yaml.tpl")
        ? templatefile("${var.cloud_init_templates_dir}/default.user-data.yaml.tpl",
        merge(local.cloud_init_vars, { hostname = "cloud-${i + 1}" }))
        : templatefile("${path.module}/cloud-init/user-data.yaml.tpl",
        merge(local.cloud_init_vars, { hostname = "cloud-${i + 1}" }))
      )
      network_config = (
        fileexists("${var.cloud_init_templates_dir}/cloud-${i + 1}.network-config.yaml.tpl")
        ? templatefile("${var.cloud_init_templates_dir}/cloud-${i + 1}.network-config.yaml.tpl",
        merge(local.cloud_init_vars, { hostname = "cloud-${i + 1}" }))
        : fileexists("${var.cloud_init_templates_dir}/default.network-config.yaml.tpl")
        ? templatefile("${var.cloud_init_templates_dir}/default.network-config.yaml.tpl",
        merge(local.cloud_init_vars, { hostname = "cloud-${i + 1}" }))
        : null
      )
    }
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

resource "lxd_storage_volume" "sdb" {
  count        = var.vm_count
  name         = "cloud-${count.index + 1}-sdb"
  pool         = var.ceph_pool_name
  content_type = "block"

  config = {
    size = var.sdb_size
  }
}

resource "lxd_instance" "cloud" {
  count = var.vm_count
  name  = "cloud-${count.index + 1}"
  type  = "virtual-machine"
  image = var.image_fingerprint

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
      "user.user-data" = local.cloud_init[tostring(count.index)].user_data
    },
    local.cloud_init[tostring(count.index)].network_config == null
    ? {}
    : { "user.network-config" = local.cloud_init[tostring(count.index)].network_config }
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
      source = lxd_storage_volume.sdb[count.index].name
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