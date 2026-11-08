"""How did each candidate's sessions end, and would its clocks fit the scorer's 12 h?

    python tools/run_budget.py --run r010

besides the mean, prints the share of sessions that ran into a cap and a repo-cluster bootstrap of the
116-task total (hidden tasks come from a handful of private repos, so slow repos travel together):
resample repos with replacement, pull tasks until 116, sum agent time / time_scale, take p50 and p99.

reads results/runs/<run>/kaggle_out/results/<cand>/task_results.jsonl, the traces, and the candidate's
own eval_config.yaml. the agent part comes from the traces (first to last step) / time_scale x 116.
setup and verification don't scale with time_scale: they're CPU-bound and 12 tasks fight over the box
in the gate. their medians land right on timeout_seconds there because the harness's
`setup.py --fast-path` runs with no timeout of its own and inherits it (r010: 122 s at 120,
204 s at 240), so they say more about the gate than the scorer, which runs one task at a time.
"""

from __future__ import annotations

import argparse
import collections
import json
import random
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]
RUNS = REPO / "results" / "runs"
HIDDEN_TASKS = 116


def ending(r: dict, max_calls: int | None) -> str:
    err = r.get("error") or ""
    if "session timeout" in err:
        return "time cap"
    if "turns budget" in err:
        return "turn cap"
    if "tool call budget" in err:
        return "tool cap"
    if "ContextWindowExceeded" in err:
        return "ctx overflow"
    if err:
        return "other error"
    # running out of calls doesn't raise, the session just winds down, so count it by the number
    if max_calls and (r.get("tool_calls") or 0) >= max_calls:
        return "tool cap"
    return "stopped"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True)
    args = ap.parse_args()
    rd = RUNS / args.run
    meta = json.loads((rd / "run.json").read_text())
    scale = float(meta.get("time_scale", 1.0))
    for cand, info in meta["candidates"].items():
        tr = rd / "kaggle_out" / "results" / cand / "task_results.jsonl"
        if not tr.is_file():
            print(f"{cand}: no task_results.jsonl yet")
            continue
        rows = [json.loads(ln) for ln in tr.read_text().splitlines() if ln.strip()]
        cfg_path = REPO / info["path"] / "eval_config.yaml"
        ev = {}
        if cfg_path.is_file():
            raw = yaml.safe_load(cfg_path.read_text()) or {}
            ev = raw.get("evaluation", raw)
        max_calls = ev.get("max_tool_calls")
        ends = collections.Counter(ending(r, max_calls) for r in rows)
        agent, pre, post = [], [], []
        by_repo: dict[str, list[float]] = collections.defaultdict(list)
        for r in rows:
            tp = rd / "kaggle_out" / "results" / cand / "traces" / f"trace_{r['instance_id']}.json"
            if not tp.is_file():
                continue
            steps = json.loads(tp.read_text(encoding="utf-8")).get("steps", [])
            el = [s["extra"]["elapsed_s"] for s in steps if (s.get("extra") or {}).get("elapsed_s") is not None]
            if el:
                agent.append(max(el) - min(el))
                pre.append(min(el))
                post.append(max(0.0, r.get("duration_seconds", 0) - max(el)))
                by_repo[r["instance_id"].rsplit("_", 1)[0]].append(max(el) - min(el))
        mean_task = sum(r.get("duration_seconds", 0) for r in rows) / len(rows) / 60
        agent_h = (sum(agent) / len(agent) / scale * HIDDEN_TASKS / 3600) if agent else 0.0
        med = lambda xs: sorted(xs)[len(xs) // 2] if xs else 0
        print(f"== {cand} ({len(rows)} tasks; {ev.get('max_time_minutes')} min, {max_calls} calls, "
              f"{ev.get('timeout_seconds')} s)")
        print("   ends: " + ", ".join(f"{k} {v}" for k, v in ends.most_common()))
        print(f"   agent ~{agent_h:.1f} h for {HIDDEN_TASKS} hidden tasks at scorer speed "
              f"(mean {sum(agent) / max(len(agent), 1) / 60:.1f} gate min/session). gate setup median {med(pre):.0f} s, "
              f"verify median {med(post):.0f} s, mean task {mean_task:.1f} min")
        capped = sum(v for k, v in ends.items() if k.endswith("cap"))
        if agent:
            srt = sorted(agent)
            p95 = srt[min(len(srt) - 1, int(0.95 * len(srt)))] / scale / 60
            print(f"   cap hits {capped}/{len(rows)} ({100 * capped / len(rows):.0f}%), session p95 {p95:.1f} scorer min")
        if len(by_repo) >= 2:
            rng = random.Random(0)
            repos = sorted(by_repo)
            sums = []
            for _ in range(4000):
                tot, n = 0.0, 0
                while n < HIDDEN_TASKS:
                    for x in by_repo[rng.choice(repos)]:
                        if n >= HIDDEN_TASKS:
                            break
                        tot += x / scale; n += 1
                sums.append(tot / 3600)
            sums.sort()
            q = lambda f: sums[min(len(sums) - 1, int(f * len(sums)))]
            print(f"   repo-cluster bootstrap of the {HIDDEN_TASKS}-task agent total: p50 {q(0.5):.1f} h, p90 {q(0.9):.1f} h, "
                  f"p99 {q(0.99):.1f} h (+ ~2 h setup/verify/startup; hard wall 12 h)")


if __name__ == "__main__":
    main()
