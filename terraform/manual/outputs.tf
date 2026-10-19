output "vm_names" {
  value = [for i in range(var.vm_count) : "cloud-${i + 1}"]
}

output "vm_ssh_ips" {
  value = {
    for i, inst in lxd_instance.cloud : "cloud-${i + 1}" => inst.ipv4_address
  }
}