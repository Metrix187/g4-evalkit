#!/usr/bin/env bash
# separate env for codet5p-110m-embedding: its remote code predates transformers 5.x
set -euo pipefail
export PATH=$HOME/.local/bin:$PATH
E=$HOME/g4emb
[ -d "$E" ] || uv venv -q --managed-python --python 3.12 "$E"
VIRTUAL_ENV=$E uv pip install -q --index-url https://download.pytorch.org/whl/cu128 torch
VIRTUAL_ENV=$E uv pip install -q "transformers<4.46" numpy
"$E/bin/python" -c "import transformers, torch; print('ok', transformers.__version__, torch.__version__)"
