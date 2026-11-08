"""Mechanism counts for a gate run: graph-tool calls, pytest before the first edit, pytest on a missing
path, `ls tests`, calls spent after a sandbox-side red (missing module, conftest import, bad path) vs an
own-breakage red, never-edited sessions, first-edit call, empty patches.

    python tools/mech_counts.py r012 fast4b fast4c

reads the ATIF traces and patches under results/runs/<run>/kaggle_out. written for r012 to check that
fast4c's step 5 did what it says; grades come from compare.py, not here.
"""
import json, re, glob, os, sys, statistics as st
from pathlib import Path
REPO = str(Path(__file__).resolve().parents[1]); run = sys.argv[1]; cands = sys.argv[2:]
NL = chr(10)
def obs_text(s):
    c = (s.get("observation") or {}).get("content") or ""
    try:
        j = json.loads(c)
        if isinstance(j, dict): c = NL.join(str(j.get(k) or "") for k in ("stdout", "stderr", "error_message", "message", "error"))
    except Exception: pass
    return c
def calls(trace):
    out = []
    for s in trace.get("steps", []):
        if s.get("source") != "agent": continue
        c = obs_text(s)
        for tc in (s.get("tool_calls") or []):
            out.append((tc.get("function_name") or "", tc.get("arguments") or {}, c))
    return out
ENV = ("ImportError while loading conftest", "error during collection", "no collectors", "ModuleNotFoundError", "not found:", "file or directory not found")
for cand in cands:
    n = 0; graph = 0; py_before_edit = 0; py_missing = 0; ls_tests = 0; env_red = 0; after_env = []; edit_after_env = 0
    own_red = 0; after_own = []; never_edit = 0; first_edit_calls = []; ncalls = []; empty = 0; submit_n = []
    for p in sorted(glob.glob(f"{REPO}/results/runs/{run}/kaggle_out/results/{cand}/traces/trace_*.json")):
        n += 1
        cs = calls(json.load(open(p, encoding="utf-8")))
        ncalls.append(len(cs))
        fe = next((i for i, (f, a, _) in enumerate(cs) if f in ("edit_file", "write_file")), None)
        if fe is None: never_edit += 1
        else: first_edit_calls.append(fe + 1)
        submit_n.append(sum(f == "submit_patch" for f, _, _ in cs))
        edited = {os.path.basename(a.get("filepath") or "") for f, a, _ in cs if f in ("edit_file", "write_file") and isinstance(a, dict)}
        graph += sum(f in ("search_similar_code", "get_code_neighbors", "get_code_subgraph") for f, _, _ in cs)
        seen_env = False; seen_own = False
        for i, (f, a, c) in enumerate(cs):
            cmd = str(a.get("command", "")) if isinstance(a, dict) else ""
            if f == "run_command" and re.search(r"^\s*ls\s+tests", cmd): ls_tests += 1
            if f == "run_command" and "pytest" in cmd:
                if fe is None or i < fe: py_before_edit += 1
                if "not found" in c and ("ERROR: not found" in c or "file or directory not found" in c): py_missing += 1
                is_env = any(k in c for k in ENV)
                is_red = bool(re.search(r"\d+ failed", c)) or "SyntaxError" in c
                if is_env and not seen_env:
                    seen_env = True; env_red += 1; after_env.append(len(cs) - i - 1)
                    edit_after_env += any(f2 == "edit_file" for f2, _, _ in cs[i+1:])
                elif is_red and not is_env and not seen_own:
                    seen_own = True; own_red += 1; after_own.append(len(cs) - i - 1)
        pf = f"{REPO}/results/runs/{run}/kaggle_out/results/{cand}/patches/{os.path.basename(p)[6:-5]}.patch"
        if not os.path.exists(pf) or not open(pf, encoding="utf-8", errors="replace").read().strip(): empty += 1
    print(f"{cand}: sessions {n} | graph-tool calls {graph} | pytest before 1st edit {py_before_edit} | pytest on missing path {py_missing} | `ls tests` {ls_tests}")
    print(f"   env-red sessions {env_red}, calls after env red mean {st.mean(after_env) if after_env else 0:.1f} (edit after: {edit_after_env}) | own-red sessions {own_red}, calls after mean {st.mean(after_own) if after_own else 0:.1f}")
    print(f"   never edited {never_edit} | median first edit call {st.median(first_edit_calls) if first_edit_calls else 0} | median calls {st.median(ncalls)} | empty/missing patches {empty} | submit calls mean {st.mean(submit_n):.2f}")
