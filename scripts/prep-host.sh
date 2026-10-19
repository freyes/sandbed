#!/usr/bin/env bash
set -euo pipefail

# prep-host.sh — install and configure host dependencies for sandbed.
# Idempotent: safe to run multiple times; already-installed/configured pieces
# are skipped.

SNAPS=(lxd microceph)
CLASSIC_SNAPS=(astral-uv terraform)

OSD_SIZE="${1:-4G}"

if [[ "${EUID}" -ne 0 ]]; then
    echo "error: this script must be run as root (or with sudo)" >&2
    exit 1
fi

for snap in "${SNAPS[@]}"; do
    if snap list "${snap}" >/dev/null 2>&1; then
        echo "snap ${snap}: already installed, skipping"
    else
        echo "snap ${snap}: installing"
        snap install "${snap}"
    fi
done

for snap in "${CLASSIC_SNAPS[@]}"; do
    if snap list "${snap}" >/dev/null 2>&1; then
        echo "snap ${snap}: already installed, skipping"
    else
        echo "snap ${snap}: installing (classic)"
        snap install "${snap}" --classic
    fi
done

if ! lxd waitready --timeout=120 >/dev/null 2>&1; then
    echo "error: LXD daemon did not become ready" >&2
    exit 1
fi

if lxc query /1.0 >/dev/null 2>&1; then
    echo "lxd: already initialized, skipping configuration"
else
    echo "lxd: initializing with default configuration"
    lxd init --auto
fi

# Prevent the microceph snap from auto-updating into a broken cluster.
snap refresh --hold microceph >/dev/null 2>&1 || true

if sudo microceph cluster list >/dev/null 2>&1; then
    echo "microceph: cluster already bootstrapped, skipping bootstrap"
else
    echo "microceph: bootstrapping single-node cluster"
    sudo microceph cluster bootstrap
fi

osd_count="$(sudo microceph.ceph osd stat 2>/dev/null | sed -n 's/^\([0-9]*\) osds:.*/\1/p')"
if [ -n "${osd_count}" ] && [ "${osd_count}" -gt 0 ]; then
    echo "microceph: ${osd_count} OSD(s) present, skipping disk setup"
else
    echo "microceph: adding file-backed OSDs (3 x ${OSD_SIZE} loop)"
    sudo microceph disk add "loop,${OSD_SIZE},3"
fi

echo "prep-host: done"
