#!/usr/bin/env bash
# full gold + empty sweep over every dev task. ~20-30 min on the laptop at concurrency 8
set -uo pipefail
cd /mnt/d/gemma4-agent
source "$HOME/g4env/bin/activate"
export PYTHONPATH=/mnt/d/gemma4-agent/tools
OUT=${1:-results/gold_full}
for mode in gold empty; do
  python tools/gold_check.py --mode "$mode" --isolated --concurrency 8 --out "$OUT" > "$OUT.$mode.log" 2>&1
  tail -25 "$OUT.$mode.log"
done
