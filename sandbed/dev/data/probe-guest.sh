#!/usr/bin/env bash
# Run INSIDE the lab VM. Usage: probe-guest.sh <private-key> <floating-ip> <deadline-seconds>
# Waits until the launched instance accepts SSH, then until its cloud-init has finished.
# Exit 0 only when `cloud-init status` reports "status: done" from the instance itself.
set -u
key="$1"; ip="$2"; limit="$3"
[[ "$ip" =~ ^[0-9]{1,3}(\.[0-9]{1,3}){3}$ ]] || { echo "probe-guest: bad address" >&2; exit 64; }
[[ "$limit" =~ ^[0-9]+$ ]] || { echo "probe-guest: bad deadline" >&2; exit 64; }
end=$(( $(date +%s) + limit ))
while :; do
    out="$(ssh -i "$key" -o BatchMode=yes -o IdentitiesOnly=yes -o ConnectTimeout=10 \
        -o StrictHostKeyChecking=accept-new -o UserKnownHostsFile=/dev/null \
        "ubuntu@$ip" -- cloud-init status --wait 2>&1)" && {
        echo "$out"
        grep -q '^status: done' <<<"$out" && exit 0
        echo "probe-guest: cloud-init did not finish cleanly" >&2
        exit 1
    }
    [ "$(date +%s)" -lt "$end" ] || { echo "$out"; echo "probe-guest: deadline reached" >&2; exit 1; }
    sleep 5
done