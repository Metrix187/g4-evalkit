#!/usr/bin/env bash
# wipe and redo gold+empty verification for every mined set with the current resolver, so the
# held-out set is graded one consistent way
set -uo pipefail
cd /mnt/d/gemma4-agent
for d in data/mined/*/; do
  name=$(basename "$d")
  case "$name" in heldout*) continue ;; esac  # heldout100 is a subset, verifying it twice is a waste
  [ -s "$d/tasks.jsonl" ] || continue
  rm -rf "$d/verify" "$d"/verify.*.log
  echo "== $name $(date +%H:%M:%S)"
  bash tools/mine/verify.sh "$name" 2>&1 | grep -E '"resolved": [0-9]+,$' | head -2
done
echo "== reverify done $(date +%H:%M:%S)"
