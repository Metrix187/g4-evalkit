#!/usr/bin/env bash
# builds two envs inside WSL:
#   ~/g4env  the harness (swegemma + friends from the comp wheelhouse, cpu-only torch). no model
#            serving here, the 4060 can't hold the 31b. this env runs sandboxes + grading.
#   ~/g4sbx  what a sandbox sees: docker/Dockerfile.sandbox's pip installs plus the task data's
#            wheels, picked the same way the harness picks them. see tools/sandbox.py
# both on python 3.13 because the official sandbox image is python:3.13-slim and some task
# wheels are cp313-only. sandbox venvs inherit the harness interpreter, so g4env has to match.
set -euo pipefail

REPO=/mnt/d/gemma4-agent
WH=$REPO/data/raw/wheelhouse
ENV=$HOME/g4env
SBX=$HOME/g4sbx
PY=3.13

python3 -m pip install --user --break-system-packages -q uv
export PATH=$HOME/.local/bin:$PATH
uv --version

# ubuntu's python ships without ensurepip, and subprocess sandboxes build a venv per task.
# no ensurepip = no pip in the sandbox = editable installs quietly fail. uv's standalone build has it.
uv python install -q $PY

[ -d "$ENV" ] || uv venv -q --managed-python --python $PY "$ENV"
source "$ENV/bin/activate"
python -c "import ensurepip, venv, sys; print('harness python', sys.version.split()[0], 'ensurepip ok')"

# cpu torch first so the harness deps don't drag in gigabytes of cuda
uv pip install -q --index-url https://download.pytorch.org/whl/cpu torch torchvision
uv pip install -q --find-links "$WH" swegemma adk-submission adk-eval-core "google-adk==1.36.1" litellm pytest

[ -d "$SBX" ] || uv venv -q --managed-python --python $PY "$SBX"
VIRTUAL_ENV=$SBX uv pip install -q pytest "pytest-timeout==2.1.0" typer pdm-backend setuptools wheel poetry-core hatchling flit-core editables
SP=$(echo "$SBX"/lib/python$PY/site-packages)
cp "$REPO/data/raw/comp/docker/imp.py" "$REPO/data/raw/comp/docker/telnetlib.py" "$SP"/
python "$REPO/tools/build_sbx.py" | head -3
# test-only deps (pytest-httpbin, inline-snapshot, ...) are NOT installed here on purpose. they go in
# per task via tools/sbx_resolve.py from each commit's own requirement files. installing them globally
# broke rich: inline-snapshot imports attrs, and rich's examples/attrs.py (on sys.path thanks to the
# fast path's workspace_paths.pth) calls SystemExit

python - <<'PY'
import swegemma, adk_submission, adk_eval_core, google.adk, torch
print("ok", swegemma.__file__, "torch", torch.__version__)
PY
uv pip list 2>/dev/null | grep -iE '^(swegemma|google-adk|litellm|torch|transformers|docker) '
