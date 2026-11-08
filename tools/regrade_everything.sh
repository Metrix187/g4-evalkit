#!/usr/bin/env bash
# after any resolver change: re-verify every mined set, rebuild the held-out set, re-sweep the dev set.
# everything downstream (gate grading, task lists, the paper's numbers) should come from one resolver
set -uo pipefail
cd /mnt/d/gemma4-agent
bash tools/mine/reverify_all.sh
source "$HOME/g4env/bin/activate"
rm -rf data/mined/heldout && python tools/mine/finalize.py
bash tools/run_gold_all.sh results/gold_full3
echo "== everything regraded $(date -u +%H:%M)"
