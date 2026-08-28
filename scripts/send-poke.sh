#!/usr/bin/env bash
# send-poke.sh <channel-dir> <message-file>
#
# The alarm clock, and nothing else. It sends one scheduled trigger message to
# the roster's channel. ZERO work logic lives here: what the agents then do is
# in the runbook, which is why a schedule change never risks the work and a
# work change never risks the schedule.
#
# The transport is deliberately pluggable. Set POKE_TRANSPORT to a command that
# reads the message on stdin and takes --channel plus repeated --mention flags.
# Unset, it prints the poke, which is what the demo and CI do.
set -euo pipefail

dir="${1:?channel dir required}"
msgfile="${2:?message file required}"

[ -f "$dir/poke.conf" ] || { echo "error: $dir/poke.conf not found" >&2; exit 1; }
[ -f "$dir/$msgfile" ]  || { echo "error: $dir/$msgfile not found" >&2; exit 1; }

# poke.conf is DATA, not code: read only known keys, never source it. Sourcing
# a config file hands arbitrary shell execution to whoever can edit the config.
conf_get() {
  # The `|| true` is load-bearing. Optional keys are absent from most configs,
  # grep exits 1 on a missing key, and with pipefail plus `set -e` that kills
  # the sender before it ever sends: a silent, instant exit 1 with no error.
  grep -E "^$1=" "$dir/poke.conf" 2>/dev/null | tail -1 | cut -d= -f2- | tr -d '"' || true
}

CHANNEL="$(conf_get CHANNEL)"
ROSTER="$(conf_get ROSTER)"
ALLOW_EMPTY_ROSTER="$(conf_get ALLOW_EMPTY_ROSTER)"

: "${CHANNEL:?CHANNEL missing in $dir/poke.conf}"

# A poke that mentions nobody wakes nobody, and it fails silently: the workflow
# goes green, the cycle never runs, and the gap is only visible in the work log
# days later. Refuse unless the config explicitly says an unmentioned send is
# intended.
if [ -z "$ROSTER" ] && [ "$ALLOW_EMPTY_ROSTER" != "1" ]; then
  echo "error: ROSTER is empty in $dir/poke.conf and ALLOW_EMPTY_ROSTER=1 is not set" >&2
  exit 1
fi

mention_args=()
for agent in $ROSTER; do
  mention_args+=(--mention "$agent")
done

if [ -n "${POKE_TRANSPORT:-}" ]; then
  exec $POKE_TRANSPORT --channel "$CHANNEL" "${mention_args[@]+"${mention_args[@]}"}" \
    < "$dir/$msgfile"
fi

echo "poke -> channel=$CHANNEL roster=[${ROSTER}]"
echo "---"
cat "$dir/$msgfile"
