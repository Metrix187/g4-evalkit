"""Rebuild a Colab notebook from its `# %%` percent-format .py twin, which is the one we edit.

    python tools/py2nb.py colab/train_lora.py      # writes colab/train_lora.ipynb, keeps its metadata
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

META = {"accelerator": "GPU", "colab": {"gpuType": "A100", "provenance": []},
        "kernelspec": {"display_name": "Python 3", "name": "python3"}}


def cells(src: str) -> list[dict]:
    out = []
    for chunk in re.split(r"^# %%", src, flags=re.M)[1:]:
        head, _, body = chunk.partition("\n")
        body = body.strip("\n")
        if "[markdown]" in head:
            text = "\n".join(re.sub(r"^# ?", "", ln) for ln in body.splitlines())
            out.append({"cell_type": "markdown", "metadata": {}, "source": text.splitlines(keepends=True)})
        else:
            out.append({"cell_type": "code", "metadata": {}, "execution_count": None, "outputs": [],
                        "source": body.splitlines(keepends=True)})
    return out


def main() -> None:
    py = Path(sys.argv[1])
    nb_path = py.with_suffix(".ipynb")
    meta = json.loads(nb_path.read_text(encoding="utf-8")).get("metadata", META) if nb_path.exists() else META
    nb = {"cells": cells(py.read_text(encoding="utf-8")), "metadata": meta, "nbformat": 4, "nbformat_minor": 0}
    nb_path.write_text(json.dumps(nb, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"{nb_path}: {len(nb['cells'])} cells")


if __name__ == "__main__":
    main()
