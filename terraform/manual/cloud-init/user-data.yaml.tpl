#cloud-config
hostname: ${hostname}
users:
  - name: ${user}
    sudo: ALL=(ALL) NOPASSWD:ALL
    shell: /bin/bash
    ssh_authorized_keys:
      - ${ssh_key}
package_update: true
%{ if shared_enabled ~}
runcmd:
  - mkdir -p /shared
  - mount -t virtiofs shared /shared
  - echo "shared /shared virtiofs defaults 0 0" >> /etc/fstab
%{ endif ~}