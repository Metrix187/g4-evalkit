"""Turn successful gate-run sessions into SFT conversations.

    python tools/sft/traces_to_sft.py --runs r001 r002 r003 --out data/sft/dev_success.jsonl

keeps only sessions our grader resolved. each ATIF trace already carries the system instruction,
the harness's task prompt, and per agent step the thought (`message`), the tool calls and the
tool result, so the conversation is rebuilt as-is: no reconstruction, no guessing what the model
saw. output is one OpenAI-style example per line: {"messages": [...], "tools": [...], "meta": {...}},
assistant turns carrying `reasoning_content` + `tool_calls`. the training notebook applies the
gemma 4 thinking chat template.

this is the model's own output on dev tasks (self-distillation), so no third-party model
outputs end up in training data (AGENTS.md rule 5).
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
RUNS = REPO / "results" / "runs"


def convert(trace: dict) -> list[dict] | None:
    msgs: list[dict] = []
    for s in trace["steps"]:
        ev = (s.get("extra") or {}).get("event_type")
        if s["source"] == "system":
            msgs.append({"role": "system", "content": s.get("message") or ""})
        elif s["source"] == "user":
            msgs.append({"role": "user", "content": s.get("message") or ""})
        elif s["source"] == "agent":
            calls = s.get("tool_calls") or []
            turn = {"role": "assistant", "content": ""}
            text = s.get("message") or ""
            if ev == "thinking":
                turn["reasoning_content"] = text
            else:
                turn["content"] = text
            if calls:
                # arguments stay a dict: gemma 4's template only renders its native
                # `call:name{key:value}` syntax from a mapping. a JSON string gets pasted in raw,
                # and the model would train on a call format it never actually emits
                turn["tool_calls"] = [{
                    "id": c.get("tool_call_id") or f"call_{i}", "type": "function",
                    "function": {"name": c["function_name"], "arguments": c.get("arguments") or {}},
                } for i, c in enumerate(calls)]
            msgs.append(turn)
            obs = (s.get("observation") or {}).get("content", "")
            for i, c in enumerate(calls):
                msgs.append({"role": "tool", "tool_call_id": turn["tool_calls"][i]["id"],
                             "name": c["function_name"], "content": obs if i == 0 else ""})
    if len(msgs) < 3 or msgs[0]["role"] != "system" or msgs[1]["role"] != "user":
        return None
    return msgs


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", nargs="+", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--max-per-task", type=int, default=0,
                    help="keep at most N sessions per task, shortest first (0 = keep all). easy tasks get "
                         "solved by every candidate in every run and would otherwise drown out the rest")
    args = ap.parse_args()
    tools = json.loads((REPO / "notes" / "tool_schemas.json").read_text())
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    found: dict[str, list[tuple[int, dict]]] = {}
    for run in args.runs:
        meta = json.loads((RUNS / run / "run.json").read_text())
        for cand in meta["candidates"]:
            gp = RUNS / run / "graded" / cand / "patches_results.jsonl"
            if not gp.is_file():
                continue
            ok = {r["instance_id"] for r in map(json.loads, gp.read_text(encoding="utf-8").splitlines())
                  if r and r["resolved"] is True}
            for iid in sorted(ok):
                tp = RUNS / run / "kaggle_out" / "results" / cand / "traces" / f"trace_{iid}.json"
                if not tp.is_file():
                    continue
                msgs = convert(json.loads(tp.read_text(encoding="utf-8")))
                if not msgs:
                    continue
                ex = {"messages": msgs, "tools": tools, "meta": {"run": run, "candidate": cand, "instance_id": iid}}
                found.setdefault(iid, []).append((len(json.dumps(msgs)), ex))
    n = 0
    with out.open("w", encoding="utf-8") as f:
        for iid in sorted(found):
            exs = [ex for _, ex in sorted(found[iid], key=lambda t: t[0])]
            for ex in exs[:args.max_per_task] if args.max_per_task else exs:
                f.write(json.dumps(ex) + "\n")
                n += 1
    per_task = found
    print(f"{n} sessions from {len(per_task)} distinct tasks -> {out}")


if __name__ == "__main__":
    main()
