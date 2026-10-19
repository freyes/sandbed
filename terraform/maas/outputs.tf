output "cloud_names" {
  value = [for i in range(3) : "cloud-${i + 1}"]
}

output "controller_names" {
  value = local.controllers
}

output "client_name" {
  value = "sunbeam-client-1"
}

output "vm_mgmt_ips" {
  value = { for k, inst in lxd_instance.provisioned : k => inst.ipv4_address }
}

output "vm_macs" {
  value = { for k, inst in lxd_instance.provisioned : k => inst.interfaces }
}