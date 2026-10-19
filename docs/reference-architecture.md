# Reference architecture

This project maps to Canonical's OpenStack 2024.1 reference architecture
("Example physical configuration"), but is intentionally a **simplified dev-box** realisation.

## Implemented

- Hyperconverged control + compute + storage (manual): 3 nodes running `control`, `compute`,
  `storage` roles, Ceph via microceph with `/dev/sdb` as OSD.
- MAAS-driven lifecycle: MAAS enlist/commission → Juju/Sunbeam deploy, `lxd` power driver.
- Separate management (`mgmt`), external (`external`), and PXE (`generic`) networks.

## Divergences (by design)

- **Hypervisor**: LXD VMs instead of bare metal / KVM-with-libvirt.
- **Storage**: single-node host microceph (file-backed loop OSD) feeding a nested guest microceph —
  a dev-box convenience, not a production Ceph layout.
- **MAAS topology**: simplified to 10 core VMs (+ optional observability/landscape) rather than the
  21-machine physical reference.
- **Ubuntu 26.04 only**: the 2024.1 how-tos target 24.04 LTS; no fallback.

## Out of scope

MAAS region/rack install, standalone OpenStack operations (upgrade/backup/scale), and any
non-LXD target.