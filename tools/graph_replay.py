"""Do the graph tools surface the *other* places a fix touches? A CPU replay over the dev tasks.

    tools/wsl.sh python tools/graph_replay.py [--out results/graph_replay.json]

for every dev task with gold-edited nodes in the graph (notes/graph_coverage.json, from
tools/graph_coverage.py), query each gold node the way an agent would (`Class.method` or `func`) through
the harness's own search_similar_code (k=10) and get_code_neighbors, on whatever swegemma ~/g4env has.
reports: does the short name resolve, do results include another gold node or another node from a
gold-edited module, how much of the result list is tests, and how spread out the similarity scores are.
baseline for sibling discovery: is the other gold node simply in the same module, where listing the file's
defs (fast4e's sibling check) would show it. per-task rows go to results/ (gitignored, dev-task names).
"""
from __future__ import annotations

import argparse
import json
import statistics as st
from pathlib import Path
from types import SimpleNamespace

from swegemma.tools.graph import get_code_neighbors, search_similar_code

REPO = Path(__file__).resolve().parents[1]
D = REPO / "data" / "raw" / "comp"


class Ctx(SimpleNamespace):
    def check_budget(self, count_tool_call=True):  # the tool wrapper asks; nothing to enforce here
        return None


def short(q: str) -> str:
    p = q.split(".")
    return f"{p[-2]}.{p[-1]}" if len(p) >= 2 and p[-2][:1].isupper() else p[-1]


def module(q: str) -> str:
    # drop trailing Class/func parts: everything up to the first capitalised or last component is close enough
    p = q.split(".")
    for i, part in enumerate(p):
        if part[:1].isupper():
            return ".".join(p[:i])
    return ".".join(p[:-1])


def bare(name: str) -> str:
    return name.split(";", 1)[1] if ";" in name else name


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(REPO / "results" / "graph_replay.json"))
    ap.add_argument("--per-task", type=int, default=4, help="max gold nodes queried per task")
    a = ap.parse_args()
    cov = json.load(open(REPO / "notes" / "graph_coverage.json"))["tasks"]
    tasks = {json.loads(l)["instance_id"]: json.loads(l) for l in open(D / "tasks.jsonl", encoding="utf-8")}
    rows, q_rows = [], []
    for r in cov:
        gold = r["in_graph"]
        if not gold:
            continue
        t = tasks[r["instance_id"]]
        ctx = Ctx(task={"repo": t["repo"], "base_commit": t["base_commit"]}, repo=None,
                  graph_dir=D / "graphs", embeddings_dir=D / "embeddings", max_stdout_chars=5000)
        gold_mods = {module(g) for g in gold}
        found_other = same_module_other = False
        for q in gold[: a.per_task]:
            sq = short(q)
            s = json.loads(search_similar_code(ctx, sq, k=10))
            n = json.loads(get_code_neighbors(ctx, sq))
            sim = [(x["node_name"], x["similarity"]) for x in s.get("results", [])]
            nbr = [bare(x) for x in n.get("neighbors", [])]
            names = [x for x, _ in sim]
            others = set(gold) - {q}
            hit_sim = bool(others & set(names)); hit_nbr = bool(others & set(nbr))
            mod_sim = any(module(x) in gold_mods and x != q for x in names)
            sims = [v for _, v in sim]
            q_rows.append({
                "task": r["instance_id"], "gold": q, "query": sq,
                "resolved": bool(sim) or bool(nbr),
                "self_in_similar": q in names,
                "other_gold_in_similar": hit_sim, "other_gold_in_neighbors": hit_nbr,
                "gold_module_in_similar": mod_sim,
                "test_share_similar": sum(x.startswith("tests.") or ".tests." in x for x in names) / len(names) if names else None,
                "test_share_neighbors": sum(x.startswith("tests.") or ".tests." in x for x in nbr) / len(nbr) if nbr else None,
                "sim_spread": (max(sims) - min(sims)) if len(sims) >= 2 else None,
                "n_neighbors": len(nbr),
            })
            found_other |= hit_sim or hit_nbr
            same_module_other |= any(module(o) == module(q) for o in others)
        rows.append({"task": r["instance_id"], "n_gold": len(gold), "graph_found_other": found_other,
                     "other_gold_same_module": same_module_other})
    Path(a.out).write_text(json.dumps({"tasks": rows, "queries": q_rows}, indent=1))
    pct = lambda xs: f"{100 * sum(xs) / max(1, len(xs)):.0f}%"
    print(f"queries {len(q_rows)} over {len(rows)} tasks")
    print("short name resolves:", pct([x["resolved"] for x in q_rows]), "| queried node itself in top-10 similar:", pct([x["self_in_similar"] for x in q_rows]))
    multi = [r for r in rows if r["n_gold"] >= 2]
    mq = [x for x in q_rows if any(r["task"] == x["task"] and r["n_gold"] >= 2 for r in rows)]
    print(f"tasks with 2+ gold nodes: {len(multi)}")
    print("  another gold node in similar top-10 (per query):", pct([x["other_gold_in_similar"] for x in mq]))
    print("  another gold node among neighbors (per query):", pct([x["other_gold_in_neighbors"] for x in mq]))
    print("  any query surfaces another gold node (per task):", pct([r["graph_found_other"] for r in multi]))
    print("  another gold node sits in the same module, so a def listing shows it (per task):", pct([r["other_gold_same_module"] for r in multi]))
    ts = [x["test_share_similar"] for x in q_rows if x["test_share_similar"] is not None]
    tn = [x["test_share_neighbors"] for x in q_rows if x["test_share_neighbors"] is not None]
    sp = [x["sim_spread"] for x in q_rows if x["sim_spread"] is not None]
    print(f"share of tests: similar {st.mean(ts):.0%}, neighbors {st.mean(tn):.0%}")
    print(f"similarity spread across top-10: median {st.median(sp):.4f}, p90 {sorted(sp)[int(.9 * len(sp))]:.4f}")


if __name__ == "__main__":
    main()
