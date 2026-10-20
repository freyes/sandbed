# The Sunbeam client is the one machine that is not enlisted in MAAS. eth0 (`mgmt`) is for host
# access; eth1 (`generic`) keeps its MAAS and Juju traffic on the PXE network.
resource "lxd_instance" "client" {
  name  = "sunbeam-client-1"
  type  = "virtual-machine"
  image = var.image_fingerprint

  timeouts = {
    create = "10m"
  }

  config = {
    "limits.cpu"            = tostring(var.vcpus)
    "limits.memory"         = "${var.memory}MiB"
    "boot.autostart"        = "true"
    "user.access_interface" = "eth0"
    "user.user-data"        = local.cloud_init["sunbeam-client-1"].user_data
    "user.network-config"   = local.cloud_init["sunbeam-client-1"].network_config
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
      network = var.mgmt_network_name
      hwaddr  = local.client_mgmt_mac
    }
  }

  device {
    name = "eth1"
    type = "nic"
    properties = {
      network = lxd_network.generic.name
      hwaddr  = local.client_generic_mac
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
