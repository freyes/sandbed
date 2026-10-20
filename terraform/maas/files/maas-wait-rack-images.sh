#!/usr/bin/env bash
# Blocks until the rack controller has synced its boot images from the region (the terraform
# maas provider only waits for the region side). Usage: maas-wait-rack-images.sh <timeout-seconds>
set -euo pipefail
export PATH="$PATH:/snap/bin"

timeout_seconds=${1:?usage: maas-wait-rack-images.sh <timeout-seconds>}
deadline=$(($(date +%s) + timeout_seconds))

system_id=$(maas admin rack-controllers read | jq -r '.[0].system_id')
while :; do
    status=$(maas admin rack-controller list-boot-images "${system_id}" | jq -r '.status')
    echo "[maas-wait-rack-images] rack ${system_id}: ${status}"
    [ "${status}" = "synced" ] && exit 0
    if [ "$(date +%s)" -ge "${deadline}" ]; then
        echo "[maas-wait-rack-images] timed out" >&2
        exit 1
    fi
    sleep 30
done
