# cloud-init is only used by the two VMs that are not enlisted in MAAS: maas-1 and the Sunbeam
# client. Both need static addressing on `generic` (nothing serves DHCP there until MAAS is up),
# so network-config always has an embedded fallback and `default.*` user templates are ignored
# for it; only a per-node `<node>.network-config.yaml.tpl` overrides it.
locals {
  maas_mac           = "00:16:3e:5a:00:02"
  client_generic_mac = "00:16:3e:5a:00:03"
  client_mgmt_mac    = "00:16:3e:5a:00:04"

  cloud_init_vars = {
    user           = var.vm_user
    ssh_key        = var.ssh_key
    shared_enabled = var.shared_dir != ""
    provider       = "maas"
  }

  cloud_init_nodes = {
    "maas-1" = {
      network_template = "${path.module}/cloud-init/network-config-maas.yaml.tpl"
      vars = {
        mac     = local.maas_mac
        address = local.maas_ip
        prefix  = local.generic_prefix
        gateway = local.gateway_ip
        dns     = var.upstream_dns
      }
    }
    "sunbeam-client-1" = {
      network_template = "${path.module}/cloud-init/network-config-client.yaml.tpl"
      vars = {
        mgmt_mac    = local.client_mgmt_mac
        generic_mac = local.client_generic_mac
        address     = local.client_ip
        prefix      = local.generic_prefix
      }
    }
  }

  cloud_init = {
    for name, node in local.cloud_init_nodes : name => {
      user_data = (
        fileexists("${var.cloud_init_templates_dir}/${name}.user-data.yaml.tpl")
        ? templatefile("${var.cloud_init_templates_dir}/${name}.user-data.yaml.tpl",
        merge(local.cloud_init_vars, { hostname = name }))
        : fileexists("${var.cloud_init_templates_dir}/default.user-data.yaml.tpl")
        ? templatefile("${var.cloud_init_templates_dir}/default.user-data.yaml.tpl",
        merge(local.cloud_init_vars, { hostname = name }))
        : templatefile("${path.module}/cloud-init/user-data.yaml.tpl",
        merge(local.cloud_init_vars, { hostname = name }))
      )
      network_config = templatefile(
        fileexists("${var.cloud_init_templates_dir}/${name}.network-config.yaml.tpl")
        ? "${var.cloud_init_templates_dir}/${name}.network-config.yaml.tpl"
        : node.network_template,
        merge(local.cloud_init_vars, node.vars, { hostname = name })
      )
    }
  }
}
