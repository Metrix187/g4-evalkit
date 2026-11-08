#!/usr/bin/env bash
# build every listed repo that doesn't have tasks.jsonl yet. sequential, the mirrors are big
#   bash tools/mine/build_all.sh
set -uo pipefail
cd /mnt/d/gemma4-agent
source "$HOME/g4env/bin/activate"
for d in data/mined/*/; do
  name=$(basename "$d")
  [ "$name" = heldout ] && continue
  [ -f "$d/prs.jsonl" ] || continue
  [ -f "$d/tasks.jsonl" ] && continue
  slug=${name/__//}
  echo "== $slug $(date +%H:%M:%S)"
  python tools/mine/build.py "$slug" 2>&1 | tail -4
done
echo "== all built $(date +%H:%M:%S)"
