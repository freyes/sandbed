variable "vcpus" {
  type    = number
  default = 4
}

variable "memory" {
  type    = number
  default = 8192
}

variable "disk" {
  type    = number
  default = 100
}

variable "sdb_size" {
  type    = string
  default = "20GiB"
}

variable "obs_count" {
  type    = number
  default = 3
}

variable "landscape_count" {
  type    = number
  default = 3
}

variable "mgmt_network_name" {
  type = string
}

variable "ceph_pool_name" {
  type = string
}

variable "image_fingerprint" {
  type = string
}

variable "shared_dir" {
  type    = string
  default = ""
}

variable "ssh_key" {
  type = string
}

variable "vm_user" {
  type    = string
  default = "ubuntu"
}

variable "generic_network_name" {
  type    = string
  default = "generic"
}

variable "generic_cidr" {
  type    = string
  default = "172.16.1.0/24"
}

variable "external_network_name" {
  type    = string
  default = "external"
}

variable "external_cidr" {
  type    = string
  default = "172.16.2.0/24"
}

variable "generic_parent_bridge" {
  type    = string
  default = "br0"
}

variable "cloud_init_templates_dir" {
  type    = string
  default = "templates/cloud-init"
}