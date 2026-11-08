"""What do a candidate's misses look like next to the reference fix? Function-level, CPU only.

    python tools/miss_shape.py --run r012 --cand fast4c [--data data/mined/heldout]

for each unresolved task with a non-empty patch: which files and which functions (innermost def/class
around each changed line, as in graph_coverage.py) the agent changed vs the gold patch. buckets: wrong
file, some gold files, all gold files but missed a gold function (a sibling miss, what fast4e's def
listing is for), all gold functions touched but still wrong. test files are ignored on both sides.
"""
from __future__ import annotations

import argparse, ast, json, re, tarfile
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+\d+(?:,\d+)? @@")


def changed(patch: str) -> dict[str, set[int]]:
    out, cur, old = {}, None, 0
    for ln in patch.splitlines():
        if ln.startswith("--- "):
            p = ln[4:].strip(); cur = None if p == "/dev/null" else p.removeprefix("a/")
        elif ln.startswith("+++ "):
            if cur is None:  # new file: record it by its new path
                p = ln[4:].strip().removeprefix("b/"); cur = p; out.setdefault(cur, set())
        elif (m := HUNK.match(ln)):
            old = int(m.group(1))
        elif cur is None:
            continue
        elif ln.startswith("-"):
            out.setdefault(cur, set()).add(old); old += 1
        elif ln.startswith("+"):
            out.setdefault(cur, set()).add(max(old - 1, 1))
        elif ln.startswith(" "):
            old += 1
    return out


def is_test(f: str) -> bool:
    parts = f.lower().split("/")
    return any(p in ("tests", "test", "testing") for p in parts[:-1]) or parts[-1].startswith("test_") or parts[-1] in ("conftest.py",)


def funcs(src: str, lines: set[int]) -> set[str]:
    sp = []
    def walk(node, prefix):
        for n in getattr(node, "body", []):
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                q = f"{prefix}.{n.name}" if prefix else n.name
                sp.append((q, n.lineno, n.end_lineno or n.lineno))
                if isinstance(n, ast.ClassDef):
                    walk(n, q)
    try:
        walk(ast.parse(src), "")
    except SyntaxError:
        return set()
    out = set()
    for l in lines:
        best = None
        for q, a, b in sp:
            if a <= l <= b and (best is None or b - a < best[2] - best[1]):
                best = (q, a, b)
        out.add(best[0] if best else "<module>")
    return out


def shape(run: str, cand: str, d: Path, tasks: dict) -> tuple[dict[str, str], dict[str, list[str]]]:
    """one bucket per graded task, 'solved' included, so two candidates can be cross-tabbed"""
    rd = REPO / "results" / "runs" / run
    res = {json.loads(l)["instance_id"]: json.loads(l)["resolved"] for l in open(rd / "graded" / cand / "patches_results.jsonl", encoding="utf-8")}
    # the grader writes no row for an empty patch, so start from the run's own task list
    meta = json.loads((rd / "run.json").read_text(encoding="utf-8"))
    out, missed = {}, {}
    for tid in sorted(meta.get("task_ids") or res):
        if tid not in tasks:
            continue
        ok = res.get(tid, False)
        if ok:
            out[tid] = "solved"; continue
        pp = rd / "patches_clean" / cand / f"{tid}.patch"
        ap_ = pp.read_text(encoding="utf-8", errors="replace") if pp.is_file() else ""
        if not ap_.strip():
            out[tid] = "no patch"; continue
        g = {f: ls for f, ls in changed(tasks[tid]["patch"]).items() if f.endswith(".py") and not is_test(f)}
        m = {f: ls for f, ls in changed(ap_).items() if f.endswith(".py") and not is_test(f)}
        if not g:
            out[tid] = "gold edits no .py source"; continue
        common = set(g) & set(m)
        if not common:
            out[tid] = "wrong file"
        elif set(g) - set(m):
            out[tid] = "some gold files"
        else:
            snap = d / "snapshots" / f"{tid}.tgz"
            gf, mf = set(), set()
            with tarfile.open(snap) as tf:
                names = set(tf.getnames())
                for f in g:
                    mem = f"./{f}" if f"./{f}" in names else f
                    src = tf.extractfile(mem).read().decode("utf-8", "replace") if mem in names else ""
                    gf |= {f"{f}:{q}" for q in funcs(src, g[f])}
                    mf |= {f"{f}:{q}" for q in funcs(src, m.get(f, set()))}
            missing = gf - mf
            out[tid] = "all gold files, missed a gold function" if missing else "all gold functions touched, still wrong"
            if missing:
                missed[tid] = sorted(missing)[:3]
    return out, missed


