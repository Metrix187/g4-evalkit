"""Cut a stratified subset of the held-out set into its own harness-layout folder.

    python tools/mine/subset.py --per-repo 10 --seed 1 --out data/mined/heldout100

up to N tasks per repo, so a gate run spreads across every repo instead of drowning in click and
werkzeug. hard links, so it costs no disk. writes a README with provenance and licenses, since
this folder is what gets uploaded as a private kaggle dataset.
"""

from __future__ import annotations

import argparse
import collections
import json
import os
import random
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SRC = REPO / "data" / "mined" / "heldout"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--per-repo", type=int, default=10)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    out = Path(args.out)
    tasks = [json.loads(ln) for ln in (SRC / "tasks.jsonl").read_text(encoding="utf-8").splitlines() if ln]
    by_repo = collections.defaultdict(list)
    for t in tasks:
        by_repo[t["repo"]].append(t)
    rng = random.Random(args.seed)
    picked = []
    for repo in sorted(by_repo):
        ts = sorted(by_repo[repo], key=lambda t: t["instance_id"])
        picked += sorted(rng.sample(ts, min(args.per_repo, len(ts))), key=lambda t: t["instance_id"])

    for sub in ("snapshots", "graphs", "embeddings"):
        (out / sub).mkdir(parents=True, exist_ok=True)
    for t in picked:
        stem = f"{t['repo'].split('/')[1]}_{t['base_commit']}"
        for sub, name in (("snapshots", f"{t['instance_id']}.tgz"), ("graphs", f"{stem}.json"),
                          ("embeddings", f"{stem}.npz")):
            src, dst = SRC / sub / name, out / sub / name
            if src.exists() and not dst.exists():
                os.link(src, dst)
    with (out / "tasks.jsonl").open("w", encoding="utf-8") as f:
        for t in picked:
            f.write(json.dumps(t) + "\n")

    counts = collections.Counter(t["repo"] for t in picked)
    lic = {t["repo"]: t.get("license") or "see repo" for t in picked}
    lines = [
        "# g4 held-out tasks (private, for local agent evaluation)",
        "",
        "Bug-fix tasks mined from merged pull requests that close an issue, in the Gemma 4 Developer",
        "Agent harness layout (tasks.jsonl, snapshots/, graphs/, embeddings/). Each one is verified on",
        "our grader: the upstream fix passes the PR's tests, and an empty patch fails them. Snapshots",
        "keep git history only up to the base commit. Graphs are static AST call graphs; embeddings",
        "come from Salesforce/codet5p-110m-embedding.",
        "",
        "| repo | tasks | license |",
        "|---|---|---|",
        *[f"| {r} | {n} | {lic[r]} |" for r, n in sorted(counts.items())],
        "",
        "Every task links its source PR and issues in `tasks.jsonl` (`pr`, `issues`). Code and text",
        "remain under their upstream licenses.",
    ]
    (out / "README.md").write_text("\n".join(lines) + "\n")
    print(f"{len(picked)} tasks from {len(counts)} repos -> {out}")
    print(dict(sorted(counts.items())))


if __name__ == "__main__":
    main()
