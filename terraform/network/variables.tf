variable "mgmt_name" {
  type    = string
  default = "mgmt"
}

variable "mgmt_cidr" {
  type    = string
  default = "192.168.200.0/24"
}

variable "ceph_pool_name" {
  type    = string
  default = "ceph"
}

variable "ceph_rbd_pool" {
  type    = string
  default = "rbd"
}

variable "ceph_cluster_name" {
  type    = string
  default = "ceph"
}

variable "ceph_user_name" {
  type    = string
  default = "admin"
}

variable "ubuntu_image_remote" {
  type    = string
  default = "ubuntu"
}

variable "ubuntu_image_alias" {
  type    = string
  default = "26.04"
}
