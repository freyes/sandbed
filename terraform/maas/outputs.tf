output "maas_url" {
  value = local.maas_url
}

output "maas_api_key" {
  value     = trimspace(lxd_instance.maas.execs["20-apikey"].stdout)
  sensitive = true
}

output "maas_admin_password" {
  value     = var.maas_admin_password
  sensitive = true
}

output "maas_instance" {
  value = lxd_instance.maas.name
}

output "lxd_power_address" {
  value = "${local.gateway_ip}:${var.lxd_https_port}"
}

output "lxd_power_cert" {
  value     = tls_self_signed_cert.lxd_power.cert_pem
  sensitive = true
}

output "lxd_power_key" {
  value     = tls_private_key.lxd_power.private_key_pem
  sensitive = true
}

output "machines" {
  description = "Machines MAAS enlists: role and the fixed MACs of their NICs."
  value = {
    for name, node in local.pxe_nodes : name => {
      role         = node.role
      generic_mac  = node.generic_mac
      external_mac = node.external ? node.external_mac : null
    }
  }
}

output "client_name" {
  value = lxd_instance.client.name
}

output "client_mgmt_ip" {
  value = lxd_instance.client.ipv4_address
}
