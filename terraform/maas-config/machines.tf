locals {
  machines = local.deployed.machines

  node_tags = {
    for name, machine in local.machines : name => concat(
      contains(var.deployment_roles, machine.role) ? ["openstack-${var.maas_name}"] : [],
      var.role_tags[machine.role],
    )
  }

  tag_names = toset(flatten(values(local.node_tags)))

  cloud_machines = { for name, machine in local.machines : name => machine if machine.role == "cloud" }
}

# Adding a machine commissions it (MAAS powers the VM on over the lxd driver) and waits for Ready.
resource "maas_machine" "node" {
  for_each = local.machines

  hostname        = each.key
  architecture    = "amd64/generic"
  power_type      = "lxd"
  pxe_mac_address = each.value.generic_mac
  power_parameters = jsonencode({
    power_address = local.deployed.lxd_power_address
    instance_name = each.key
    project       = var.lxd_project
    certificate   = local.deployed.lxd_power_cert
    key           = local.deployed.lxd_power_key
  })

  timeouts {
    create = var.machine_timeout
  }

  depends_on = [
    maas_vlan_dhcp.generic,
    maas_ssh_keys.admin,
    maas_configuration.default_distro_series,
    terraform_data.rack_images_synced,
  ]
}

resource "maas_tag" "role" {
  for_each = local.tag_names

  name     = each.key
  machines = [for name, tags in local.node_tags : maas_machine.node[name].id if contains(tags, each.key)]
}

# Ceph OSD disk: /dev/sdb on the cloud nodes.
resource "maas_block_device_tag" "ceph" {
  for_each = local.cloud_machines

  machine         = maas_machine.node[each.key].id
  block_device_id = one([for device in maas_machine.node[each.key].block_devices : device.id if device.name == "sdb"])
  tags            = ["ceph"]
}

# External NIC: neutron:physnet1 tag, no IP. The provider cannot look an interface up by MAC,
# so a small script does it through the MAAS CLI inside maas-1.
resource "terraform_data" "external_nic" {
  for_each = local.cloud_machines

  provisioner "local-exec" {
    command = "lxc exec ${local.deployed.maas_instance} -- /usr/local/sbin/maas-tag-nic.sh ${each.key} ${each.value.external_mac} neutron:physnet1"
  }

  depends_on = [maas_machine.node]
}
