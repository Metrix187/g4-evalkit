"""Run candidate agents on kaggle's L4x4 box, bring the patches home, grade them here.

    python tools/kaggle_gate.py push  --run r001 --candidates submissions/base submissions/v1 \
                                      --tasks notes/gate_tasks.txt --concurrency 4
    python tools/kaggle_gate.py status --run r001
    python tools/kaggle_gate.py fetch  --run r001          # downloads kernel output
    python tools/kaggle_gate.py grade  --run r001          # local grader, via WSL

kaggle side runs phase 1 with the stock harness (see kaggle/gate_template.py). everything gets
graded by tools/gold_check.py --mode patches so A and B always face the same judge.
run windows-side with the kaggleulturie venv python (it has the kaggle package + creds).
"""

from __future__ import annotations

import argparse
import base64
import datetime as dt
import hashlib
import io
import json
import subprocess
import tarfile
import time
import zlib
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
RUNS = REPO / "results" / "runs"
KDIR = REPO / "kaggle" / "runs"
USER = "goth55"
SOURCES = {
    "dataset_sources": ["metric/gemma-4-developer-agent-wheelhouse"],
    "competition_sources": ["gemma-4-developer-agent"],
    "model_sources": ["google/gemma-4/Other/gemma-4-31b-it-qat-w4a16-ct/2"],
}
# the comp's L4 image jumped from python 3.12 to 3.13 by oct 3, but wheelhouse v28 is still cp312-only, so
# every gate died at pip install 4 s in. stay on the image r001-r003 ran on until the wheels catch up
DOCKER_IMAGE = "gcr.io/kaggle-private-byod/python@sha256:37c64f7dd9c54116ecd1bcc88817c5469b88387388fade02bfa8bf3fc647d461"


def api():
    from kaggle.api.kaggle_api_extended import KaggleApi
    a = KaggleApi()
    a.authenticate()
    return a


def pack(cands: list[Path]) -> tuple[str, dict]:
    adapters_seen: dict[str, str] = {}
    info = {}
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for c in cands:
            if not (c / "agent.yaml").is_file():
                raise SystemExit(f"{c} has no agent.yaml")
            for a in (c / "adapters").glob("*") if (c / "adapters").is_dir() else []:
                # one vLLM serves every candidate, so adapter names share a namespace
                if a.name in adapters_seen:
                    raise SystemExit(f"adapter name '{a.name}' is in both {adapters_seen[a.name]} and {c.name}")
                adapters_seen[a.name] = c.name
            h = hashlib.sha256()
            for f in sorted(p for p in c.rglob("*") if p.is_file()):
                h.update(f.relative_to(c).as_posix().encode())
                h.update(f.read_bytes())
            info[c.name] = {"path": str(c.relative_to(REPO)), "sha256": h.hexdigest()[:16]}
            # adapter weights are way past the inline limit, so they ride in a private dataset
            # (--adapters-data) and the kernel copies them back in, checked against these hashes
            weights = {a.parent.name: hashlib.sha256(a.read_bytes()).hexdigest()
                       for a in (c / "adapters").glob("*/adapter_model.safetensors")}
            if weights:
                info[c.name]["adapter_weights"] = weights
            tf.add(c, arcname=c.name, filter=lambda ti: None if ti.name.endswith("adapter_model.safetensors") else ti)
    blob = base64.b64encode(buf.getvalue()).decode()
    if len(blob) > 8_000_000:
        raise SystemExit("candidates too big to inline even without adapter weights")
    return blob, info


def local_tasks(slug: str) -> Path | None:
    for meta in (REPO / "data" / "mined").glob("*/dataset-metadata.json"):
        if json.loads(meta.read_text()).get("id") == slug and (meta.parent / "tasks.jsonl").is_file():
            return meta.parent / "tasks.jsonl"
    return None


