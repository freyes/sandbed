locals {
  user_data = templatefile("${path.module}/cloud-init/user-data.yaml.tpl", {
    hostname = var.vm_name
    user     = var.vm_user
    ssh_key  = var.ssh_key
  })
}

resource "lxd_profile" "lab" {
  project     = var.project
  name        = "sandbed-dev"
  description = "sandbed dev lab profile (replaces the host default profile)"

  device {
    name = "root"
    type = "disk"
    properties = {
      path = "/"
      pool = var.storage_pool
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
}

resource "lxd_storage_volume" "sdb" {
  project      = var.project
  name         = "${var.vm_name}-sdb"
  pool         = var.storage_pool
  content_type = "block"

  config = {
    size = var.sdb_size
  }
}

resource "lxd_instance" "cloud" {
  project  = var.project
  name     = var.vm_name
  type     = "virtual-machine"
  image    = var.image_fingerprint
  profiles = [lxd_profile.lab.name]

  timeouts = {
    create = "10m"
  }

  config = {
    "limits.cpu"     = tostring(var.vcpus)
    "limits.memory"  = "${var.memory}MiB"
    "boot.autostart" = "false"
    "user.user-data" = local.user_data
  }

  device {
    name = "sdb"
    type = "disk"
    properties = {
      pool   = var.storage_pool
      source = lxd_storage_volume.sdb.name
    }
  }

  wait_for {
    type = "ipv4"
    nic  = "eth0"
  }
}
