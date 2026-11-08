"""Per-commit dependency fix-up, run inside a sandbox right after the harness's own setup.py.

the harness always calls setup.py --fast-path: editable install with --no-deps, nothing else. so
every task gets whatever version of starlette/pydantic/pygments happens to be newest in the task
wheels, a bunch of older fastapi commits can't pass even with the gold patch, and test-only deps
(requirements-tests.txt, poetry dev groups) never get installed at all.

this reads the commit's own declared deps and pins:
  - pyproject [project] deps + optional deps + [dependency-groups]
  - poetry's [tool.poetry.dependencies] / dev-dependencies / group.*.dependencies
  - requirements*.txt (following -r)
  - if the commit has a lockfile (uv.lock / poetry.lock / pdm.lock), the locked version wins, which
    is what upstream's own CI ran. rich's syntax tests only match pygments 2.19.2 from its
    poetry.lock, and the task wheels only ship 2.20/2.21
then installs each one: newest matching wheel from /wheels first, else pypi, with pypi constrained
so it can't bump anything the first pass picked.

a deliberate deviation from the official grader. hidden tasks were verified in the hosts' env, so
this only changes how many *dev* tasks we can grade. see AGENTS.md.

usage (sandbox paths get rewritten by the subprocess manager):
    python3 sbx_resolve.py /workspace /wheels
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

from packaging.requirements import InvalidRequirement, Requirement

# the image owns these, same skip list as swegemma's _inspect_wheels_dir. plus `py`: old repos list
# the legacy py library, and installing it breaks the image pytest's own py shim (AttributeError: __spec__)
SKIP = {"pytest", "pluggy", "pip", "setuptools", "wheel", "py"}

UV = os.environ.get("G4_UV") or shutil.which("uv")


def norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def load_toml(p: Path) -> dict:
    try:
        return tomllib.loads(p.read_text(errors="ignore"))
    except (OSError, tomllib.TOMLDecodeError):
        return {}


def from_requirements_file(path: Path, seen: set[Path]) -> list[str]:
    if path in seen or not path.is_file():
        return []
    seen.add(path)
    out = []
    for raw in path.read_text(errors="ignore").splitlines():
        line = raw.split(" #", 1)[0].strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith(("-r ", "--requirement ")):
            out += from_requirements_file(path.parent / line.split(None, 1)[1], seen)
        elif line.startswith("-") or "://" in line or line.startswith("."):
            continue  # -e ., --index-url, local paths: the editable install already covered us
        else:
            out.append(line)
    return out


def poetry_names(table: dict) -> list[str]:
    # poetry's ^/~ specs don't parse as PEP 440. we only need the names: lockfile pins cover versions,
    # and anything unlocked just takes the newest wheel, same as the official fast path would
    # optional = an extra, and `poetry install` leaves extras out. rich's ipywidgets extra drags in
    # ipython, whose pygments plugin then wins guess_lexer and breaks rich's own syntax tests
    return [n for n, spec in table.items()
            if n != "python" and not (isinstance(spec, dict) and spec.get("optional"))]


def from_setup_py(path: Path) -> list[str]:
    # older repos declare deps in setup.py. read the literal lists out of setup(...) without running it
    import ast
    try:
        tree = ast.parse(path.read_text(errors="ignore"))
    except (OSError, SyntaxError, ValueError):
        return []
    out: list[str] = []
    # structlog-era setup.py: INSTALL_REQUIRES = ["six"] up top, setup(install_requires=INSTALL_REQUIRES)
    names = {t.id: n.value for n in tree.body if isinstance(n, ast.Assign)
             for t in n.targets if isinstance(t, ast.Name)}

    def strings(node, depth=0) -> list[str]:
        if isinstance(node, ast.Name) and node.id in names and depth < 3:
            return strings(names[node.id], depth + 1)
        if isinstance(node, (ast.List, ast.Tuple)):
            return [e.value for e in node.elts if isinstance(e, ast.Constant) and isinstance(e.value, str)]
        if isinstance(node, ast.Dict):
            return [s for v in node.values for s in strings(v, depth)]
        if isinstance(node, ast.BinOp):  # ["a"] + TESTS_REQUIRE
            return strings(node.left, depth) + strings(node.right, depth)
        return []

    for n in ast.walk(tree):
        if isinstance(n, ast.Call) and getattr(n.func, "id", getattr(n.func, "attr", "")) == "setup":
            for kw in n.keywords:
                if kw.arg in ("install_requires", "extras_require", "tests_require"):
                    out += strings(kw.value)
    return out


def collect(ws: Path) -> list[str]:
    reqs: list[str] = []
    data = load_toml(ws / "pyproject.toml")
    proj = data.get("project", {})
    reqs += proj.get("dependencies", [])
    for group in proj.get("optional-dependencies", {}).values():
        reqs += group
    for group in data.get("dependency-groups", {}).values():
        reqs += [r for r in group if isinstance(r, str)]  # skip {include-group = ...} tables
    poetry = data.get("tool", {}).get("poetry", {})
    reqs += poetry_names(poetry.get("dependencies", {}))
    reqs += poetry_names(poetry.get("dev-dependencies", {}))
    for group in poetry.get("group", {}).values():
        reqs += poetry_names(group.get("dependencies", {}))
    if (ws / "setup.py").is_file():
        reqs += from_setup_py(ws / "setup.py")
    seen: set[Path] = set()
    # root requirements*.txt, plus the pallets-style requirements/ folder of pip-compiled pins
    for f in [*sorted(ws.glob("requirements*.txt")), *sorted(ws.glob("requirements/*.txt"))]:
        reqs += from_requirements_file(f, seen)
    return reqs


def base_date(ws: Path) -> str | None:
    # the snapshot's own history ends at the base commit; the harness then adds "baseline" and
    # "eval_baseline" commits stamped today, so skip those
    res = subprocess.run(["git", "-C", str(ws), "log", "-1", "--format=%cI", "--grep=baseline", "--invert-grep"],
                         capture_output=True, text=True)
    return res.stdout.strip() or None


def locked_versions(ws: Path) -> dict[str, str]:
    for name in ("uv.lock", "poetry.lock", "pdm.lock"):
        data = load_toml(ws / name)
        pkgs = data.get("package", [])
        if pkgs:
            return {norm(p["name"]): str(p["version"]) for p in pkgs if "name" in p and "version" in p}
    return {}


def own_name(ws: Path) -> str:
    data = load_toml(ws / "pyproject.toml")
    name = data.get("project", {}).get("name") or data.get("tool", {}).get("poetry", {}).get("name")
    return norm(name or ws.name)


def plan(ws: Path) -> list[str]:
    own, locks = own_name(ws), locked_versions(ws)
    keep: dict[str, str] = {}
    for r in collect(ws):
        try:
            req = Requirement(r)
        except InvalidRequirement:
            continue
        n = norm(req.name)
        if n in SKIP or n == own or n in keep:
            continue  # first spelling wins: pyproject before requirements files
        if req.marker is not None and not req.marker.evaluate({"extra": ""}):
            continue
        name = req.name + (f"[{','.join(sorted(req.extras))}]" if req.extras else "")
        keep[n] = f"{name}=={locks[n]}" if n in locks else (name + str(req.specifier))
    return list(keep.values())


def install(*args: str) -> subprocess.CompletedProcess:
    if UV:
        cmd = [UV, "pip", "install", "-q", "--python", sys.executable, *args]
    else:
        cmd = [sys.executable, "-m", "pip", "install", "-q", "--disable-pip-version-check", *args]
    return subprocess.run(cmd, capture_output=True, text=True)


def freeze() -> list[str]:
    if UV:
        out = subprocess.run([UV, "pip", "freeze", "--python", sys.executable], capture_output=True, text=True).stdout
    else:
        out = subprocess.run([sys.executable, "-m", "pip", "freeze"], capture_output=True, text=True).stdout
    # constraints files reject unnamed/editable lines, and freeze always has the repo's own -e line
    return [ln for ln in out.splitlines() if ln and not ln.startswith(("-e", "#")) and " @ " not in ln]


def image_holds() -> list[str]:
    """Pins for the packages the image's pytest stands on. an old plugin pulled from pypi would happily
    drag in a 2023 pluggy and break it (tomlkit: ImportError HookimplOpts). read with importlib.metadata,
    not freeze: they live in ~/g4sbx, reached through a .pth that `uv pip freeze` on the venv doesn't see"""
    from importlib import metadata
    held = []
    for name in ("pytest", "pluggy", "iniconfig"):
        try:
            held.append(f"{name}=={metadata.version(name)}")
        except metadata.PackageNotFoundError:
            pass
    return held


