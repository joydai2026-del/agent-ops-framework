#!/usr/bin/env bash
# The second clock.
#
# Hosted cron is the primary schedule because its history is visible to anyone
# who asks "did it run this morning". It is also late at busy hours and drops a
# scheduled run outright on occasion. This runs the same poke from a machine the
# desk owner controls, so a dropped hosted run costs nothing.
#
# It is safe to run both. The poke is not the work: whichever agent answers
# first claims the cycle, and the rest stand down, so a duplicate poke produces
# one cycle, not two.
#
# Install as a cron entry on the desk machine (10 minutes after the hosted run,
# so the hosted one wins the normal case and this one only covers a miss):
#
#   5 9 * * 1-5  /path/to/agent-ops-framework/scripts/backup-clock.sh morning
#
# Or as a launchd job on macOS, or a systemd timer on Linux. The mechanism does
# not matter; being a second, independent one does.
set -euo pipefail

slot="${1:-morning}"
root="$(cd "$(dirname "$0")/.." && pwd)"

case "$slot" in
  morning) message="poke-morning.txt" ;;
  hourly)  message="poke-hourly.txt" ;;
  *) echo "usage: $0 [morning|hourly]" >&2; exit 2 ;;
esac

# Same sender, same config, same roster as the hosted clock. Sharing the sender
# is the point: two clocks that could drift into sending different things would
# be two schedules, not one schedule with a backup.
exec "$root/scripts/send-poke.sh" "$root/desk/channel" "$message"
