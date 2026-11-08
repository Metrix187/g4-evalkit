# Local grader and compute: what we measured (2026-10-01)

## The local grader

Gold-patch pass rate on the 129 dev tasks, grading in WSL with the subprocess sandbox:

| setup | gold patches passing |
|---|---|
| stock `--sandbox subprocess` (smoke test, 4 tasks) | 1/4 |
| isolated venv, no host packages | 0/4 (no pytest at all) |
| docker-like base env (image packages + harness-picked task wheels) | 2/4 |
| + per-commit resolver with lockfile pins (`tools/sbx_resolve.py`) | **119/129** |

What was wrong, in order of discovery:

1. **Host leakage.** The stock subprocess sandbox `.pth`-links the harness's own site-packages into every sandbox. The harness depends on rich, httpx and requests, which are three of the four dev repos. httpx tests imported the wrong `httpx`, and rich ran against the host's pygments.
2. **No test deps in subprocess mode.** `install_test_dependencies` returns early for classes named `SubprocessManager`.
3. **`/wheels` resolves to nothing.** The harness copies wheels somewhere `exec()`'s path rewrite never looks, so even the editable install fell back to building from PyPI.
4. **No per-commit pins, even in the official docker path.** It always injects the *newest* version of each wheel (`_deduplicate_wheels`) and calls `setup.py --fast-path`, which installs `--no-deps`. Older fastapi commits get the newest starlette and break, and test-only deps (pytest-httpbin, inline-snapshot, ujson, orjson, ...) never arrive. This is probably why the forum reports many gold patches failing locally.
5. **Lockfiles matter.** rich's syntax tests only match the pygments 2.19.2 pinned in its `poetry.lock`; the task wheels ship only 2.20 and 2.21.
6. **Poetry extras must stay out.** Installing rich's optional `ipywidgets` pulls in IPython, whose pygments plugin wins `guess_lexer`.
7. **Global test extras broke rich.** inline-snapshot imports `attrs`, and rich's `examples/attrs.py` (on `sys.path` via the fast path's `workspace_paths.pth`) calls `SystemExit`.

The 10 that still fail with the gold patch are left out of the gate:
- 3 fastapi router-context commits (15745, 15763, 15785)
- fastapi_14186, where a required node is never collected
- 4 requests tasks with network/cert/old-werkzeug issues (6589, 6592, 6629, 6757)
- 2 rich tasks (3472: attrs error text on Python 3.13; 3486: required node not collected)

**Gradeable = gold passes and an empty patch fails.** See `results/gold_full/` and `tools/make_task_list.py`.

This deliberately departs from the official grader. The hidden tasks were verified in the hosts' environment (which has its own `metric-data/sandbox/wheels`), so the departure only changes how many *dev* tasks we can grade, not what the scorer will do.

## Mined tasks: what the grader taught us about the hidden set

- **The harness can't find `setup.py` for a mined set.** It looks next to `tasks.jsonl`, so we set `KAGGLE_SANDBOX_DIR`. Without that, click verified 26/66 by luck; with it, 60/66.
- **src-layout repos lose to installed copies of themselves** (click, flask, attrs... are in the task wheels). The fix is a `.pth` that puts `src/` first.
- **The official pytest call disables anyio (`-p no:anyio`)**, so starlette and uvicorn (anyio-plugin suites) can never pass: 0/20 and 0/10. The hosts verified hidden tasks with the same command, **so the hidden set can't contain anyio-plugin tests**.
- **Python 3.13 plus current pytest cuts off old eras**: flask tests that use pytest internals (`monkeypatch.notset`), and structlog 2019 `six.moves`. Same logic: hidden tasks must run on 3.13 and current pytest.
- **Time-pinned deps (SWE-bench style, `uv --exclude-newer` at the base commit date) didn't help**: flask stayed at 2/36 and structlog went from 17 to 12 of 47, because old releases don't run on 3.13. It stays off; the code is behind `G4_TIME_PINNED=1`.
- **Old repos list the legacy `py` library**, and installing it shadows the image pytest's own `py` shim (`AttributeError: __spec__` at collection). The resolver skips it, same as pytest/pluggy. jinja 15 → 20 of 66, more-itertools 39 → 53 of 56.
- **A PyPI-fallback plugin can drag in a 2023 pluggy** and break the image pytest (`ImportError: HookimplOpts`, tomlkit). The fallback is now constrained to the image's pytest/pluggy/iniconfig. tomlkit's remaining failures need the `tests/toml-test` git submodule, which snapshots don't carry, so they stay out.
- **Final regrade (Oct 1, one resolver for everything):** 602 built → 345 verified; dev map unchanged at 119 gold / 2 empty (`results/gold_full3/`).
- **werkzeug has a malformed 2008 author ident** that `git fast-import` rejects, so build.py falls back to clone + prune for snapshots.

## Compute: Kaggle 4×L4, the scorer's exact vLLM config (probe kernel `goth55/g4-probe`)

- 4× L4 (23 GB each), 48 CPUs, 188 GB RAM, 20 GB `/kaggle/working`, Python 3.12.
- Wheelhouse install takes 61 s and vLLM boot takes 386 s.
- Decode speed, with 768-token outputs on a ~260-token prompt:

| concurrent streams | tok/s per stream | total tok/s |
|---|---|---|
| 1 | 39.2 | 39 |
| 4 | 33.6 | 134 |
| 8 | 21.4 | 171 |
| 16 | 22.3 | 356 |

- **Batch kernel runs skip the interactive L4 queue.** The probe started immediately, then spent about 35-40 min provisioning (mounting ~40 GB of inputs) before the script ran. Provisioning isn't billed: the probe cost 0.35 GPU-h.
- **L4 kernels for this comp must run with internet off** (the API refuses otherwise).
- The quota is 30 GPU-h a week, resetting Saturday 00:00 UTC. **An L4×4 session burns it at 2× wall clock** (r001: 4 h of wall time cost about 8 quota-hours), so a week buys about 15 wall-hours on the scorer's box. Size gate runs to fit that.

What this means for a single task on the scorer: about 5 min at roughly 39 tok/s is about 11k decoded tokens across all turns, before counting prefill. A 4096-token thinking budget per turn can use that up in three turns.
