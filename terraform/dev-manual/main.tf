locals {
  user_data = templatefile("${path.module}/cloud-init/user-data.yaml.tpl", {
    hostname = var.vm_name
    user     = var.vm_user
    ssh_key  = var.ssh_key
  })

  # LXD hands QEMU the file behind the extra disk over the QMP socket. Inside an LXD container
  # AppArmor checks the receiving QEMU profile for that file, and the generated profile only
  # covers the VM's own directory, so the VM fails to start with "No file descriptor supplied
  # via SCM_RIGHTS". Allow exactly this lab's volume directory (snap LXD layout).
  sdb_apparmor = "/var/snap/lxd/common/lxd/storage-pools/${var.storage_pool}/custom/${var.project}_${var.vm_name}-sdb/** rwk,"
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
    "raw.apparmor"   = local.sdb_apparmor
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

  # No `nic`: the provider matches it against the guest's interface name, which cloud-init
  # without a network-config leaves as the predictable name (enp5s0), never the device name.
  wait_for {
    type = "ipv4"
  }
}
