"""Rewrite mined problem statements the way the comp writes them: PR title + blank line + PR body, no hints.

    python tools/mine/restate.py

checked against the dev set: every comp task's problem_statement is exactly f"{title}\\n\\n{body}" of
its PR, and hints_text is always empty. we'd been using the linked issue's text plus pre-PR issue
comments, which is SWE-bench's convention and a much friendlier spec than "fix for markdown table /
Fixes #3053". the old text moves to issue_text / issue_hints so nothing's lost.

rewrites every data/mined/*/tasks.jsonl in place (heldout and heldout100 too, so the subset keeps
its task ids). verification doesn't read the statement, so verified status doesn't change.
"""

from __future__ import annotations

import json
from pathlib import Path

MINED = Path(__file__).resolve().parents[2] / "data" / "mined"


def statement(pr: dict) -> str:
    return f"{pr['title']}\n\n{pr['body'] or ''}"


def main() -> None:
    prs = {}
    for f in MINED.glob("*/prs.jsonl"):
        for ln in f.read_text(encoding="utf-8").splitlines():
            if ln:
                pr = json.loads(ln)
                prs[f"https://github.com/{pr['repo']}/pull/{pr['number']}"] = pr
    for f in sorted(MINED.glob("*/tasks.jsonl")):
        rows = [json.loads(ln) for ln in f.read_text(encoding="utf-8").splitlines() if ln]
        changed = 0
        for t in rows:
            pr = prs.get(t.get("pr"))
            if pr is None:
                continue
            if "issue_text" not in t:  # first pass only, so a rerun can't clobber the issue text
                t["issue_text"], t["issue_hints"] = t["problem_statement"], t["hints_text"]
            new = statement(pr)
            changed += new != t["problem_statement"] or t["hints_text"] != ""
            t["problem_statement"], t["hints_text"] = new, ""
        f.write_text("".join(json.dumps(t) + "\n" for t in rows), encoding="utf-8", newline="\n")
        print(f"{f.parent.name:<36}{len(rows):>5} tasks, {changed} restated")


if __name__ == "__main__":
    main()
