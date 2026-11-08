# g4 probe: boot the scorer's vLLM config on L4x4 and measure how fast the 31B actually is.
# answers "how many dev tasks fit in a GPU-hour" before we build the real gate runner.
# writes /kaggle/working/probe.json and prints everything as it goes.

import glob
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

T0 = time.time()
OUT = {}


def log(*a):
    print(f"[{time.time() - T0:7.1f}s]", *a, flush=True)


def sh(cmd):
    return subprocess.run(cmd, shell=True, capture_output=True, text=True).stdout.strip()


def find_dir(pattern):
    hits = sorted(glob.glob(pattern, recursive=True))
    return Path(hits[0]) if hits else None


OUT["gpus"] = sh("nvidia-smi --query-gpu=name,memory.total --format=csv,noheader")
OUT["cpus"] = os.cpu_count()
OUT["mem"] = sh("free -g | sed -n 2p")
OUT["python"] = sys.version.split()[0]
OUT["disk"] = sh("df -h /kaggle/working | tail -1")
log(json.dumps(OUT, indent=1))
log("inputs:", sh("ls /kaggle/input; ls /kaggle/input/* | head -30"))

# --- wheelhouse install, same dance as the host's getting-started notebook ---
os.environ.update({
    "LITELLM_LOCAL_MODEL_COST_MAP": "True", "TRANSFORMERS_NO_TF": "1",
    "VLLM_WORKER_MULTIPROC_METHOD": "spawn", "VLLM_MEMORY_PROFILER_ESTIMATE_CUDAGRAPHS": "1",
    "VLLM_ENGINE_READY_TIMEOUT_S": "1200", "VLLM_NO_USAGE_STATS": "1", "OTEL_SDK_DISABLED": "true",
    "PYTORCH_CUDA_ALLOC_CONF": "expandable_segments:True",
})
WH = find_dir("/kaggle/input/**/gemma-4-developer-agent-wheelhouse")
MODEL = find_dir("/kaggle/input/**/gemma-4-31b-it-qat-w4a16-ct/*") or find_dir("/kaggle/input/models/**/2")
log("wheelhouse:", WH, "model:", MODEL)
for pth in glob.glob("/usr/local/lib/python*/*-packages/*cutlass*.pth"):
    try:
        os.unlink(pth)
    except OSError:
        pass
tmp_whl = Path("/tmp/wheelhouse")
tmp_whl.mkdir(exist_ok=True)
for w in WH.glob("*.whl"):
    if "cutlass" in w.name.lower():
        continue
    name = w.name.replace("cu128", "+cu128") if ("cu128" in w.name and "+" not in w.name) else w.name
    if not (tmp_whl / name).exists():
        os.symlink(w, tmp_whl / name)
t = time.time()
subprocess.run([sys.executable, "-m", "pip", "install", "-q", "--no-deps", "--force-reinstall",
                *sorted(str(w) for w in tmp_whl.glob("*.whl"))], check=True)
OUT["wheel_install_s"] = round(time.time() - t, 1)
log("wheelhouse installed in", OUT["wheel_install_s"], "s")

# --- the scorer's server config (HARNESS_README section 3.1) ---
import requests  # noqa: E402
import torch  # noqa: E402
from adk_submission import VllmConfig, VllmServer  # noqa: E402

gpu_count = torch.cuda.device_count()
OUT["gpu_count"] = gpu_count
cfg = VllmConfig(
    model=str(MODEL), port=8000, host="127.0.0.1",
    tool_call_parser="gemma4", reasoning_parser="gemma4", max_model_len=32768,
    dtype="bfloat16", gpu_memory_utilization=0.80, enable_auto_tool_choice=True,
    enable_lora=True, max_loras=8, max_lora_rank=128,
    tensor_parallel_size=4 if gpu_count >= 4 else gpu_count, startup_timeout=60 * 20,
)
t = time.time()
srv = VllmServer(cfg, adapter_manifest=None)
srv.start()
OUT["vllm_boot_s"] = round(time.time() - t, 1)
log("vllm up in", OUT["vllm_boot_s"], "s at", srv.base_url)
served = requests.get(f"{srv.base_url}/models").json()["data"][0]["id"]
OUT["served_model"] = served

PROMPT = ("You are fixing a bug in a Python web framework. Explain step by step how you would "
          "locate the root cause of a 422 error raised for a valid request body, then write the fix. ")


def one(max_tokens, results, i):
    t = time.time()
    r = requests.post(f"{srv.base_url}/chat/completions", json={
        "model": served, "max_tokens": max_tokens, "temperature": 0.2,
        "messages": [{"role": "user", "content": PROMPT * 6}],
        "chat_template_kwargs": {"enable_thinking": False},
    }, timeout=1200).json()
    u = r.get("usage", {})
    results[i] = {"s": time.time() - t, "prompt": u.get("prompt_tokens"), "out": u.get("completion_tokens")}


one(64, {}, 0)  # warmup
for conc in (1, 4, 8, 16):
    res = {}
    t = time.time()
    th = [threading.Thread(target=one, args=(768, res, i)) for i in range(conc)]
    [x.start() for x in th]
    [x.join() for x in th]
    wall = time.time() - t
    out_tok = sum(v["out"] or 0 for v in res.values())
    OUT[f"conc{conc}"] = {
        "wall_s": round(wall, 1),
        "agg_tok_s": round(out_tok / wall, 1),
        "per_stream_tok_s": round(sum((v["out"] or 0) / v["s"] for v in res.values()) / conc, 1),
        "prompt_tokens": res[0]["prompt"],
    }
    log(f"concurrency {conc}:", OUT[f"conc{conc}"])

OUT["total_s"] = round(time.time() - T0, 1)
Path("/kaggle/working/probe.json").write_text(json.dumps(OUT, indent=2))
log("done", json.dumps(OUT, indent=2))
srv.stop()
