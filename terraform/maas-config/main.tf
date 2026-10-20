locals {
  sync_unit    = substr(var.image_sync_timeout, length(var.image_sync_timeout) - 1, 1)
  sync_amount  = tonumber(substr(var.image_sync_timeout, 0, length(var.image_sync_timeout) - 1))
  sync_seconds = local.sync_amount * (local.sync_unit == "h" ? 3600 : 60)

  gateway_ip = cidrhost(var.generic_cidr, 1)
  maas_ip    = cidrhost(var.generic_cidr, 2)
}

# --- server settings -------------------------------------------------------------------------

resource "maas_configuration" "default_osystem" {
  key   = "default_osystem"
  value = "ubuntu"
}

resource "maas_configuration" "default_distro_series" {
  key   = "default_distro_series"
  value = var.deploy_series
}

resource "maas_configuration" "upstream_dns" {
  key   = "upstream_dns"
  value = var.upstream_dns
}

resource "maas_ssh_keys" "admin" {
  keys = [var.ssh_key]
}

# --- images: blocks until every selected image has synced -------------------------------------

data "maas_boot_source" "default" {}

data "maas_configuration" "commissioning_series" {
  key = "commissioning_distro_series"
}

resource "maas_boot_source_selection" "deploy" {
  boot_source = tonumber(data.maas_boot_source.default.id)
  os          = "ubuntu"
  release     = var.deploy_series
  arches      = ["amd64"]

  timeouts {
    create = var.image_sync_timeout
    update = var.image_sync_timeout
  }
}

# The commissioning image is selected by MAAS itself; wait for it too when it is a different series.
resource "maas_boot_source_selection" "commissioning" {
  count = data.maas_configuration.commissioning_series.value == var.deploy_series ? 0 : 1

  boot_source = tonumber(data.maas_boot_source.default.id)
  os          = "ubuntu"
  release     = data.maas_configuration.commissioning_series.value
  arches      = ["amd64"]

  timeouts {
    create = var.image_sync_timeout
    update = var.image_sync_timeout
  }
}

# The provider only waits for the region; the rack controller must have the images before it
# can PXE-boot machines.
resource "terraform_data" "rack_images_synced" {
  provisioner "local-exec" {
    command = "lxc exec ${local.deployed.maas_instance} -- /usr/local/sbin/maas-wait-rack-images.sh ${local.sync_seconds}"
  }

  depends_on = [
    maas_boot_source_selection.deploy,
    maas_boot_source_selection.commissioning,
  ]
}
