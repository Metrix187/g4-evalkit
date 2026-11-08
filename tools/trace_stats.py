"""Where did each agent's time go? Per-task trace stats for a gate run, joined with our grades.

    tools/wsl.sh python tools/trace_stats.py --run r001

reads results/runs/<run>/kaggle_out/results/<cand>/traces/*.json (ATIF, loaded with the
harness's own SessionTrace) and graded/<cand>/patches_results.jsonl. prints one block per
candidate and writes results/runs/<run>/trace_stats.jsonl for deeper digging.
"""

from __future__ import annotations

import argparse
import json
import statistics as st
from pathlib import Path

from adk_eval_core.tracing import SessionTrace

REPO = Path(__file__).resolve().parents[1]
RUNS = REPO / "results" / "runs"


def task_stats(path: Path) -> dict:
    tr = SessionTrace.from_dict(json.loads(path.read_text(encoding="utf-8")))
    ents = tr._entries
    s = tr.summarize()
    tools = s["tool_call_breakdown"]
    last_tool = next((e.tool_name for e in reversed(ents) if e.event_type == "tool_call"), "")
    think_chars = sum(len(e.content or "") for e in ents if e.event_type == "thinking")
    return {
        "seconds": round(max((e.elapsed for e in ents), default=0.0), 1),
        "llm_events": sum(1 for e in ents if e.usage),
        "tool_calls": s["tool_calls"],
        "tools": tools,
        "submitted": tools.get("submit_patch", 0) > 0,
        "last_tool": last_tool,
        "completion_tokens": s["total_completion_tokens"],
        "prompt_tokens": s["total_prompt_tokens"],
        "think_chars": think_chars,
        "compactions": s["compaction_entries"],
        "errors": sum(1 for e in ents if e.event_type == "error"),
    }


def med(xs):
    return round(st.median(xs), 1) if xs else 0


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True)
    args = ap.parse_args()
    rd = RUNS / args.run
    meta = json.loads((rd / "run.json").read_text())
    rows = []
    for cand in meta["candidates"]:
        graded = {}
        gp = rd / "graded" / cand / "patches_results.jsonl"
        if gp.is_file():
            graded = {r["instance_id"]: r for r in map(json.loads, gp.read_text().splitlines()) if r}
        for tp in sorted((rd / "kaggle_out" / "results" / cand / "traces").glob("trace_*.json")):
            iid = tp.stem.removeprefix("trace_")
            try:
                s = task_stats(tp)
            except Exception as e:  # a corrupt trace shouldn't hide the rest
                s = {"broken_trace": str(e)[:100]}
            s.update(cand=cand, instance_id=iid, resolved=graded.get(iid, {}).get("resolved"))
            rows.append(s)
    with (rd / "trace_stats.jsonl").open("w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")

    for cand in meta["candidates"]:
        rs = [r for r in rows if r["cand"] == cand and "seconds" in r]
        if not rs:
            continue
        sub = [r for r in rs if r["submitted"]]
        res = [r for r in rs if r["resolved"]]
        tool_tot: dict[str, int] = {}
        for r in rs:
            for k, v in r["tools"].items():
                tool_tot[k] = tool_tot.get(k, 0) + v
        print(f"\n== {cand}: {len(rs)} traces, resolved {len(res)}, submitted {len(sub)}")
        print(f"   median seconds {med([r['seconds'] for r in rs])}, tool calls {med([r['tool_calls'] for r in rs])}, "
              f"llm turns {med([r['llm_events'] for r in rs])}, completion tok {med([r['completion_tokens'] for r in rs])}, "
              f"thinking chars {med([r['think_chars'] for r in rs])}")
        print(f"   resolved | submitted: {sum(1 for r in sub if r['resolved'])}/{len(sub)}   "
              f"resolved | not submitted: {sum(1 for r in rs if r['resolved'] and not r['submitted'])}/{len(rs) - len(sub)}")
        print(f"   tool mix: {dict(sorted(tool_tot.items(), key=lambda kv: -kv[1]))}")


if __name__ == "__main__":
    main()
