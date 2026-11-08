"""Code graphs + node embeddings for mined tasks, in the comp's format.

    ~/g4emb/bin/python tools/mine/graph.py pallets__click        # inside WSL

per unique base commit: a static AST call graph (nodes = module functions, classes, methods with
ids like `click.core.Command.invoke`, src/ stripped, tests included; edges typed "calls" for calls,
instantiations and base->subclass links, same mix the comp graphs carry) plus one 256-d vector per
node. the comp's vectors aren't any public model we could match (tools/mine/embed_probe.py), so
these come from Salesforce/codet5p-110m-embedding. search_similar_code only compares vectors within
one repo, so a different space still behaves the same. embeddings are cached per repo by text
hash, since most code doesn't change between nearby commits.

writes data/mined/<set>/{graphs,embeddings}/<repo_short>_<base_commit>.{json,npz}
"""

from __future__ import annotations

import ast
import hashlib
import io
import json
import subprocess
import sys
import tarfile
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[2]
MIRRORS = Path.home() / "g4mine" / "mirrors"
SKIP_DIRS = {".git", ".venv", "venv", "build", "dist", "node_modules", "__pycache__", ".tox", ".nox"}


# ---------- graph ----------

def module_name(rel: str) -> str:
    parts = rel[:-3].split("/")
    if parts[0] == "src":
        parts = parts[1:]
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def class_stub(node: ast.ClassDef, src: str) -> str:
    head = ast.get_source_segment(src, node).split("\n", 1)[0]
    out = [head]
    doc = ast.get_docstring(node)
    if doc:
        out.append(f'    """{doc}"""')
    for b in node.body:
        if isinstance(b, (ast.FunctionDef, ast.AsyncFunctionDef)):
            sig = ast.get_source_segment(src, b).split("\n", 1)[0].rstrip(":")
            out.append(f"\n    {sig}:\n        ...")
    return "\n".join(out)


class Collector(ast.NodeVisitor):
    def __init__(self, mod: str, src: str):
        self.mod, self.src = mod, src
        self.nodes: dict[str, str] = {}
        self.funcs: list[tuple[str, ast.AST, str | None]] = []  # (id, node, enclosing class id)
        self.bases: list[tuple[str, ast.expr]] = []
        self.stack: list[str] = []

    def visit_ClassDef(self, n: ast.ClassDef):
        nid = ".".join([self.mod, *self.stack, n.name])
        self.nodes[nid] = class_stub(n, self.src)
        self.bases += [(nid, b) for b in n.bases]
        self.stack.append(n.name)
        for b in n.body:
            if isinstance(b, (ast.FunctionDef, ast.AsyncFunctionDef)):
                mid = f"{nid}.{b.name}"
                self.nodes[mid] = ast.get_source_segment(self.src, b) or ""
                self.funcs.append((mid, b, nid))
            elif isinstance(b, ast.ClassDef):
                self.visit_ClassDef(b)
        self.stack.pop()

    def visit_FunctionDef(self, n):
        if self.stack:
            return
        nid = f"{self.mod}.{n.name}"
        self.nodes[nid] = ast.get_source_segment(self.src, n) or ""
        self.funcs.append((nid, n, None))

    visit_AsyncFunctionDef = visit_FunctionDef


def imports_of(tree: ast.Module, mod: str) -> dict[str, str]:
    alias = {}
    pkg = mod.rsplit(".", 1)[0] if "." in mod else ""
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            for a in n.names:
                alias[a.asname or a.name.split(".")[0]] = a.name if a.asname else a.name.split(".")[0]
        elif isinstance(n, ast.ImportFrom):
            base = n.module or ""
            if n.level:
                up = pkg.split(".")[: max(0, len(pkg.split(".")) - (n.level - 1))] if pkg else []
                base = ".".join([*up, base] if base else up)
            for a in n.names:
                alias[a.asname or a.name] = f"{base}.{a.name}" if base else a.name
    return alias


def dotted(e: ast.expr) -> list[str] | None:
    parts = []
    while isinstance(e, ast.Attribute):
        parts.append(e.attr)
        e = e.value
    if isinstance(e, ast.Name):
        return [e.id, *reversed(parts)]
    return None


