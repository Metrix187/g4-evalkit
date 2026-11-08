#!/usr/bin/env bash
# reinstall the harness wheels from data/raw/wheelhouse into ~/g4env without touching deps.
# the host republishes wheels under the same version number (v31 on oct 12 changed swegemma 0.2.11
# in place), so pip/uv think nothing changed unless you force it. run through tools/wsl.sh
set -euo pipefail
cd /mnt/d/gemma4-agent
for w in swegemma-0.2.11 adk_submission-0.2.13 adk_eval_core-0.1.0; do
  "$HOME/.local/bin/uv" pip install --no-deps --reinstall "data/raw/wheelhouse/$w-py3-none-any.whl"
done
python -c "import swegemma.budget as b; print('verification_timeout_seconds' in b.HarnessLimits.__dataclass_fields__)"
