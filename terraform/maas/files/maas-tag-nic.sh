#!/usr/bin/env bash
# Tags the NIC with the given MAC on a MAAS machine and leaves it unconfigured (no IP), as Sunbeam
# expects for the external NIC. Idempotent.
# Usage: maas-tag-nic.sh <hostname> <mac-address> <tag>
set -euo pipefail
export PATH="$PATH:/snap/bin"

hostname=${1:?usage: maas-tag-nic.sh <hostname> <mac-address> <tag>}
mac=${2:?}
tag=${3:?}

system_id=$(maas admin machines read hostname="${hostname}" | jq -r '.[0].system_id')
interface=$(maas admin interfaces read "${system_id}" |
    jq -c --arg mac "${mac}" '.[] | select(.mac_address == $mac)')
[ -n "${interface}" ] || { echo "no interface ${mac} on ${hostname}" >&2; exit 1; }
interface_id=$(jq -r '.id' <<<"${interface}")

for link_id in $(jq -r '.links[].id' <<<"${interface}"); do
    maas admin interface unlink-subnet "${system_id}" "${interface_id}" id="${link_id}" >/dev/null
done
maas admin interface link-subnet "${system_id}" "${interface_id}" mode=LINK_UP >/dev/null
maas admin interface update "${system_id}" "${interface_id}" tags="${tag}" >/dev/null
echo "[maas-tag-nic] ${hostname} ${mac}: tagged ${tag}, unconfigured"
