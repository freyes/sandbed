# LXD side of MAAS power control: LXD listens on the `generic` gateway address and trusts a
# client certificate that MAAS presents through its `lxd` power driver.
resource "lxd_server" "https" {
  config = {
    "core.https_address" = "${local.gateway_ip}:${var.lxd_https_port}"
  }

  depends_on = [lxd_network.generic]
}

resource "tls_private_key" "lxd_power" {
  algorithm = "RSA"
  rsa_bits  = 4096
}

resource "tls_self_signed_cert" "lxd_power" {
  private_key_pem       = tls_private_key.lxd_power.private_key_pem
  validity_period_hours = 87600
  allowed_uses          = ["client_auth", "digital_signature", "key_encipherment"]

  subject {
    common_name = "sandbed-maas"
  }
}

resource "lxd_trust_certificate" "maas" {
  name    = "sandbed-maas"
  content = tls_self_signed_cert.lxd_power.cert_pem

  depends_on = [lxd_server.https]
}

# One machine runs the MAAS region and rack controllers; maas-test-db provides PostgreSQL.
resource "lxd_instance" "maas" {
  name  = "maas-1"
  type  = "virtual-machine"
  image = var.image_fingerprint

  timeouts = {
    create = "45m"
  }

  config = {
    "limits.cpu"          = tostring(var.maas_vcpus)
    "limits.memory"       = "${var.maas_memory}MiB"
    "boot.autostart"      = "true"
    "user.user-data"      = local.cloud_init["maas-1"].user_data
    "user.network-config" = local.cloud_init["maas-1"].network_config
  }

  device {
    name = "root"
    type = "disk"
    properties = {
      path = "/"
      pool = var.ceph_pool_name
      size = "${var.maas_disk}GiB"
    }
  }

  device {
    name = "eth0"
    type = "nic"
    properties = {
      network = lxd_network.generic.name
      hwaddr  = local.maas_mac
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

  file {
    content            = file("${path.module}/files/maas-bootstrap.sh")
    target_path        = "/usr/local/sbin/maas-bootstrap.sh"
    mode               = "0755"
    create_directories = true
  }

  file {
    content            = file("${path.module}/files/maas-wait-rack-images.sh")
    target_path        = "/usr/local/sbin/maas-wait-rack-images.sh"
    mode               = "0755"
    create_directories = true
  }

  file {
    content            = file("${path.module}/files/maas-tag-nic.sh")
    target_path        = "/usr/local/sbin/maas-tag-nic.sh"
    mode               = "0755"
    create_directories = true
  }

  wait_for {
    type = "agent"
  }

  execs = {
    "10-bootstrap" = {
      command       = ["/usr/local/sbin/maas-bootstrap.sh"]
      trigger       = "once"
      fail_on_error = true
      environment = {
        MAAS_ADMIN_PASSWORD = var.maas_admin_password
        MAAS_CHANNEL        = var.maas_channel
        MAAS_URL            = local.maas_url
        MAAS_GENERIC_CIDR   = var.generic_cidr
      }
    }
    "20-apikey" = {
      command       = ["/snap/bin/maas", "apikey", "--username", "admin"]
      trigger       = "once"
      record_output = true
      fail_on_error = true
    }
  }

  depends_on = [lxd_network.generic]
}