def build_graph(root: Path) -> tuple[dict[str, str], list[tuple[str, str]]]:
    nodes: dict[str, str] = {}
    per_mod = []
    for f in sorted(root.rglob("*.py")):
        rel = f.relative_to(root).as_posix()
        if set(rel.split("/")[:-1]) & SKIP_DIRS:
            continue
        try:
            src = f.read_text(encoding="utf-8")
            tree = ast.parse(src)
        except (SyntaxError, UnicodeDecodeError, ValueError):
            continue
        mod = module_name(rel)
        c = Collector(mod, src)
        for top in tree.body:
            c.visit(top)
        nodes.update(c.nodes)
        per_mod.append((mod, tree, c))

    edges: set[tuple[str, str]] = set()
    for mod, tree, c in per_mod:
        alias = imports_of(tree, mod)

        def resolve(parts: list[str], cls: str | None) -> str | None:
            head, rest = parts[0], parts[1:]
            if head in ("self", "cls") and cls and rest:
                cand = f"{cls}.{rest[0]}"
                return cand if cand in nodes else None
            for base in (f"{mod}.{head}", alias.get(head)):
                if base:
                    cand = ".".join([base, *rest])
                    while cand:
                        if cand in nodes:
                            return cand
                        cand = cand.rsplit(".", 1)[0] if "." in cand else ""
            return None

        for fid, fn, cls in c.funcs:
            for n in ast.walk(fn):
                if isinstance(n, ast.Call):
                    parts = dotted(n.func)
                    tgt = resolve(parts, cls) if parts else None
                    if tgt and tgt != fid:
                        edges.add((fid, tgt))
        for cid, b in c.bases:
            parts = dotted(b)
            tgt = resolve(parts, None) if parts else None
            if tgt:
                edges.add((tgt, cid))  # base -> subclass, like requests.exceptions.Timeout -> ReadTimeout
    return nodes, sorted(edges)


# ---------- embeddings ----------

class Embedder:
    def __init__(self, cache_path: Path):
        import torch
        from transformers import AutoModel, AutoTokenizer
        self.torch = torch
        ck = "Salesforce/codet5p-110m-embedding"
        self.tok = AutoTokenizer.from_pretrained(ck, trust_remote_code=True)
        self.model = AutoModel.from_pretrained(ck, trust_remote_code=True).eval()
        self.dev = "cuda" if torch.cuda.is_available() else "cpu"
        self.model.to(self.dev)
        self.cache_path = cache_path
        self.cache: dict[str, np.ndarray] = dict(np.load(cache_path)) if cache_path.exists() else {}

    def __call__(self, texts: list[str]) -> list[np.ndarray]:
        keys = [hashlib.sha1(t.encode()).hexdigest() for t in texts]
        todo = sorted({k: t for k, t in zip(keys, texts) if k not in self.cache}.items())
        for i in range(0, len(todo), 64):
            batch = todo[i:i + 64]
            enc = self.tok([t for _, t in batch], return_tensors="pt", padding=True,
                           truncation=True, max_length=512).to(self.dev)
            with self.torch.no_grad():
                vecs = self.model(**enc).float().cpu().numpy()
            for (k, _), v in zip(batch, vecs):
                self.cache[k] = v.astype(np.float32)
        return [self.cache[k] for k in keys]

    def save(self):
        np.savez(self.cache_path, **self.cache)


# ---------- driver ----------

def checkout(mirror: Path, commit: str, dest: Path) -> None:
    data = subprocess.run(["git", "archive", "--format=tar", commit], cwd=mirror,
                          capture_output=True, check=True).stdout
    with tarfile.open(fileobj=io.BytesIO(data)) as tf:
        tf.extractall(dest, filter="data")


def main() -> None:
    import tempfile
    set_name = sys.argv[1]
    d = REPO / "data" / "mined" / set_name
    tasks = [json.loads(ln) for ln in (d / "tasks.jsonl").read_text(encoding="utf-8").splitlines() if ln]
    mirror = MIRRORS / f"{set_name}.git"
    (d / "graphs").mkdir(exist_ok=True)
    (d / "embeddings").mkdir(exist_ok=True)
    emb = Embedder(d / "emb_cache.npz")
    done = 0
    for base in sorted({t["base_commit"] for t in tasks}):
        short = tasks[0]["repo"].split("/")[1]
        stem = f"{short}_{base}"
        if (d / "graphs" / f"{stem}.json").exists():
            continue
        with tempfile.TemporaryDirectory() as td:
            checkout(mirror, base, Path(td))
            nodes, edges = build_graph(Path(td))
        g = {"directed": True, "multigraph": True, "graph": {"repo_name": stem},
             "nodes": [{"name": k, "text": v, "id": k} for k, v in nodes.items()],
             "edges": [{"type": "calls", "source": s, "target": t, "key": 0} for s, t in edges]}
        (d / "graphs" / f"{stem}.json").write_text(json.dumps(g))
        ids = list(nodes)
        vecs = emb([nodes[i] for i in ids])
        np.savez_compressed(d / "embeddings" / f"{stem}.npz", **dict(zip(ids, vecs)))
        done += 1
        print(f"{stem[:40]}: {len(nodes)} nodes, {len(edges)} edges, cache {len(emb.cache)}", flush=True)
        if done % 10 == 0:
            emb.save()
    emb.save()
    print(f"{set_name}: {done} graphs built")


if __name__ == "__main__":
    main()
