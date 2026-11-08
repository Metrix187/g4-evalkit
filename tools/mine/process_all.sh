#!/usr/bin/env bash
# verify + graph every built set that hasn't been done yet. safe to rerun; it skips finished work.
#   bash tools/mine/process_all.sh verify     # cpu: gold/empty on our grader
#   bash tools/mine/process_all.sh graphs     # gpu: code graphs + codet5p embeddings
set -uo pipefail
cd /mnt/d/gemma4-agent
what=$1
for d in data/mined/*/; do
  name=$(basename "$d")
  [ "$name" = heldout ] && continue
  [ -f "$d/tasks.jsonl" ] || continue
  case $what in
    verify)
      [ -f "$d/verify/empty_summary.json" ] && continue
      echo "== verify $name $(date +%H:%M:%S)"
      bash tools/mine/verify.sh "$name" 2>&1 | grep -E '"resolved": [0-9]+,$' | head -2
      ;;
    graphs)
      n_tasks=$(python3 -c "import json;print(len({json.loads(l)['base_commit'] for l in open('$d/tasks.jsonl')}))")
      n_graphs=$(ls "$d/graphs" 2>/dev/null | wc -l)
      [ "$n_graphs" -ge "$n_tasks" ] && continue
      echo "== graphs $name ($n_graphs/$n_tasks) $(date +%H:%M:%S)"
      ~/g4emb/bin/python tools/mine/graph.py "$name" 2>&1 | grep -vE 'arn' | tail -1
      ;;
  esac
done
echo "== $what pass done $(date +%H:%M:%S)"
