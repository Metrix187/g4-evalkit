"""Where do sessions burn calls resending a failed tool call? Per candidate, per tool, per error.

    python tools/repeat_fails.py --runs r010 r011 r012 --cands fast3 fast4a fast4b fast4c

a "streak" is consecutive failed calls to the same tool with the same error kind. reports how many
calls went into streaks of 3+, which tasks they hit, and whether the session still resolved.
written for fast4d: fast4c's edit_file rule says don't resend, but write_file and run_command
loops weren't covered.
"""
import argparse, collections, glob, json, os, re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ap = argparse.ArgumentParser()
ap.add_argument("--runs", nargs="+", default=["r010", "r011", "r012"])
ap.add_argument("--cands", nargs="+", default=["fast4b", "fast4c"])
A = ap.parse_args()


def kind(content: str) -> str | None:
    try:
        j = json.loads(content)
    except Exception:
        j = None
    if isinstance(j, dict):
        if str(j.get("status", "")).lower() in ("ok", "success"):
            return None
        msg = " ".join(str(j.get(k) or "") for k in ("error_type", "error_message", "error", "message"))
    else:
        msg = content
    if "mandatory input parameters" in msg:
        m = re.search(r"not present:\W*(\w+)", msg)
        return "missing-arg:" + (m.group(1) if m else "?")
    for pat, name in (("BudgetExceeded", "budget"), ("FileEditError", "edit-no-match"), ("Path traversal", "path-traversal"),
                      ("not found", "not-found"), ("TimeoutExceeded", "timeout"), ("Error", "other-error")):
        if pat in msg:
            return name
    return None


for run in A.runs:
    for cand in A.cands:
        tdir = REPO / "results" / "runs" / run / "kaggle_out" / "results" / cand / "traces"
        if not tdir.is_dir():
            continue
        res = {}
        rp = REPO / "results" / "runs" / run / "graded" / cand / "patches_results.jsonl"
        if rp.is_file():
            res = {json.loads(l)["instance_id"]: json.loads(l)["resolved"] for l in open(rp, encoding="utf-8")}
        by_kind = collections.Counter(); fails = 0; calls = 0; streak_calls = 0; streak_tasks = []
        for p in sorted(tdir.glob("trace_*.json")):
            tid = p.stem[6:]
            seq = []
            for s in json.load(open(p, encoding="utf-8")).get("steps", []):
                if s.get("source") != "agent":
                    continue
                c = (s.get("observation") or {}).get("content") or ""
                for tc in s.get("tool_calls") or []:
                    k = kind(c if isinstance(c, str) else json.dumps(c))
                    seq.append((tc.get("function_name"), k))
            calls += len(seq)
            run_len = 0; worst = 0; prev = None; wasted = 0
            for f, k in seq:
                if k:
                    fails += 1; by_kind[f"{f}/{k}"] += 1
                cur = (f, k) if k else None
                run_len = run_len + 1 if cur and cur == prev else (1 if cur else 0)
                if run_len >= 3:
                    wasted += 1
                worst = max(worst, run_len); prev = cur
            if worst >= 3:
                streak_calls += wasted
                streak_tasks.append((tid, worst, res.get(tid)))
        print(f"== {run} {cand}: {calls} calls, {fails} failed, {streak_calls} spent in 3rd+ repeats of the same failure")
        print("   top failure kinds:", dict(by_kind.most_common(6)))
        print("   streak tasks (task, longest streak, resolved):", streak_tasks)
