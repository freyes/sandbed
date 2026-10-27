variable "project" {
  type        = string
  description = "LXD project owned by this lab (also used as the resource-name prefix)."
}

variable "mgmt_cidr" {
  type = string
}
