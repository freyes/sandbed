from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass
class Config:
    provider: str
    action: str
    # networking
    mgmt_cidr: str = "192.168.200.0/24"
    external_cidr: str = "172.16.2.0/24"
    generic_cidr: str = "172.16.1.0/24"
    # VM resources
    vcpus: int = 4
    memory: int = 8192
    disk: int = 100
    obs_count: int = 3
    landscape_count: int = 3
    # images / storage
    ubuntu_image_remote: str = "ubuntu"
    ubuntu_image_alias: str = "26.04"
    ceph_pool_name: str = "ceph"
    ceph_rbd_pool: str = "rbd"
    ceph_cluster_name: str = "ceph"
    ceph_user_name: str = "admin"
    microceph_osd_size: int = 50
    microceph_osd_img: str = "/var/snap/microceph/common/osd.img"
    microceph_disk_size: str = "20G"
    # shared dir
    shared_dir: str = ""
    # maas
    maas_name: str = ""
    maas_url: str = ""
    maas_token: str = ""
    maas_space: str = "myspace"
    lxd_https_port: int = 8443
    maas_admin_password: str = "changeme"
    maas_channel: str = "3.6/stable"
    maas_vcpus: int = 4
    maas_memory: int = 8192
    maas_disk: int = 50
    maas_deploy_series: str = "noble"
    maas_image_sync_timeout: str = "60m"
    maas_upstream_dns: str = "1.1.1.1"
    maas_parallelism: int = 10
    internal_api_range: str = ""
    public_api_range: str = ""
    floating_ip_range: str = ""
    # sunbeam
    region: str = "RegionOne"
    db_topology: str = "single"
    project_network: str = "192.168.0.0/24"
    ssh_key: str = ""
    ssh_host: str = ""
    vm_user: str = "ubuntu"
    # hooks
    hooks_dir: str = "hooks"
    no_hooks: bool = False
    # cloud-init templating
    cloud_init_templates_dir: str = "templates/cloud-init"
    # testflinger
    use_testflinger: bool = False
    testflinger_series: str = "resolute"
    testflinger_queue: str = ""
    testflinger_timeout: str = "6h"
    testflinger_job: str = ""
    testflinger_job_file: str = ""
    testflinger_key: list = field(default_factory=list)
    # internals
    terraform_dir: str = "terraform"
    state_file: str = field(
        default_factory=lambda: os.path.expanduser("~/.cache/sandbed/state.json")
    )


SSH_PUBLIC_KEY_FILES = ("id_ed25519.pub", "id_ecdsa.pub", "id_rsa.pub")


def default_ssh_public_key() -> str:
    """First public key under ~/.ssh, or "" when there is none."""
    ssh_dir = os.path.expanduser("~/.ssh")
    for name in SSH_PUBLIC_KEY_FILES:
        path = os.path.join(ssh_dir, name)
        if os.path.isfile(path):
            with open(path) as fh:
                key = fh.read().strip()
            if key:
                return key
    return ""


def load_config(provider: str, action: str, **overrides) -> Config:
    cfg = Config(provider=provider, action=action)
    if not cfg.ssh_key:
        cfg.ssh_key = os.environ.get("SUNBEAM_SSH_KEY", "")
    for key, value in overrides.items():
        if value is not None and hasattr(cfg, key):
            if isinstance(value, tuple):
                value = list(value)
            setattr(cfg, key, value)
    if provider == "maas" and not cfg.maas_name:
        cfg.maas_name = "sandbed"
    # The maas flow ssh-es into the client VM, so it needs a key that cloud-init authorizes.
    if provider == "maas" and not cfg.ssh_key:
        cfg.ssh_key = default_ssh_public_key()
    return cfg