def push(args) -> None:
    cands = [Path(c).resolve() for c in args.candidates]
    task_ids = [ln.split()[0] for ln in Path(args.tasks).read_text().splitlines()
                if ln.strip() and not ln.startswith("#")]
    blob, info = pack(cands)
    if any("adapter_weights" in v for v in info.values()) and not args.adapters_data:
        raise SystemExit("a candidate has adapter weights: upload them (tools/upload_adapters.py) and pass --adapters-data")
    run = {"run": args.run, "task_ids": task_ids, "concurrency": args.concurrency, "time_scale": args.time_scale,
           "data": args.data, "model": args.model,
           "candidates": info, "pushed_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")}
    # ship our local tasks.jsonl along with the run when we have the dataset's folder here. re-uploading
    # 2 GB of snapshots because a problem statement changed (restate.py) is silly
    override = local_tasks(args.data) if args.data else None
    remote = run
    if override:
        # kaggle caps kernel source at 1 MB and all of g4-mined's tasks.jsonl alone is past that,
        # so only ship the rows this run actually needs
        keep = set(task_ids)
        rows = [ln for ln in override.read_bytes().splitlines() if ln.strip() and json.loads(ln)["instance_id"] in keep]
        missing = keep - {json.loads(ln)["instance_id"] for ln in rows}
        if missing:
            raise SystemExit(f"{len(missing)} task ids aren't in {override}: {sorted(missing)[:5]}")
        raw = b"".join(ln + b"\n" for ln in rows)
        run["tasks_override"] = {"from": override.relative_to(REPO).as_posix(), "sha256": hashlib.sha256(raw).hexdigest()}
        remote = {**run, "tasks_override_b64": base64.b64encode(zlib.compress(raw, 9)).decode()}
        print(f"embedding {run['tasks_override']['from']} ({len(raw) // 1024} KB) over the dataset's tasks.jsonl")
    src = (REPO / "kaggle" / "gate_template.py").read_text()
    src = src.replace("__RUN_JSON_B64__", base64.b64encode(json.dumps(remote).encode()).decode())
    src = src.replace("__CANDIDATES_TGZ_B64__", blob)

    slug = args.slot
    kd = KDIR / args.run
    kd.mkdir(parents=True, exist_ok=True)
    (kd / "run_gate.py").write_text(src)
    meta = {"id": f"{USER}/{slug}", "title": slug, "code_file": "run_gate.py", "language": "python",
            "kernel_type": "script", "is_private": True, "enable_gpu": True, "enable_tpu": False,
            "enable_internet": False, "machine_shape": "NvidiaL4", "keywords": [], "kernel_sources": [],
            **SOURCES, "model_sources": [f"google/gemma-4/Other/{args.model}/2"]}
    if args.data:
        meta["dataset_sources"] = [*meta["dataset_sources"], args.data]
    if args.adapters_data:
        meta["dataset_sources"] = [*meta["dataset_sources"], args.adapters_data]
    if args.docker_image:
        meta["docker_image"] = args.docker_image
    (kd / "kernel-metadata.json").write_text(json.dumps(meta, indent=2))

    rd = RUNS / args.run
    rd.mkdir(parents=True, exist_ok=True)
    run["kernel"] = meta["id"]
    (rd / "run.json").write_text(json.dumps(run, indent=2))
    r = api().kernels_push(str(kd))
    print("pushed", getattr(r, "url", r), "| version", getattr(r, "version_number", "?"),
          "| tasks", len(task_ids), "| candidates", ", ".join(info))


def wsl_path(p: Path) -> str:
    # everything we hand to WSL lives under the repo, which WSL sees at /mnt/d/gemma4-agent
    return "/mnt/d/gemma4-agent/" + p.resolve().relative_to(REPO).as_posix()


def run_meta(name: str) -> dict:
    return json.loads((RUNS / name / "run.json").read_text())


def status(args) -> None:
    meta = run_meta(args.run)
    print(api().kernels_status(meta["kernel"]))


def fetch(args) -> None:
    meta = run_meta(args.run)
    out = RUNS / args.run / "kaggle_out"
    out.mkdir(parents=True, exist_ok=True)
    a = api()
    if args.wait:
        while True:
            s = str(getattr(a.kernels_status(meta["kernel"]), "status", ""))
            print(time.strftime("%H:%M:%S"), s, flush=True)
            if "RUNNING" not in s and "QUEUED" not in s:
                break
            time.sleep(120)
    a.kernels_output(meta["kernel"], path=str(out))
    print("output in", out)
    for c in meta["candidates"]:
        n = len(list((out / "results" / c / "patches").glob("*.patch")))
        print(f"  {c}: {n} patches")


