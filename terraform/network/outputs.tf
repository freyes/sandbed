output "mgmt_network_name" { value = lxd_network.mgmt.name }
output "mgmt_cidr" { value = var.mgmt_cidr }
output "ceph_pool_name" { value = lxd_storage_pool.ceph.name }
output "ubuntu_image_fingerprint" { value = lxd_image.ubuntu.fingerprint }