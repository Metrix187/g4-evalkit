"""How much of what a gold patch touches is even in the provided code graph?

    tools/wsl.sh python tools/graph_coverage.py [--data-dir data/raw/comp] [--out notes/graph_coverage.json]

for each task: parse the gold patch, map every changed line in the *old* file to the innermost
enclosing def/class (ids built the way the comp graphs name nodes: dotted module path with src/
stripped, then qualname), then look those ids up in the task's graph. reports node coverage,
how connected the edited nodes are, and how much of the patch is module-level code no node can hold.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import statistics as st
import sys
import tarfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "mine"))
from graph import module_name  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+\d+(?:,\d+)? @@")


def changed_old_lines(patch: str) -> dict[str, set[int]]:
    """file -> old-file line numbers that were removed, or that an insertion sits right after."""
    out: dict[str, set[int]] = {}
    cur, old = None, 0
    for ln in patch.splitlines():
        if ln.startswith("--- "):
            p = ln[4:].strip()
            cur = None if p == "/dev/null" else p.removeprefix("a/")
        elif ln.startswith("+++ "):
            continue
        elif m := HUNK.match(ln):
            old = int(m.group(1))
        elif cur is None:
            continue
        elif ln.startswith("-"):
            out.setdefault(cur, set()).add(old)
            old += 1
        elif ln.startswith("+"):
            out.setdefault(cur, set()).add(max(old - 1, 1))
        elif ln.startswith(" "):
            old += 1
    return out


def spans(src: str, mod: str) -> list[tuple[str, int, int]]:
    out = []

    def walk(node, prefix):
        for n in getattr(node, "body", []):
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                qid = f"{prefix}.{n.name}"
                out.append((qid, n.lineno, n.end_lineno or n.lineno))
                if isinstance(n, ast.ClassDef):
                    walk(n, qid)
    walk(ast.parse(src), mod)
    return out


def innermost(sp, line):
    best = None
    for qid, a, b in sp:
        if a <= line <= b and (best is None or b - a < best[2] - best[1]):
            best = (qid, a, b)
    return best[0] if best else None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default=str(REPO / "data" / "raw" / "comp"))
    ap.add_argument("--out", default=str(REPO / "notes" / "graph_coverage.json"))
    args = ap.parse_args()
    d = Path(args.data_dir)
    tasks = [json.loads(ln) for ln in (d / "tasks.jsonl").read_text(encoding="utf-8").splitlines() if ln]

    rows = []
    for t in tasks:
        stem = f"{t['repo'].split('/')[1]}_{t['base_commit']}"
        gp = d / "graphs" / f"{stem}.json"
        if not gp.is_file():
            continue
        g = json.loads(gp.read_text(encoding="utf-8"))
        ids = {n["id"] for n in g["nodes"]}
        deg: dict[str, int] = {}
        for e in g["edges"]:
            deg[e["source"]] = deg.get(e["source"], 0) + 1
            deg[e["target"]] = deg.get(e["target"], 0) + 1
        lines = {f: ls for f, ls in changed_old_lines(t["patch"]).items() if f.endswith(".py")}
        edited, module_level = set(), 0
        with tarfile.open(d / "snapshots" / f"{t['instance_id']}.tgz") as tf:
            names = set(tf.getnames())
            for f, ls in lines.items():
                member = f"./{f}" if f"./{f}" in names else f
                if member not in names:
                    continue  # brand-new file: nothing existed to be a node
                try:
                    src = tf.extractfile(member).read().decode("utf-8", errors="replace")
                    sp = spans(src, module_name(f))
                except (SyntaxError, AttributeError, ValueError):
                    continue
                for line in ls:
                    q = innermost(sp, line)
                    if q:
                        edited.add(q)
                    else:
                        module_level += 1
        found = [q for q in edited if q in ids]
        rows.append({
            "instance_id": t["instance_id"], "repo": t["repo"],
            "edited": sorted(edited), "in_graph": sorted(found),
            "coverage": len(found) / len(edited) if edited else None,
            "edited_with_edges": sum(1 for q in found if deg.get(q, 0) > 0),
            "module_level_lines": module_level,
            "graph_nodes": len(ids), "graph_edges": len(g["edges"]),
        })

    cov = [r["coverage"] for r in rows if r["coverage"] is not None]
    any_hit = sum(1 for r in rows if r["in_graph"])
    connected = sum(1 for r in rows if r["edited_with_edges"])
    summary = {
        "tasks": len(rows),
        "mean_node_coverage": round(st.mean(cov), 3) if cov else None,
        "tasks_with_any_edited_node_in_graph": any_hit,
        "tasks_where_an_edited_node_has_edges": connected,
        "tasks_with_module_level_edits": sum(1 for r in rows if r["module_level_lines"]),
        "median_graph_nodes": st.median(r["graph_nodes"] for r in rows) if rows else 0,
        "median_edges_per_node": round(st.median(r["graph_edges"] / max(r["graph_nodes"], 1) for r in rows), 3) if rows else 0,
    }
    Path(args.out).write_text(json.dumps({"summary": summary, "tasks": rows}, indent=1))
    print(json.dumps(summary, indent=2))
    missed = [(r["instance_id"], q) for r in rows for q in r["edited"] if q not in r["in_graph"]][:12]
    print("examples of edited code missing from the graph:")
    for iid, q in missed:
        print(f"  {iid}: {q}")


if __name__ == "__main__":
    main()
