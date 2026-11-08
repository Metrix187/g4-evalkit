"""Stage 1 of task mining: list merged PRs that close an issue and touch both source and tests.

    python tools/mine/prs.py pallets/click --max 400

writes data/mined/<owner>__<name>/prs.jsonl. uses the gh CLI's auth, one GraphQL page (50 PRs)
per call, newest first. filtering here is cheap and loose; build.py and the grader do the real
weeding (patch splits, fail-to-pass).
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
OUT = REPO / "data" / "mined"

QUERY = """
query($owner: String!, $name: String!, $cursor: String) {
  repository(owner: $owner, name: $name) {
    licenseInfo { spdxId }
    pullRequests(states: MERGED, first: 50, after: $cursor, orderBy: {field: CREATED_AT, direction: DESC}) {
      pageInfo { hasNextPage endCursor }
      nodes {
        number title body createdAt mergedAt
        mergeCommit { oid }
        closingIssuesReferences(first: 3) {
          nodes {
            number title body createdAt
            comments(first: 30) { nodes { body createdAt author { login } } }
          }
        }
        files(first: 100) { totalCount nodes { path additions deletions } }
      }
    }
  }
}
"""

TEST_RE = re.compile(r"(^|/)(tests?|testing)/|(^|/)test_[^/]*\.py$|_test\.py$|(^|/)conftest\.py$")


def is_test(path: str) -> bool:
    return bool(TEST_RE.search(path))


def gql(owner: str, name: str, cursor: str | None) -> dict:
    cmd = ["gh", "api", "graphql", "-f", f"query={QUERY}", "-F", f"owner={owner}", "-F", f"name={name}"]
    if cursor:
        cmd += ["-F", f"cursor={cursor}"]
    res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    if res.returncode != 0:
        raise RuntimeError(res.stderr[:500])
    return json.loads(res.stdout)["data"]["repository"]


def keep(pr: dict) -> str | None:
    """None if the PR looks like a task, else the reason it doesn't."""
    if not pr["closingIssuesReferences"]["nodes"]:
        return "no linked issue"
    if not pr.get("mergeCommit"):
        return "no merge commit"
    files = pr["files"]["nodes"]
    if pr["files"]["totalCount"] > 30:
        return "too many files"
    py = [f for f in files if f["path"].endswith(".py")]
    src = [f for f in py if not is_test(f["path"])]
    tests = [f for f in files if is_test(f["path"])]
    if not src:
        return "no source change"
    if not tests:
        return "no test change"
    if sum(f["additions"] + f["deletions"] for f in src) > 400:
        return "source change too big"
    return None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("repo", help="owner/name")
    ap.add_argument("--max", type=int, default=400, help="PRs to scan")
    args = ap.parse_args()
    owner, name = args.repo.split("/")
    out = OUT / f"{owner}__{name}"
    out.mkdir(parents=True, exist_ok=True)

    cursor, seen, kept, why = None, 0, [], {}
    lic = None
    while seen < args.max:
        page = gql(owner, name, cursor)
        lic = (page.get("licenseInfo") or {}).get("spdxId")
        prs = page["pullRequests"]
        for pr in prs["nodes"]:
            seen += 1
            reason = keep(pr)
            if reason:
                why[reason] = why.get(reason, 0) + 1
            else:
                kept.append(pr)
        if not prs["pageInfo"]["hasNextPage"]:
            break
        cursor = prs["pageInfo"]["endCursor"]

    with (out / "prs.jsonl").open("w", encoding="utf-8") as f:
        for pr in kept:
            f.write(json.dumps({"repo": args.repo, "license": lic, **pr}) + "\n")
    print(f"{args.repo} ({lic}): scanned {seen}, kept {len(kept)}, dropped {why}")


if __name__ == "__main__":
    main()
