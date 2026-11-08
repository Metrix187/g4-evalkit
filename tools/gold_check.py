"""Push every dev task through the real scorer path with a fake "agent".

--mode gold    : the agent hands back the reference patch. a task that fails here can't be
                 solved by *any* agent on this box, so the gate has to drop it.
--mode empty   : the agent hands back nothing. a task that passes here is free points, and
                 also useless for telling two agents apart.
--mode patches : the agent hands back <patches-dir>/<instance_id>.patch, i.e. whatever a real
                 agent produced on kaggle. this is how every gate run gets graded, so all
                 comparisons go through one grader.

phase 2 (fresh sandbox, apply patch, reset tests, apply test_patch, pytest + junit checks) is
swegemma's own verify_task, untouched. only phase 1 is swapped out.

run inside WSL with ~/g4env active:
    python tools/gold_check.py --mode gold --concurrency 4
"""

from __future__ import annotations

import argparse
import asyncio
import collections
import json
import os
import time
from pathlib import Path

from adk_submission import ModelRegistry
from adk_eval_core.tracing import SessionTrace
from swegemma.config import EvalConfig
from swegemma.evaluate import Evaluator
from swegemma.models import load_tasks

from sandbox import DockerLikeSubprocessManager

REPO = Path(__file__).resolve().parents[1]
DATA = REPO / "data" / "raw" / "comp"


class CannedEvaluator(Evaluator):
    """Evaluator whose agent always returns the same canned patch for a task."""

    def __init__(self, config: EvalConfig, mode: str, isolated: bool = False,
                 patches_dir: Path | None = None) -> None:
        super().__init__(config)
        self.mode = mode
        self.patches_dir = patches_dir
        if isolated and config.sandbox == "subprocess":
            # see tools/sandbox.py for why the stock subprocess sandbox can't be trusted here
            self.sandbox = self.docker = DockerLikeSubprocessManager(
                timeout_seconds=config.harness.command_timeout_seconds or 300,
                resolve_log=Path(config.results_dir) / f"{mode}_resolve.jsonl",
                wheels_dir=config.wheels_dir,
            )

    async def _run_agent_sandbox(self, task, snapshot_path, task_index, total_tasks, **_):
        if self.mode == "gold":
            patch = task.patch
        elif self.mode == "patches":
            f = self.patches_dir / f"{task.instance_id}.patch"
            patch = f.read_text(encoding="utf-8", errors="replace") if f.is_file() else ""
        else:
            patch = ""
        return patch, None, SessionTrace()


def build_config(args) -> EvalConfig:
    # --data-dir points at any task set in the harness layout (tasks.jsonl + snapshots/), e.g. a
    # mined one under data/mined/. wheels and the never-compiled sample submission always come
    # from the comp data; anything the task wheels lack, sbx_resolve pulls from pypi
    d = Path(args.data_dir) if args.data_dir else DATA
    return EvalConfig(
        tasks_path=d / "tasks.jsonl",
        snapshots_dir=d / "snapshots",
        results_dir=Path(args.out),
        # never compiled, the canned agent skips phase 1. it just has to exist
        submission_dir=DATA / "sample_submission",
        models=ModelRegistry(),
        sandbox=args.sandbox,
        wheels_dir=DATA / "wheels",
        graph_dir=str((d if (d / "graphs").is_dir() else DATA) / "graphs"),
        embeddings_dir=str((d if (d / "embeddings").is_dir() else DATA) / "embeddings"),
        task_ids=args.task_ids or None,
        concurrency=args.concurrency,
        display_mode="quiet",
        # skip_agent_patch makes verify run even when the patch is empty, which is what --mode empty needs
        skip_agent_patch=(args.mode == "empty"),
    )


async def main_async(args) -> None:
    # the harness looks for sandbox/setup.py next to tasks.jsonl, and a mined set doesn't have one.
    # without it there's no editable install and no pytest config, and grades are half luck
    os.environ.setdefault("KAGGLE_SANDBOX_DIR", str(DATA / "sandbox"))
    # G4_TIME_PINNED=1 resolves deps as of each base commit's date. off by default: on flask and
    # structlog it went 2/36 -> 2/36 and 17/47 -> 12/47, because old releases don't run on py3.13
    # (which the official image is), so pinning to the era just trades one break for another
    cfg = build_config(args)
    patches_dir = Path(args.patches_dir) if args.patches_dir else None
    ev = CannedEvaluator(cfg, args.mode, isolated=args.isolated, patches_dir=patches_dir)
    tasks = load_tasks(cfg.tasks_path)
    if cfg.task_ids:
        tasks = [t for t in tasks if t.instance_id in set(cfg.task_ids)]
    elif args.mode == "patches":
        # grade exactly the tasks the run attempted. a missing .patch file would grade as empty
        attempted = {p.stem for p in patches_dir.glob("*.patch")}
        tasks = [t for t in tasks if t.instance_id in attempted]

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    rows_path = out / f"{args.mode}_results.jsonl"

    sem = asyncio.Semaphore(args.concurrency)
    t0 = time.time()

    async def one(i, task):
        async with sem:
            res = await ev.evaluate_task(task, task_index=i, total_tasks=len(tasks))
            row = {
                "instance_id": task.instance_id,
                "repo": task.repo,
                "mode": args.mode,
                "resolved": bool(res.resolved),
                "test_exit_code": res.test_exit_code,
                "error": (res.error or "")[:500],
                "seconds": round(res.duration_seconds, 1),
                "test_tail": (res.test_output or "")[-1500:],
            }
            with rows_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(row) + "\n")
            print(f"[{i}/{len(tasks)}] {task.instance_id:<22} resolved={row['resolved']} "
                  f"exit={row['test_exit_code']} {row['seconds']}s {row['error'][:80]}", flush=True)
            return row

    rows = await asyncio.gather(*(one(i, t) for i, t in enumerate(tasks, 1)))
    # the per-row appends are just crash insurance. on /mnt/d two of 129 rows went missing once
    # (both 5-min timeouts), so rewrite the whole file from memory now that we have everything
    rows_path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")

    by_repo = collections.defaultdict(lambda: [0, 0])
    for r in rows:
        by_repo[r["repo"]][0] += r["resolved"]
        by_repo[r["repo"]][1] += 1
    summary = {
        "mode": args.mode,
        "sandbox": args.sandbox + ("+isolated" if args.isolated else ""),
        "tasks": len(rows),
        "resolved": sum(r["resolved"] for r in rows),
        "by_repo": {k: {"resolved": v[0], "tasks": v[1]} for k, v in sorted(by_repo.items())},
        "minutes": round((time.time() - t0) / 60, 1),
    }
    (out / f"{args.mode}_summary.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mode", choices=["gold", "empty", "patches"], default="gold")
    ap.add_argument("--patches-dir", help="for --mode patches: folder of <instance_id>.patch files")
    ap.add_argument("--data-dir", help="task set in harness layout (default: the comp's dev set)")
    ap.add_argument("--sandbox", choices=["subprocess", "docker"], default="subprocess")
    ap.add_argument("--isolated", action="store_true",
                    help="subprocess sandbox without the host site-packages leaking in")
    ap.add_argument("--concurrency", type=int, default=4)
    ap.add_argument("--task-ids", nargs="*")
    ap.add_argument("--out", default=str(REPO / "results" / "gold_check"))
    asyncio.run(main_async(ap.parse_args()))


if __name__ == "__main__":
    main()
