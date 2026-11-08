"""Zip a candidate into a submission.zip the scorer will accept.

    python tools/package.py submissions/think1k              # -> results/packages/think1k.zip

agent.yaml at the archive root, only the extensions the scorer allows, under 3 GiB unpacked,
and a sha256 printed so the leaderboard entry can be traced back to an exact config. submitting
is a separate, manual step: sky okays every kaggle submission in chat (AGENTS.md).
"""

from __future__ import annotations

import hashlib
import sys
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ALLOWED = {".yaml", ".yml", ".md", ".txt", ".py", ".json", ".safetensors"}
MAX_BYTES = 3 * 1024 ** 3


def main() -> None:
    cand = Path(sys.argv[1]).resolve()
    if not (cand / "agent.yaml").is_file():
        raise SystemExit(f"{cand} has no agent.yaml at its root")
    files = sorted(p for p in cand.rglob("*") if p.is_file())
    bad = [p for p in files if p.suffix.lower() not in ALLOWED]
    if bad:
        raise SystemExit(f"scorer would reject: {[str(p.relative_to(cand)) for p in bad]}")
    total = sum(p.stat().st_size for p in files)
    if total >= MAX_BYTES:
        raise SystemExit(f"{total / 1e9:.2f} GB unpacked, limit is 3 GiB")
    out = REPO / "results" / "packages" / f"{cand.name}.zip"
    out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in files:
            zf.write(p, p.relative_to(cand).as_posix())
    digest = hashlib.sha256(out.read_bytes()).hexdigest()
    print(f"{out}  {out.stat().st_size / 1e3:.1f} kB zipped, {total / 1e3:.1f} kB unpacked, sha256 {digest[:16]}")
    for p in files:
        print("  ", p.relative_to(cand).as_posix())


if __name__ == "__main__":
    main()
