variable "project" {
  type        = string
  description = "LXD project owned by this lab (also used as the resource-name prefix)."
}

variable "mgmt_cidr" {
  type = string
}

variable "ubuntu_image_remote" {
  type    = string
  default = "ubuntu"
}

variable "ubuntu_image_alias" {
  type    = string
  default = "26.04"
}
