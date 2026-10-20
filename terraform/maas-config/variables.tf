variable "deploy_series" {
  description = "Ubuntu series MAAS deploys on the machines."
  type        = string
  default     = "noble"
}

variable "maas_name" {
  description = "Sunbeam deployment name; machines get the openstack-<name> tag."
  type        = string
  default     = "sandbed"
}

variable "maas_space" {
  type    = string
  default = "myspace"
}

variable "generic_cidr" {
  type    = string
  default = "172.16.1.0/24"
}

variable "generic_fabric" {
  description = "Name MAAS gave the fabric it discovered for the generic network."
  type        = string
  default     = "fabric-0"
}

variable "generic_vid" {
  type    = number
  default = 0
}

variable "ssh_key" {
  type = string
}

variable "upstream_dns" {
  type    = string
  default = "1.1.1.1"
}

variable "lxd_project" {
  type    = string
  default = "default"
}

variable "image_sync_timeout" {
  description = "How long to wait for boot images to sync, as <N>m or <N>h."
  type        = string
  default     = "60m"

  validation {
    condition     = can(regex("^[0-9]+[mh]$", var.image_sync_timeout))
    error_message = "image_sync_timeout must look like 60m or 2h."
  }
}

variable "machine_timeout" {
  description = "How long each machine may take to commission."
  type        = string
  default     = "45m"
}

variable "role_tags" {
  description = "MAAS machine tags per role, besides openstack-<maas_name>."
  type        = map(list(string))
  default = {
    "cloud"              = ["control", "compute", "storage"]
    "sunbeam-controller" = ["sunbeam"]
    "juju-controller"    = ["juju-controller"]
    "observability"      = ["observability"]
    "landscape"          = ["landscape"]
  }
}

variable "deployment_roles" {
  description = "Roles Sunbeam deploys onto; they get the openstack-<maas_name> tag."
  type        = list(string)
  default     = ["cloud", "sunbeam-controller", "juju-controller"]
}
