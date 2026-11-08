"""Submit a candidate the way this comp wants it: a packaging notebook that writes submission.zip.

    python tools/kaggle_submit.py submissions/think1k --message "think1k, gate r001-r003 winner"         # dry run
    python tools/kaggle_submit.py submissions/think1k --message "..." --yes                               # for real

builds results/packages/<cand>.zip (tools/package.py), embeds it in a tiny CPU kernel that just writes
/kaggle/working/submission.zip, pushes and waits for it, then submits that kernel version.

**one submission a day, and sky okays every one in chat (AGENTS.md).** without --yes this stops after
the dry run. also check the forum first: scoring broke after the 2026-09-30 wheelhouse update, and a
failed run still eats the day's slot.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import subprocess
import sys
import time
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
USER = "goth55"
COMP = "gemma-4-developer-agent"
SLUG = "g4-pack"

KERNEL = '''# writes submission.zip; nothing else. built by tools/kaggle_submit.py
# the small files ride inline. lora weights are way past kaggle's 1 MB kernel source cap, so they come
# from a private dataset (--adapters-data) and have to match the sha256 we packed
import base64, glob, hashlib, io, os, zipfile
from pathlib import Path
blob = base64.b64decode("__ZIP_B64__")
weights = __WEIGHTS__
src_root = os.environ.get("G4_INPUT", "/kaggle/input")
out = Path(os.environ.get("G4_OUT", "/kaggle/working/submission.zip"))


def sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 24), b""):
            h.update(chunk)
    return h.hexdigest()


with zipfile.ZipFile(io.BytesIO(blob)) as small, zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
    for info in small.infolist():
        z.writestr(info, small.read(info))
    for arc, want in weights.items():
        name = arc.split("/")[-2]
        hits = sorted(glob.glob(f"{src_root}/**/{name}/adapter_model.safetensors", recursive=True))
        got = next((h for h in hits if sha(h) == want), None)
        if got is None:
            raise SystemExit(f"{arc}: no file with sha256 {want[:12]} under {src_root} ({len(hits)} candidates)")
        z.write(got, arc, compress_type=zipfile.ZIP_STORED)  # float weights barely compress anyway
        print("added", arc, "from", got)
print("wrote", out, out.stat().st_size, "bytes, sha256", sha(out))
'''


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("candidate")
    ap.add_argument("--message", required=True)
    ap.add_argument("--yes", action="store_true", help="actually push and submit (needs sky's ok)")
    ap.add_argument("--adapters-data", help="private dataset holding the candidate's adapter weights (e.g. goth55/g4-adapters)")
    args = ap.parse_args()

    cand = Path(args.candidate)
    subprocess.run([sys.executable, str(REPO / "tools" / "package.py"), str(cand)], check=True)
    zp = REPO / "results" / "packages" / f"{cand.name}.zip"
    # package.py checked the full candidate. the kernel gets everything but the weights inline, plus
    # the hash of each weight file it has to fetch from --adapters-data
    weights = {}
    small = io.BytesIO()
    with zipfile.ZipFile(zp) as full, zipfile.ZipFile(small, "w", zipfile.ZIP_DEFLATED) as z:
        for info in full.infolist():
            if info.filename.endswith(".safetensors"):
                weights[info.filename] = hashlib.sha256(full.read(info)).hexdigest()
            else:
                z.writestr(info, full.read(info))
    if weights and not args.adapters_data:
        raise SystemExit("this candidate ships adapter weights: upload them (tools/upload_adapters.py) and pass --adapters-data")
    kd = REPO / "kaggle" / "pack"
    kd.mkdir(parents=True, exist_ok=True)
    (kd / "pack.py").write_text(KERNEL.replace("__ZIP_B64__", base64.b64encode(small.getvalue()).decode())
                                .replace("__WEIGHTS__", json.dumps(weights)))
    if (kd / "pack.py").stat().st_size > 1_000_000:
        raise SystemExit("pack.py is over kaggle's 1 MB kernel source cap")
    (kd / "kernel-metadata.json").write_text(json.dumps({
        "id": f"{USER}/{SLUG}", "title": SLUG, "code_file": "pack.py", "language": "python",
        "kernel_type": "script", "is_private": True, "enable_gpu": False, "enable_tpu": False,
        "enable_internet": False, "keywords": [], "dataset_sources": [args.adapters_data] if weights else [],
        "kernel_sources": [], "competition_sources": [COMP], "model_sources": [],
    }, indent=2))
    for arc, digest in weights.items():
        print(f"  {arc} comes from {args.adapters_data}, sha256 {digest[:16]}")
    print(f"packaging kernel ready in {kd} for {cand.name}: {args.message!r}")
    if not args.yes:
        print("dry run. rerun with --yes once sky has okayed this submission and scoring is healthy")
        return

    from kaggle.api.kaggle_api_extended import KaggleApi
    api = KaggleApi()
    api.authenticate()
    r = api.kernels_push(str(kd))
    version = getattr(r, "version_number", None)
    print("pushed", getattr(r, "url", r), "version", version)
    while True:
        s = str(getattr(api.kernels_status(f"{USER}/{SLUG}"), "status", ""))
        print(time.strftime("%H:%M:%S"), s, flush=True)
        if any(x in s for x in ("COMPLETE", "ERROR", "CANCEL")):
            break
        time.sleep(20)
    if "COMPLETE" not in s:
        raise SystemExit("packaging kernel didn't complete, not submitting")
    # same interpreter as the api above, so a venv that isn't on PATH still finds its kaggle cli
    res = subprocess.run([sys.executable, "-m", "kaggle", "competitions", "submit", COMP, "-f", "submission.zip",
                          "-k", f"{USER}/{SLUG}", "-v", str(version), "-m", args.message],
                         capture_output=True, text=True)
    print(res.stdout, res.stderr)


if __name__ == "__main__":
    main()
