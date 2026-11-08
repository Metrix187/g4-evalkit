"""Stage 2 of task mining: turn listed PRs into harness-format tasks + snapshots.

    tools/wsl.sh python tools/mine/build.py pallets/click            # inside WSL, always

per PR: base = first parent of the merge commit, the diff base..merge split into a code patch and
a test patch, problem statement from the linked issue(s), hints from issue comments written
before the PR opened. snapshot = the repo with history cut at base (fast-export/import, same as
the comp's snapshots, so no future commits leak), tarred like data/raw/comp/snapshots/*.tgz.

has to run in WSL: a windows checkout would bake CRLF and lost exec bits into the snapshots.
output: data/mined/<owner>__<name>/{tasks.jsonl, snapshots/}
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from prs import is_test  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
MIRRORS = Path.home() / "g4mine" / "mirrors"


def git(*args: str, cwd: Path | None = None, inp: bytes | None = None) -> str:
    res = subprocess.run(["git", *args], cwd=cwd, input=inp, capture_output=True)
    if res.returncode != 0:
        raise RuntimeError(f"git {' '.join(args[:3])}: {res.stderr.decode(errors='replace')[:300]}")
    return res.stdout.decode("utf-8", errors="replace")


def mirror(slug: str) -> Path:
    m = MIRRORS / f"{slug.replace('/', '__')}.git"
    if m.exists():
        git("fetch", "-q", "origin", "+refs/heads/*:refs/heads/*", cwd=m)
    else:
        m.parent.mkdir(parents=True, exist_ok=True)
        git("clone", "-q", "--bare", f"https://github.com/{slug}.git", str(m))
    return m


def _fast_export_import(m: Path, wt: Path) -> None:
    git("init", "-q", "-b", "main", cwd=wt)
    exported = subprocess.run(
        ["git", "fast-export", "--signed-tags=strip", "--tag-of-filtered-object=drop", "refs/heads/g4base"],
        cwd=m, capture_output=True, check=True).stdout
    git("fast-import", "--quiet", cwd=wt, inp=exported)
    git("branch", "-M", "g4base", "main", cwd=wt)
    git("checkout", "-q", "-f", "main", cwd=wt)


def _clone_and_prune(m: Path, base: str, wt: Path) -> None:
    # fallback for histories fast-import chokes on (werkzeug has a "Name<email>" ident from 2008).
    # same guarantee: only commits reachable from base survive
    git("clone", "-q", "--no-checkout", str(m), str(wt))
    git("checkout", "-q", "-B", "main", base, cwd=wt)
    for ref in git("for-each-ref", "--format=%(refname)", cwd=wt).split():
        if ref != "refs/heads/main":
            git("update-ref", "-d", ref, cwd=wt)
    git("remote", "remove", "origin", cwd=wt)
    git("reflog", "expire", "--expire=now", "--all", cwd=wt)
    git("gc", "-q", "--prune=now", cwd=wt)


def snapshot(m: Path, base: str, dest: Path) -> None:
    git("update-ref", "refs/heads/g4base", base, cwd=m)
    with tempfile.TemporaryDirectory() as td:
        wt = Path(td) / "repo"
        wt.mkdir()
        try:
            _fast_export_import(m, wt)
        except (RuntimeError, subprocess.CalledProcessError):
            shutil.rmtree(wt)
            _clone_and_prune(m, base, wt)
        dest.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["tar", "-czf", str(dest), "-C", str(wt), "."], check=True)


def build_one(pr: dict, m: Path, short: str, snaps: Path) -> dict:
    merge = pr["mergeCommit"]["oid"]
    base = git("rev-parse", f"{merge}^1", cwd=m).strip()
    changed = [p for p in git("diff", "--name-only", base, merge, cwd=m).splitlines() if p]
    tests = [p for p in changed if is_test(p)]
    code = [p for p in changed if not is_test(p)]
    if not tests or not any(p.endswith(".py") for p in code):
        raise ValueError("diff has no test or no python source change")
    patch = git("diff", "--binary", base, merge, "--", *code, cwd=m)
    test_patch = git("diff", "--binary", base, merge, "--", *tests, cwd=m)

    issues = pr["closingIssuesReferences"]["nodes"]
    # the comp's statements are the PR's title + body and nothing else (checked on the dev set, see
    # restate.py), so ours are too. the issue text rides along for ablations
    issue_text = "\n\n".join(f"{i['title']}\n{i['body'] or ''}".strip() for i in issues)
    issue_hints = "\n\n".join(c["body"] for i in issues for c in i["comments"]["nodes"]
                              if c["createdAt"] < pr["createdAt"] and c.get("body"))
    iid = f"{short}_{pr['number']}"
    snapshot(m, base, snaps / f"{iid}.tgz")
    return {
        "instance_id": iid, "repo": pr["repo"], "base_commit": base,
        "problem_statement": f"{pr['title']}\n\n{pr['body'] or ''}", "hints_text": "",
        "issue_text": issue_text, "issue_hints": issue_hints,
        "patch": patch, "test_patch": test_patch, "created_at": pr["createdAt"],
        # provenance, the harness ignores these
        "pr": f"https://github.com/{pr['repo']}/pull/{pr['number']}",
        "issues": [i["number"] for i in issues], "license": pr.get("license"),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("repo", help="owner/name, must have data/mined/<owner>__<name>/prs.jsonl")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()
    slug = args.repo
    out = REPO / "data" / "mined" / slug.replace("/", "__")
    prs = [json.loads(ln) for ln in (out / "prs.jsonl").read_text(encoding="utf-8").splitlines() if ln]
    if args.limit:
        prs = prs[: args.limit]
    m = mirror(slug)
    short = slug.split("/")[1].lower().replace("-", "_")
    snaps = out / "snapshots"
    tasks, failed = [], {}
    for pr in prs:
        try:
            tasks.append(build_one(pr, m, short, snaps))
        except Exception as e:  # one weird PR shouldn't sink the batch
            failed[pr["number"]] = str(e)[:120]
    with (out / "tasks.jsonl").open("w", encoding="utf-8") as f:
        for t in tasks:
            f.write(json.dumps(t) + "\n")
    print(f"{slug}: built {len(tasks)} tasks, {len(failed)} failed")
    for n, e in list(failed.items())[:10]:
        print(f"  #{n}: {e}")
    shutil.rmtree(m / "refs" / "heads" / "g4base", ignore_errors=True)


if __name__ == "__main__":
    main()
