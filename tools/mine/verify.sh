#!/usr/bin/env bash
# stage 3 of task mining: gold must pass, empty must fail, on our grader.
#   bash tools/mine/verify.sh pallets__click
# then tools/mine/finalize.py keeps the verified ones.
set -uo pipefail
cd /mnt/d/gemma4-agent
source "$HOME/g4env/bin/activate"
export PYTHONPATH=/mnt/d/gemma4-agent/tools
SET=$1
D=data/mined/$SET
for mode in gold empty; do
  python tools/gold_check.py --mode "$mode" --isolated --concurrency 8 --data-dir "$D" --out "$D/verify" > "$D/verify.$mode.log" 2>&1
  tail -3 "$D/verify.$mode.log"
done
