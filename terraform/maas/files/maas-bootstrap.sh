#!/usr/bin/env bash
# Installs and initialises a single-node MAAS (region+rack, maas-test-db as the database), creates
# the admin user and logs in as profile `admin`. Idempotent. Runs inside maas-1, driven by
# terraform through the lxd_instance `execs` block.
#
# Required environment: MAAS_ADMIN_PASSWORD MAAS_CHANNEL MAAS_URL MAAS_GENERIC_CIDR
set -euo pipefail
export PATH="$PATH:/snap/bin"
export DEBIAN_FRONTEND=noninteractive

: "${MAAS_ADMIN_PASSWORD:?}" "${MAAS_CHANNEL:?}" "${MAAS_URL:?}" "${MAAS_GENERIC_CIDR:?}"

MARKER=/var/lib/sandbed/maas-initialised
log() { echo "[maas-bootstrap] $*"; }

wait_for() { # wait_for <seconds> <description> <command...>
    local deadline=$(($(date +%s) + $1)) description=$2
    shift 2
    until "$@" >/dev/null 2>&1; do
        if [ "$(date +%s)" -ge "$deadline" ]; then
            log "timed out waiting for ${description}" >&2
            return 1
        fi
        sleep 5
    done
}

log "waiting for cloud-init and snapd"
cloud-init status --wait >/dev/null 2>&1 || true # non-zero when degraded; not fatal here
snap wait system seed.loaded

if ! command -v jq >/dev/null; then
    apt-get update -qq
    apt-get install -y -qq jq
fi

snap list maas-test-db >/dev/null 2>&1 || snap install maas-test-db
snap list maas >/dev/null 2>&1 || snap install maas --channel="${MAAS_CHANNEL}"

if [ ! -f "${MARKER}" ]; then
    log "initialising MAAS (region+rack)"
    maas init region+rack --database-uri maas-test-db:/// --maas-url "${MAAS_URL}" --force
    mkdir -p "$(dirname "${MARKER}")"
    touch "${MARKER}"
fi

log "waiting for the MAAS API"
wait_for 600 "the MAAS API" curl -fsS "${MAAS_URL}/api/2.0/version/"

if ! maas apikey --username admin >/dev/null 2>&1; then
    log "creating the admin user"
    maas createadmin --username admin --password "${MAAS_ADMIN_PASSWORD}" \
        --email admin@sandbed.local
fi

maas login admin "${MAAS_URL}" "$(maas apikey --username admin)" >/dev/null

log "waiting for the rack controller and the generic subnet"
wait_for 900 "the rack controller" \
    bash -c "maas admin rack-controllers read | jq -e 'length > 0'"
wait_for 900 "subnet ${MAAS_GENERIC_CIDR}" \
    bash -c "maas admin subnets read | jq -e --arg c '${MAAS_GENERIC_CIDR}' 'any(.[]; .cidr == \$c)'"

log "done"
