"""Fill ~/g4sbx with the exact wheel set a docker sandbox would get.

the docker path (install_test_dependencies -> _ensure_container_site_packages) unpacks every
"base" wheel from the task data into site-packages: deduped to the highest version, filtered to
cp313-compatible, minus pytest/pluggy/setuptools/wheel which the image already has. "large" wheels
(>= 20 MB) only go in when the repo imports them, but the dev set has none, so a static env
is an exact match. we ask swegemma for the list instead of guessing.

run with ~/g4env active (it has swegemma):  python tools/build_sbx.py
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from swegemma.harness.container_setup import _inspect_wheels_dir

REPO = Path(__file__).resolve().parents[1]
WHEELS = REPO / "data" / "raw" / "comp" / "wheels"
SBX = Path.home() / "g4sbx"


def main() -> None:
    base, large, _, _ = _inspect_wheels_dir(WHEELS)
    if large:
        # not true for the dev set, but the hidden set's wheels could differ. say so loudly
        print(f"heads up: {len(large)} large wheels get per-repo injection in docker, adding all of them:",
              ", ".join(sorted(large)), file=sys.stderr)
    picks = [str(p) for p in base] + [str(p) for p in large.values()]
    print(f"installing {len(picks)} wheels into {SBX}")
    subprocess.run(
        ["uv", "pip", "install", "-q", "--python", str(SBX / "bin" / "python"),
         "--no-deps", "--reinstall", *picks],
        check=True,
    )
    for p in sorted(Path(x).name for x in picks):
        print("  ", p)


if __name__ == "__main__":
    main()
