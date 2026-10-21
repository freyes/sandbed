#!/usr/bin/env bash
# Guest-side stage wrapper. Usage: run-stage.sh <stage-dir> <timeout-seconds> -- <argv...>
#
# Runs one command with a deadline and leaves pid/rc/out.log behind, so a controller that
# lost its SSH connection can later ask "did that work stop, and how did it end?" instead of
# guessing. `rc` is written last and atomically; its presence means the work has ended.
set -u
dir="$1"; limit="$2"; shift 2
[ "${1:-}" = "--" ] && shift
[ "$#" -gt 0 ] || { echo "run-stage: no command" >&2; exit 64; }
umask 077
mkdir -p "$dir"
rm -f "$dir/rc"
echo $$ > "$dir/pid"
set -o pipefail
timeout --foreground --kill-after=10 "$limit" "$@" 2>&1 | tee -a "$dir/out.log"
rc=${PIPESTATUS[0]}
echo "$rc" > "$dir/rc.tmp" && mv "$dir/rc.tmp" "$dir/rc"
exit "$rc"
