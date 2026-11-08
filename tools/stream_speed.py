"""How fast did each agent's stream actually run during a gate run?

    tools/wsl.sh python tools/stream_speed.py --run r002

for every model call in every trace: wall time from the previous event (tool result or session
start) to the response, and the completion tokens it produced. the gate's --time-scale only gives
the scorer's token budget if per-stream speed under concurrency is known, and the probe measured
short prompts. this measures the real thing, long contexts and all.
"""

from __future__ import annotations

import argparse
import json
import statistics as st
from pathlib import Path

from adk_eval_core.tracing import SessionTrace

RUNS = Path(__file__).resolve().parents[1] / "results" / "runs"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True)
    args = ap.parse_args()
    meta = json.loads((RUNS / args.run / "run.json").read_text())
    for cand in meta["candidates"]:
        lat, toks, prompt, rate = [], [], [], []
        tool_time = []
        for tp in sorted((RUNS / args.run / "kaggle_out" / "results" / cand / "traces").glob("trace_*.json")):
            tr = SessionTrace.from_dict(json.loads(tp.read_text(encoding="utf-8")))
            prev = 0.0
            last_call = None
            for e in tr._entries:
                if e.event_type == "tool_call":
                    last_call = e.elapsed
                if e.event_type == "tool_response" and last_call is not None:
                    tool_time.append(e.elapsed - last_call)
                if e.usage:
                    dt = e.elapsed - prev
                    c = int(e.usage.get("completion_tokens", 0) or 0)
                    if dt > 0 and c > 0:
                        lat.append(dt)
                        toks.append(c)
                        prompt.append(int(e.usage.get("prompt_tokens", 0) or 0))
                        rate.append(c / dt)
                if e.event_type in ("tool_response", "text", "final") or e.usage:
                    prev = e.elapsed
        if not lat:
            continue
        print(f"== {cand}: {len(lat)} model calls")
        print(f"   tokens/s per call: median {st.median(rate):.1f}, overall {sum(toks) / sum(lat):.1f}")
        print(f"   call latency s: median {st.median(lat):.1f}, p90 {sorted(lat)[int(len(lat) * .9)]:.1f}")
        print(f"   completion tokens/call: median {st.median(toks):.0f}; prompt tokens/call: median {st.median(prompt):.0f}")
        if tool_time:
            print(f"   tool exec s: median {st.median(tool_time):.1f}, total share {sum(tool_time) / (sum(tool_time) + sum(lat)):.0%}")


if __name__ == "__main__":
    main()
