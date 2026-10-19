propose me a plan to create a new project that deploys sunbeam


# Stack

- terraform, 
- libvirt + qemu
- Python
- Ubuntu 26.06

# Requirements

- there is a script that's used as entry point
- terraform is installed from hashicorp's apt repo
- networking is provided by libvirt
- virtual network is named "mgmt" and the CIDR is a parameter that defaults to 192.168.200.0/24
- support maas and manual provider

# Topology

## Manual provider

- 3 VMs to host sunbeam
- VMs default resources: 4 vcpus, 8G of memory, 100G of disk

## MAAS provider

- produce the VMs needed to implement the reference architecture documented at https://canonical.com/openstack/docs/2024.1/reference/example-physical-configuration/
