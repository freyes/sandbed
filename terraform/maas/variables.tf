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

  validation {
    condition     = var.obs_count >= 0 && var.obs_count <= 16
    error_message = "obs_count must be between 0 and 16."
  }
}

variable "landscape_count" {
  type    = number
  default = 3

  validation {
    condition     = var.landscape_count >= 0 && var.landscape_count <= 16
    error_message = "landscape_count must be between 0 and 16."
  }
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
variable "maas_vcpus" {
  type    = number
  default = 4
}

variable "maas_memory" {
  type    = number
  default = 8192
}

variable "maas_disk" {
  type    = number
  default = 50
}

variable "maas_channel" {
  type    = string
  default = "3.6/stable"
}

variable "maas_admin_password" {
  type      = string
  default   = "changeme"
  sensitive = true
}

variable "upstream_dns" {
  type    = string
  default = "1.1.1.1"
}

variable "lxd_https_port" {
  type    = number
  default = 8443
}
