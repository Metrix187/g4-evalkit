"""Put candidates' LoRA weights in a private Kaggle dataset so the gate can mount them.

    python tools/upload_adapters.py submissions/fast2_lora --slug goth55/g4-adapters

stages <adapter name>/{adapter_model.safetensors, adapter_config.json} for every adapter in the
given candidates (hard links, no copies), then creates the dataset or pushes a new version. the
gate finds weights by adapter name and sha256 (kaggle_gate.py --adapters-data), so older adapters
can stay in the dataset and adapter names just have to be unique.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("candidates", nargs="+")
    ap.add_argument("--slug", default="goth55/g4-adapters")
    ap.add_argument("--message", default="adapters")
    args = ap.parse_args()

    stage = REPO / "results" / "adapters_upload" / args.slug.split("/")[1]
    stage.mkdir(parents=True, exist_ok=True)
    for c in map(Path, args.candidates):
        for a in sorted((c / "adapters").glob("*/adapter_model.safetensors")):
            dst = stage / a.parent.name
            dst.mkdir(exist_ok=True)
            for f in (a, a.parent / "adapter_config.json"):
                if (dst / f.name).exists():
                    (dst / f.name).unlink()
                os.link(f, dst / f.name)
            print(f"staged {a.parent.name} from {c.name}: {a.stat().st_size / 1e6:.0f} MB")
    meta = stage / "dataset-metadata.json"
    first = not meta.exists()
    meta.write_text(json.dumps({"title": args.slug.split("/")[1], "id": args.slug,
                                "licenses": [{"name": "apache-2.0"}]}, indent=2))
    kaggle = str(Path(sys.executable).with_name("kaggle"))
    # the cli trips over relative paths with slashes, so run it from inside the folder
    cmd = [kaggle, "datasets", "create", "-p", ".", "--dir-mode", "zip"] if first else \
          [kaggle, "datasets", "version", "-p", ".", "--dir-mode", "zip", "-m", args.message]
    subprocess.run(cmd, cwd=stage, check=True)


if __name__ == "__main__":
    main()