def time_pinned(specs: list[str], when: str) -> tuple[list[str], list[str]]:
    """Mined sets: install each requirement as of the base commit's date (SWE-bench style), so an old
    flask gets the werkzeug it was written against. old releases often have no cp313 wheel and won't
    build, so a failure retries without the date before giving up."""
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
        f.write("\n".join(image_holds()) + "\n")
        hold = f.name
    pinned, rest = [], []
    for spec in specs:
        if UV and install("--exclude-newer", when, "-c", hold, spec).returncode == 0:
            pinned.append(spec)
        else:
            rest.append(spec)
    return pinned, rest


def main() -> None:
    ws, wheels = Path(sys.argv[1]), Path(sys.argv[2])
    specs = plan(ws)

    dated: list[str] = []
    when = base_date(ws) if os.environ.get("G4_TIME_PINNED") else None
    if when:
        dated, specs = time_pinned(specs, when)

    offline, missing = [], []
    for spec in specs:
        res = install("--no-index", f"--find-links={wheels}", "--no-deps", spec)
        (offline if res.returncode == 0 else missing).append(spec)

    online_ok, online_fail = [], []
    if missing:
        held = image_holds()
        held_names = {norm(h.split("==")[0]) for h in held}
        pins_lines = [ln for ln in freeze() if norm(ln.split("==")[0]) not in held_names] + held
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
            f.write("\n".join(pins_lines) + "\n")
            pins = f.name
        for spec in missing:
            res = install("-c", pins, spec)
            if res.returncode == 0:
                online_ok.append(spec)
            else:
                why = (res.stderr.strip().splitlines() or ["?"])[-1][:160]
                online_fail.append(f"{spec}  ({why})")

    # src-layout repos lose to installed copies of themselves: g4sbx carries click/flask/attrs/...
    # because the task wheels do, it's on sys.path via a plain .pth, and modern editable installs
    # hook in through a meta-path finder that only runs after the normal path search. a .pth line
    # starting with "import" runs at startup, so it can put src/ in front of everything
    if (ws / "src").is_dir():
        site = next(p for p in sys.path if p.endswith("site-packages") and p.startswith(sys.prefix))
        Path(site, "_g4_ws_first.pth").write_text(
            f"import sys; sys.path.insert(0, {str((ws / 'src').resolve())!r})\n")

    tool = "uv" if UV else "pip"
    print(f"sbx_resolve[{tool}]: {len(dated)} pinned to {when or '-'}, {len(offline)} from task wheels, "
          f"{len(online_ok)} from pypi, {len(online_fail)} unresolved, lockfile pins: {len(locked_versions(ws)) > 0}")
    for spec in online_ok:
        print(f"  pypi: {spec}")
    for spec in online_fail:
        print(f"  unresolved: {spec}")


if __name__ == "__main__":
    main()
