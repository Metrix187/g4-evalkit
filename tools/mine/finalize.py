"""Stage 4 of task mining: keep verified tasks and assemble one held-out set.

    python tools/mine/finalize.py                    # every data/mined/*/ with a finished verify

verified = gold patch passes and empty patch fails on our grader (tools/mine/verify.sh). writes
data/mined/heldout/{tasks.jsonl, snapshots/} in the harness layout, snapshots hard-linked from each
repo's folder, plus a per-repo count table. the gate and gold_check take it via --data-dir.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
MINED = REPO / "data" / "mined"
OUT = MINED / "heldout"


def load(p: Path) -> dict[str, dict]:
    if not p.is_file():
        return {}
    return {r["instance_id"]: r for r in map(json.loads, p.read_text(encoding="utf-8").splitlines()) if r}


def main() -> None:
    (OUT / "snapshots").mkdir(parents=True, exist_ok=True)
    kept, table = [], []
    for d in sorted(p for p in MINED.iterdir() if p.is_dir() and not p.name.startswith("heldout")):  # heldout100 is a subset, not a repo
        gold, empty = load(d / "verify" / "gold_results.jsonl"), load(d / "verify" / "empty_results.jsonl")
        if not gold or not empty:
            continue
        tasks = load(d / "tasks.jsonl")
        ok = [t for t in tasks if gold.get(t, {}).get("resolved") and t in empty and not empty[t]["resolved"]]
        table.append((d.name, len(tasks), sum(1 for t in tasks if gold.get(t, {}).get("resolved")), len(ok)))
        for t in ok:
            kept.append(tasks[t])
            stem = f"{tasks[t]['repo'].split('/')[1]}_{tasks[t]['base_commit']}"
            for sub, name in (("snapshots", f"{t}.tgz"), ("graphs", f"{stem}.json"), ("embeddings", f"{stem}.npz")):
                src, dst = d / sub / name, OUT / sub / name
                if src.exists() and not dst.exists():
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    os.link(src, dst)
    with (OUT / "tasks.jsonl").open("w", encoding="utf-8") as f:
        for t in kept:
            f.write(json.dumps(t) + "\n")
    print(f"{'repo':<36}{'built':>7}{'gold ok':>9}{'verified':>10}")
    for name, n, g, v in table:
        print(f"{name:<36}{n:>7}{g:>9}{v:>10}")
    print(f"held-out set: {len(kept)} tasks -> {OUT}")


if __name__ == "__main__":
    main()
