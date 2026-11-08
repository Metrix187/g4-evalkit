"""A subprocess sandbox that grades like the official docker image, minus its blind spots.

stock SubprocessManager hands every sandbox venv a .pth into the harness's own site-packages.
the harness pulls in rich, httpx, requests, pygments... the same packages several dev repos *are*,
so tests end up running against the wrong copy. turning that off leaves no pytest at all, and the
harness skips installing task wheels for anything named SubprocessManager.

so sandboxes here see ~/g4sbx instead: what docker/Dockerfile.sandbox pip-installs plus the task
wheels the harness would inject, plus the imp/telnetlib shims (tools/setup_wsl_env.sh builds it).
then, right after the harness's setup.py, tools/sbx_resolve.py installs the commit's own pinned
deps, because the official fast path never does and older fastapi commits fall over without it.

same idea works on colab, which has no docker either.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import threading
from pathlib import Path

from swegemma.sandbox.subprocess import SubprocessManager

SBX_ENV = Path(os.environ.get("G4_SANDBOX_ENV", Path.home() / "g4sbx"))
RESOLVER = Path(__file__).with_name("sbx_resolve.py")
PIP_CACHE = Path.home() / ".cache" / "g4pip"
# the sandbox env is sanitized (HOME moves), so caches and uv itself get passed in by absolute path
UV_CACHE = Path.home() / ".cache" / "g4uv"
UV_BIN = shutil.which("uv") or next((str(p) for p in [Path.home() / ".local/bin/uv"] if p.exists()), None)

# install_test_dependencies runs `python3 /tmp/setup.py --fast-path <repo>` (or /sandbox/setup.py)
_SETUP_CALL = re.compile(r"python3\s+\S*setup\.py\s+--fast-path\s*(\S*)")


def _sbx_site_packages() -> Path:
    hits = sorted(SBX_ENV.glob("lib/python*/site-packages"))
    if not hits:
        raise FileNotFoundError(f"no sandbox base env at {SBX_ENV}, run tools/setup_wsl_env.sh")
    return hits[0]


class DockerLikeSubprocessManager(SubprocessManager):
    def __init__(self, timeout_seconds: int = 300, resolve_log: Path | None = None,
                 wheels_dir: Path | None = None) -> None:
        super().__init__(timeout_seconds=timeout_seconds, system_site_packages=False)
        self._sbx_site = _sbx_site_packages()
        self._sbx_bin = SBX_ENV / "bin"
        self._resolve_log = resolve_log
        self._wheels = sorted(Path(wheels_dir).glob("*.whl")) if wheels_dir else []
        self._log_lock = threading.Lock()
        PIP_CACHE.mkdir(parents=True, exist_ok=True)
        UV_CACHE.mkdir(parents=True, exist_ok=True)

    def start(self, *args, **kwargs) -> str:
        sid = super().start(*args, **kwargs)
        venv = self._sandboxes[sid]["venv"]
        # the harness's own wheel copy lands somewhere exec()'s /wheels rewrite never looks, so
        # `pip --find-links=/wheels` silently finds nothing and falls back to building from pypi.
        # flat symlinks in the sandbox's wheels dir fix that, and the harness then skips its copy
        whl_dir = self._sandboxes[sid]["wheels"]
        for w in self._wheels:
            link = whl_dir / w.name
            if not link.exists():
                link.symlink_to(w)
        for sp in venv.glob("lib/python*/site-packages"):
            (sp / "_sbx_base.pth").write_text(f"{self._sbx_site}\n", encoding="utf-8")
        # an agent typing bare `pytest` would otherwise fall through PATH to the harness env's pytest
        for name in ("pytest", "py.test"):
            dst = venv / "bin" / name
            if not dst.exists():
                dst.write_text(f'#!/bin/sh\nexec "{venv}/bin/python3" -m pytest "$@"\n', encoding="utf-8")
                dst.chmod(0o755)
        return sid

    def exec(self, sandbox_id: str, command: str, *, timeout: int | None = None):
        res = super().exec(sandbox_id, command, timeout=timeout)
        m = _SETUP_CALL.search(command)
        if m:
            fix = super().exec(
                sandbox_id,
                f"PIP_CACHE_DIR={PIP_CACHE} UV_CACHE_DIR={UV_CACHE} G4_UV={UV_BIN or ''} "
                f"G4_TIME_PINNED={os.environ.get('G4_TIME_PINNED', '')} "
                f"python3 {RESOLVER} /workspace /wheels",
                timeout=900,
            )
            head = super().exec(sandbox_id, "git -C /workspace rev-parse HEAD", timeout=30)
            self._log({
                "repo": m.group(1),
                "head": (head.stdout or "").strip(),
                "exit_code": fix.exit_code,
                "out": (fix.stdout or "")[-3000:],
                "err": (fix.stderr or "")[-2000:],
            })
        return res

    def _log(self, row: dict) -> None:
        if self._resolve_log is None:
            return
        with self._log_lock, self._resolve_log.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")
