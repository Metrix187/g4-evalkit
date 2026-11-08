"""How often does a session see a red pytest run, and what does it do after?

    python tools/red_runs.py out.json --runs r010 r011 --cands fast3 fast4a fast4b

reads the ATIF traces under results/runs/<run>/kaggle_out/results/<cand>/traces and the local grades,
prints one summary row per candidate and every red-but-unresolved session with what the red was
(own breakage vs a conftest ImportError, a collection error, a guessed path). written for r010/r011
after the jinja_1614 "pre-existing red" theory didn't survive reading the trace.
"""
import argparse, json, re, sys, glob, os
from pathlib import Path
REPO = str(Path(__file__).resolve().parents[1])
ap = argparse.ArgumentParser()
ap.add_argument("out")
ap.add_argument("--runs", nargs="+", default=["r010", "r011"])
ap.add_argument("--cands", nargs="+", default=["fast3", "fast4a", "fast4b"])
A = ap.parse_args()
NL = chr(10)
def resolved_map(run, cand):
    m = {}
    p = f"{REPO}/results/runs/{run}/graded/{cand}/patches_results.jsonl"
    if not os.path.exists(p): return m
    for l in open(p, encoding="utf-8"):
        r = json.loads(l); m[r["instance_id"]] = bool(r.get("resolved"))
    return m
def obs_text(s):
    ob = s.get("observation") or {}
    c = ob.get("content")
    if c is None:
        rs = ob.get("results") or []
        c = rs[0].get("content") if rs else ""
    if not isinstance(c, str): c = json.dumps(c)
    try:
        j = json.loads(c)
        if isinstance(j, dict):
            c = NL.join(str(j.get(k) or "") for k in ("stdout", "stderr", "error_message", "message", "error"))
    except Exception:
        pass
    return c
def calls(trace):
    out = []
    for s in trace.get("steps", []):
        if s.get("source") != "agent": continue
        c = obs_text(s)
        for tc in (s.get("tool_calls") or []):
            out.append((tc.get("function_name") or tc.get("name"), tc.get("arguments") or {}, c))
    return out
rows = []
for run in A.runs:
    for cand in A.cands:
        res = resolved_map(run, cand)
        for p in sorted(glob.glob(f"{REPO}/results/runs/{run}/kaggle_out/results/{cand}/traces/trace_*.json")):
            tid = os.path.basename(p)[6:-5]
            cs = calls(json.load(open(p, encoding="utf-8")))
            edited = {os.path.basename(a.get("filepath") or "") for f, a, _ in cs if f in ("edit_file", "write_file") and isinstance(a, dict)}
            first_edit = next((i for i, (f, a, _) in enumerate(cs) if f in ("edit_file", "write_file")), None)
            first_red = None; red_txt = ""; n_py = 0; red_before_edit = False
            for i, (f, a, c) in enumerate(cs):
                if f == "run_command" and "pytest" in str(a.get("command", "")):
                    n_py += 1
                    red = re.search(r"\d+ failed", c) or re.search(r"\d+ errors?\b", c) or "SyntaxError" in c or "ImportError" in c or "ERROR" in c
                    if red and first_red is None:
                        first_red = i
                        tail = re.findall(r"(\d+ failed[^\n]*|\d+ errors?[^\n]*|SyntaxError[^\n]*|ImportError[^\n]*|ERROR[^\n]{0,60})", c)
                        red_txt = (tail[-1] if tail else "")[:70]
                        red_before_edit = first_edit is None or i < first_edit
            after = len(cs) - first_red - 1 if first_red is not None else 0
            edit_after = first_red is not None and any(f == "edit_file" for f, _, _ in cs[first_red+1:])
            own = first_red is not None and any(e and e in cs[first_red][2] for e in edited)
            rows.append(dict(run=run, cand=cand, tid=tid, ok=res.get(tid), ncalls=len(cs), n_py=n_py, red=first_red is not None,
                             red_at=first_red, after=after, edit_after=edit_after, own=own, red_before_edit=red_before_edit, red_txt=red_txt))
print(f"{'cand':7} {'n':>3} {'ran_py':>6} {'saw_red':>7} {'red&ok':>6} {'red&miss':>8} {'after(mean)':>11} {'edit_after':>10} {'tb_names_edited_file':>20} {'red_before_any_edit':>19}")
for cand in A.cands:
    rs = [r for r in rows if r["cand"] == cand]; red = [r for r in rs if r["red"]]
    print(f"{cand:7} {len(rs):3d} {sum(r['n_py']>0 for r in rs):6d} {len(red):7d} {sum(bool(r['ok']) for r in red):6d} {sum(not r['ok'] for r in red):8d} "
          f"{(sum(r['after'] for r in red)/len(red) if red else 0):11.1f} {sum(r['edit_after'] for r in red):10d} {sum(r['own'] for r in red):20d} {sum(r['red_before_edit'] for r in red):19d}")
print(NL + "--- red + unresolved: tid, red call/total, calls after, edited after?, traceback names an edited file?, summary ---")
for cand in A.cands:
    for r in rows:
        if r["cand"] == cand and r["red"] and not r["ok"]:
            print(f"{cand:7} {r['run']} {r['tid']:26} red@{r['red_at']+1:2d}/{r['ncalls']:2d} after={r['after']:2d} edit={int(r['edit_after'])} own={int(r['own'])} | {r['red_txt']}")
json.dump(rows, open(A.out, "w"), indent=0)
