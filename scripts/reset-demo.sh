#!/usr/bin/env bash
# Put the demo desk back to its seeded state: drop every artifact a run
# produced, keep every input a run reads.
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
rm -f "$root"/desk/state/*.claim.json
rm -rf "$root"/desk/knowledge
rm -f "$root"/desk/work-log.csv
find "$root/desk/data/drafts" -name '*.json' ! -name 't-northwind-flour.json' -delete
echo "demo desk reset"