HARNESS_FILES = {"conftest.py", "pytest.ini"}


def strip_harness_files(patch: str) -> str:
    # on mined repos swegemma drops a "hermetic test discovery hook" (root conftest.py + pytest.ini) into
    # the workspace, and it lands in every kernel patch as two new files. our verify sandbox writes the same
    # pair first, so git apply refuses the whole patch. the scorer resets test/config files before its
    # hidden tests anyway, so dropping root conftest.py / pytest.ini diffs is what it would do too
    parts, cur = [], []
    for ln in patch.splitlines(keepends=True):
        if ln.startswith("diff --git ") and cur:
            parts.append(cur)
            cur = []
        cur.append(ln)
    if cur:
        parts.append(cur)
    keep = [p for p in parts if not (p[0].startswith("diff --git ") and p[0].split()[2][2:] in HARNESS_FILES)]
    return "".join("".join(p) for p in keep)


def grade(args) -> None:
    meta = run_meta(args.run)
    rd = RUNS / args.run
    for c in meta["candidates"]:
        raw = rd / "kaggle_out" / "results" / c / "patches"
        pdir = rd / "patches_clean" / c
        pdir.mkdir(parents=True, exist_ok=True)
        stripped = 0
        for p in raw.glob("*.patch"):
            text = p.read_text(encoding="utf-8", errors="surrogateescape")
            clean = strip_harness_files(text)
            stripped += clean != text
            if clean.strip():
                (pdir / p.name).write_text(clean, encoding="utf-8", errors="surrogateescape")
            else:
                (pdir / p.name).unlink(missing_ok=True)  # nothing left but harness files: same as no patch
        print(f"{c}: dropped harness conftest.py/pytest.ini from {stripped} of {len(list(raw.glob('*.patch')))} patches")
        cmd = ["wsl", "-d", "Ubuntu", "--", "bash", "/mnt/d/gemma4-agent/tools/wsl.sh",
               "python", "tools/gold_check.py", "--mode", "patches", "--isolated",
               "--concurrency", str(args.concurrency),
               "--patches-dir", wsl_path(pdir), "--out", wsl_path(rd / "graded" / c)]
        if meta.get("data"):
            # mined runs grade against our local copy of that set, not the dev tasks
            local = local_tasks(meta["data"])
            if local is None:
                raise SystemExit(f"no local folder with dataset-metadata.json id {meta['data']}")
            cmd += ["--data-dir", wsl_path(local.parent)]
        print("grading", c, flush=True)
        subprocess.run(cmd, check=False)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("push")
    p.add_argument("--run", required=True)
    p.add_argument("--candidates", nargs="+", required=True)
    p.add_argument("--tasks", required=True, help="file with one instance_id per line")
    p.add_argument("--concurrency", type=int, default=4)
    p.add_argument("--time-scale", type=float, default=1.0,
                   help="stretch max_time_minutes by this, to offset the per-stream slowdown from concurrency")
    p.add_argument("--slot", default="g4-gate", help="kernel slug; one run per slot at a time")
    p.add_argument("--data", help="dataset slug with a mined task set (e.g. goth55/g4-heldout100); default: dev set")
    p.add_argument("--adapters-data", help="dataset slug holding <adapter name>/adapter_model.safetensors for candidates with LoRAs")
    p.add_argument("--model", default="gemma-4-31b-it-qat-w4a16-ct",
                   help="base model: the kaggle model slug under google/gemma-4/Other and the alias the server registers (e.g. gemma-4-e4b-it-qat-w4a16-ct)")
    p.add_argument("--docker-image", default=DOCKER_IMAGE, help="kernel image to pin ('' takes kaggle's latest)")
    for name in ("status", "fetch", "grade"):
        q = sub.add_parser(name)
        q.add_argument("--run", required=True)
        if name == "fetch":
            q.add_argument("--wait", action="store_true")
        if name == "grade":
            q.add_argument("--concurrency", type=int, default=8)
    args = ap.parse_args()
    {"push": push, "status": status, "fetch": fetch, "grade": grade}[args.cmd](args)


if __name__ == "__main__":
    main()
