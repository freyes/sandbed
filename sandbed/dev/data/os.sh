#!/usr/bin/env bash
# Run INSIDE the lab VM: the openstack CLI with the lab's private credentials.
# Usage: os.sh <openstack arguments...>
set -euo pipefail
set -a
# shellcheck disable=SC1091
. "$HOME/sandbed/openrc"
set +a
exec openstack "$@"