ORDER = ["solved", "no patch", "wrong file", "some gold files", "all gold files, missed a gold function",
         "all gold functions touched, still wrong", "gold edits no .py source"]
SHORT = {"solved": "solved", "no patch": "empty", "wrong file": "wrong-file", "some gold files": "some-files",
         "all gold files, missed a gold function": "missed-func", "all gold functions touched, still wrong": "all-touched",
         "gold edits no .py source": "no-py-gold"}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True); ap.add_argument("--cand", required=True)
    ap.add_argument("--vs", help="second candidate (cand, or run/cand) for a task-by-task transition matrix")
    ap.add_argument("--data", default=str(REPO / "data" / "mined" / "heldout"))
    a = ap.parse_args()
    d = Path(a.data)
    tasks = {json.loads(l)["instance_id"]: json.loads(l) for l in open(d / "tasks.jsonl", encoding="utf-8")}
    sa, missed = shape(a.run, a.cand, d, tasks)
    buckets = Counter(v for v in sa.values() if v != "solved")
    print(f"{a.run} {a.cand}: {sum(buckets.values())} unresolved of {len(sa)}")
    for k, v in buckets.most_common():
        print(f"  {k:42} {v}")
    print("  sibling misses (task, missed gold functions):")
    for tid, miss in missed.items():
        print(f"    {tid:24} {miss}")
    if not a.vs:
        return
    run_b, cand_b = a.vs.split("/") if "/" in a.vs else (a.run, a.vs)
    sb, _ = shape(run_b, cand_b, d, tasks)
    common = sorted(set(sa) & set(sb))
    # rows = where the task was under A, columns = where it went under B. the diagonal is "stayed put",
    # so a bucket that shrinks for B is only good news if its tasks moved to the solved column
    used = [b for b in ORDER if any(sa[t] == b for t in common) or any(sb[t] == b for t in common)]
    w = max(len(SHORT[b]) for b in used) + 1
    print(f"\ntransition {a.cand} (rows) -> {cand_b} (cols), {len(common)} shared tasks")
    print(" " * (w + 2) + "".join(f"{SHORT[b]:>{w}}" for b in used) + f"{'total':>{w}}")
    for ra in used:
        row = [t for t in common if sa[t] == ra]
        cells = [sum(1 for t in row if sb[t] == cb) for cb in used]
        print(f"  {SHORT[ra]:<{w}}" + "".join(f"{c:>{w}}" for c in cells) + f"{len(row):>{w}}")
    inc = [t for t in common if sa[t] in ("some gold files", "all gold files, missed a gold function")]
    went = Counter(SHORT[sb[t]] for t in inc)
    print(f"  {a.cand}'s {len(inc)} incomplete fixes under {cand_b}: " + ", ".join(f"{k} {v}" for k, v in went.most_common()))
    for t in inc:
        if sb[t] == "solved":
            print(f"    rescued: {t}")
    lost = [t for t in common if sa[t] == "solved" and sb[t] != "solved"]
    print(f"  {a.cand}'s solves lost under {cand_b}: {len(lost)}: " + ", ".join(f"{t} -> {SHORT[sb[t]]}" for t in lost))


if __name__ == "__main__":
    main()
