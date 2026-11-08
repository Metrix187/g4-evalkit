"""Write a gate task list from the gold sweep: gradeable tasks only, stratified by repo.

    python tools/make_task_list.py --out notes/tasks_all.txt
    python tools/make_task_list.py --per-repo fastapi=24 rich=10 requests=6 httpx=1 --seed 1 --out notes/tasks_s40.txt

gradeable = gold patch passes and an empty patch fails on our grader. anything else either can't
be solved here or is free points, and neither tells two agents apart.
"""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
GOLD = REPO / "results" / "gold_full"


def load(p: Path) -> dict[str, dict]:
    return {r["instance_id"]: r for r in map(json.loads, p.read_text(encoding="utf-8").splitlines()) if r}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--per-repo", nargs="*", default=[], help="repo_short=count, omit for all")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    gold, empty = load(GOLD / "gold_results.jsonl"), load(GOLD / "empty_results.jsonl")
    ok = sorted(t for t, r in gold.items() if r["resolved"] and t in empty and not empty[t]["resolved"])
    by_repo: dict[str, list[str]] = {}
    for t in ok:
        by_repo.setdefault(gold[t]["repo"].split("/")[1], []).append(t)

    want = dict(kv.split("=") for kv in args.per_repo)
    rng = random.Random(args.seed)
    picked = []
    for repo, ts in sorted(by_repo.items()):
        n = int(want[repo]) if repo in want else (len(ts) if not want else 0)
        picked += sorted(rng.sample(ts, min(n, len(ts))))

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    header = (f"# {len(picked)} gradeable dev tasks (gold passes, empty fails on our grader). "
              f"seed={args.seed} per_repo={want or 'all'}\n")
    Path(args.out).write_text(header + "\n".join(picked) + "\n")
    print(header.strip(), "|", {r: len(v) for r, v in by_repo.items()})


if __name__ == "__main__":
    main()
