"""Paired comparison of candidates from one gate run.

    python tools/compare.py --run r001                  # every pair of candidates in the run
    python tools/compare.py --run r001 --a base --b v1

only tasks our gold check calls gradeable count (gold passes, empty fails). on those, what tells
two agents apart is the discordant tasks: A solved and B didn't, or the reverse. the exact
two-sided sign test on that split is the number to look at, not the raw totals.
"""

from __future__ import annotations

import argparse
import itertools
import json
from math import comb
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
RUNS = REPO / "results" / "runs"
GOLD = REPO / "results" / "gold_full"


def load(path: Path) -> dict[str, dict]:
    if not path.is_file():
        return {}
    return {r["instance_id"]: r for r in map(json.loads, path.read_text(encoding="utf-8").splitlines()) if r}


def gradeable() -> set[str] | None:
    gold, empty = load(GOLD / "gold_results.jsonl"), load(GOLD / "empty_results.jsonl")
    if not gold:
        return None
    return {t for t, r in gold.items() if r["resolved"] and not empty.get(t, {}).get("resolved", False)}


def sign_test_p(a_only: int, b_only: int) -> float:
    n = a_only + b_only
    if n == 0:
        return 1.0
    k = min(a_only, b_only)
    tail = sum(comb(n, i) for i in range(k + 1)) / 2 ** n
    return min(1.0, 2 * tail)


def solved(rows: dict[str, dict], t: str) -> bool:
    # a session that ended without a patch never gets a .patch file, so it never gets graded.
    # that's a fail, not a missing data point
    return bool(rows.get(t, {}).get("resolved"))


def where(run: str, cand: str) -> tuple[str, str]:
    # "r004b/fast2_t1" compares across runs: same tasks, candidates pushed as separate kernels
    return tuple(cand.split("/", 1)) if "/" in cand else (run, cand)


def compare(run: str, a: str, b: str, ok: set[str] | None) -> None:
    (run_a, a), (run_b, b) = where(run, a), where(run, b)
    ra = load(RUNS / run_a / "graded" / a / "patches_results.jsonl")
    rb = load(RUNS / run_b / "graded" / b / "patches_results.jsonl")
    tasks = sorted(json.loads((RUNS / run_a / "run.json").read_text())["task_ids"])
    other = set(json.loads((RUNS / run_b / "run.json").read_text())["task_ids"])
    if other != set(tasks):
        print(f"  note: {run_a} and {run_b} ran different task lists, comparing on the overlap")
        tasks = [t for t in tasks if t in other]
    if ok is not None:
        dropped = [t for t in tasks if t not in ok]
        tasks = [t for t in tasks if t in ok]
    else:
        dropped = []
    both = [t for t in tasks if solved(ra, t) and solved(rb, t)]
    a_only = [t for t in tasks if solved(ra, t) and not solved(rb, t)]
    b_only = [t for t in tasks if solved(rb, t) and not solved(ra, t)]
    p = sign_test_p(len(a_only), len(b_only))
    sa, sb = len(both) + len(a_only), len(both) + len(b_only)
    print(f"\n{a} vs {b}  ({len(tasks)} gradeable tasks, {len(dropped)} ungradeable dropped)")
    print(f"  {a}: {sa}/{len(tasks)}   {b}: {sb}/{len(tasks)}   both: {len(both)}")
    print(f"  discordant: {a} only {len(a_only)}, {b} only {len(b_only)}   sign test p = {p:.3f}")
    if a_only:
        print(f"  {a} only: {' '.join(a_only)}")
    if b_only:
        print(f"  {b} only: {' '.join(b_only)}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run", required=True)
    ap.add_argument("--a", help="candidate, or run/candidate for one from another run")
    ap.add_argument("--b", help="candidate, or run/candidate for one from another run")
    args = ap.parse_args()
    ok = gradeable()
    if json.loads((RUNS / args.run / "run.json").read_text()).get("data"):
        ok = None  # mined sets only hold verified tasks, and the dev gold map doesn't apply to them
    elif ok is None:
        print("no gold sweep found, comparing on every task")
    graded = sorted(p.name for p in (RUNS / args.run / "graded").iterdir() if p.is_dir())
    pairs = [(args.a, args.b)] if args.a and args.b else list(itertools.combinations(graded, 2))
    for a, b in pairs:
        compare(args.run, a, b, ok)


if __name__ == "__main__":
    main()
