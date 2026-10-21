output "project" { value = lxd_project.lab.name }
output "mgmt_network_name" { value = lxd_network.mgmt.name }
output "ubuntu_image_fingerprint" { value = lxd_image.ubuntu.fingerprint }
