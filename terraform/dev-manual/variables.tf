variable "project" {
  type = string
}

variable "storage_pool" {
  type        = string
  description = "Prepared, host-owned LXD pool. Referenced by name; never created or destroyed here."
}

variable "mgmt_network_name" {
  type = string
}

variable "image_fingerprint" {
  type = string
}

variable "vcpus" {
  type    = number
  default = 8
}

variable "memory" {
  type        = number
  default     = 32768
  description = "MiB"
}

variable "disk" {
  type        = number
  default     = 100
  description = "Root disk, GiB"
}

variable "sdb_size" {
  type    = string
  default = "50GiB"
}

variable "ssh_key" {
  type = string
}

variable "vm_user" {
  type    = string
  default = "ubuntu"
}

variable "vm_name" {
  type    = string
  default = "cloud-1"
}
