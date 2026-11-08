"""Per-string comparison of malformed edit_file/write_file calls ("mandatory input parameters are not present") vs calls that ran, r010-r012. new_string/content only, swallowed tail cut off."""
import json, glob, collections, re, statistics as st
from pathlib import Path
REPO = str(Path(__file__).resolve().parents[1])
BS = chr(92); NL = chr(10); BT = chr(96); DQ = chr(34)
TAIL = re.compile(BT + r",\s*(old_string|new_string|filepath|content)\s*:.*$", re.S)
rows = {"bad": [], "ok": []}; tail_hits = 0; bt_where = collections.Counter()
for p in glob.glob(f"{REPO}/results/runs/r01[012]/kaggle_out/results/*/traces/trace_*.json"):
    for s in json.load(open(p, encoding="utf-8")).get("steps", []):
        if s.get("source") != "agent": continue
        c = (s.get("observation") or {}).get("content") or ""
        for tc in s.get("tool_calls") or []:
            f = tc.get("function_name")
            if f not in ("edit_file", "write_file"): continue
            a = tc.get("arguments") or {}
            key = "new_string" if f == "edit_file" else "content"
            if "mandatory input parameters" in c: k = "bad"
            elif '"status": "ok"' in c: k = "ok"
            else: continue
            v = a.get(key)
            if not isinstance(v, str): continue
            if k == "bad":
                m = TAIL.search(v)
                if m:
                    tail_hits += 1; v = v[:m.start()]
                # where did the stray backtick land: which arg holds a TAIL
                for kk, vv in a.items():
                    if isinstance(vv, str) and TAIL.search(vv): bt_where[kk] += 1
            rows[k].append(v)
def pct(xs, fn): return 100 * sum(1 for x in xs if fn(x)) / max(1, len(xs))
print("strings: malformed", len(rows["bad"]), "| ran", len(rows["ok"]), "| malformed with backtick+arg tail:", tail_hits)
print("which argument held the swallowed tail:", dict(bt_where))
feats = [("has a real line break", lambda x: NL in x),
         ("has backslash-n", lambda x: (BS + "n") in x),
         ("backslash-n but no real line break", lambda x: (BS + "n") in x and NL not in x),
         ("has backslash-quote", lambda x: (BS + DQ) in x),
         ("has a backtick", lambda x: BT in x),
         ("single line (no real or escaped break)", lambda x: NL not in x and (BS + "n") not in x)]
print(f"{'new_string/content':40} {'malformed':>9} {'ran':>6}")
for name, fn in feats:
    print(f"{name:40} {pct(rows['bad'], fn):8.0f}% {pct(rows['ok'], fn):5.0f}%")
for k in ("bad", "ok"):
    L = [len(x) for x in rows[k]]
    print(k, "median length", st.median(L), "| >400 chars", f"{pct(rows[k], lambda x: len(x) > 400):.0f}%")
