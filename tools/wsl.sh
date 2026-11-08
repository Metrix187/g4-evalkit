#!/usr/bin/env bash
# run anything from the repo root inside WSL with ~/g4env active.
# from git bash on windows:  MSYS_NO_PATHCONV=1 wsl -d Ubuntu -- bash /mnt/d/gemma4-agent/tools/wsl.sh python tools/gold_check.py ...
set -euo pipefail
cd /mnt/d/gemma4-agent
source "$HOME/g4env/bin/activate"
export PYTHONPATH=/mnt/d/gemma4-agent/tools${PYTHONPATH:+:$PYTHONPATH}
exec "$@"
