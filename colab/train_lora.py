# %% [markdown]
# # g4 LoRA pilot: self-distillation on successful dev sessions
#
# Runtime: **A100** (Runtime > Change runtime type; the 80 GB one if offered). Then Runtime > Run all.
#
# No Drive needed. Uses two Colab secrets with notebook access on, `KAGGLE_USERNAME` and `KAGGLE_KEY`:
# - pulls the training sessions + chat template from the private dataset `goth55/g4-sft`,
# - downloads the QAT weights from Kaggle (BASE = "qat"),
# - pushes the finished adapter to the private dataset `goth55/g4-adapters`, as `<RUN_NAME>/`, which is
#   where the gate (`kaggle_gate.py --adapters-data`) and the packaging step pick it up.
#
# Trains only on what the model wrote (thoughts + tool calls); tool results and prompts are masked.

# %%
RUN_NAME = "lora_dev_r16_qat"
MAX_LEN = 16384        # longer sessions get cut after the last tool call that fits (the scorer compacts at ~14k anyway)
MIN_LEN = 3000         # ...and dropped if that leaves less than this
RANK = 16
EPOCHS = 2
LR = 1e-4
GRAD_ACCUM = 8
# "qat": the bf16 weights of the QAT model the scorer quantizes to w4a16 (same weights, so the LoRA
# learns against what it'll actually be served on). "unsloth": unsloth's 4-bit copy of the original
# release, quicker to fetch but a different checkpoint
BASE = "qat"
QAT_HANDLE = "google/gemma-4/transformers/gemma-4-31b-it-qat-q4_0-unquantized/2"

# %%
!pip -q install unsloth
!pip -q install -U kagglehub
import os
from google.colab import userdata
os.environ["KAGGLE_USERNAME"] = userdata.get("KAGGLE_USERNAME")
key = userdata.get("KAGGLE_KEY").strip()
# new-style kaggle tokens (KGAT_..., what ~/.kaggle/access_token holds) go in KAGGLE_API_TOKEN;
# old 32-hex keys go in KAGGLE_KEY
os.environ["KAGGLE_API_TOKEN" if key.startswith("KGAT_") else "KAGGLE_KEY"] = key
print("kaggle auth:", "api token" if key.startswith("KGAT_") else "legacy key", "for", os.environ["KAGGLE_USERNAME"])
import kagglehub
SFT = kagglehub.dataset_download("goth55/g4-sft")
print("sft data:", SFT, os.listdir(SFT))

# %%
import json, re, math
from pathlib import Path
import torch
from jinja2.sandbox import ImmutableSandboxedEnvironment

G4 = Path(SFT)
OUT_ROOT = Path("/content/out")
OUT = OUT_ROOT / RUN_NAME
OUT.mkdir(parents=True, exist_ok=True)
gpu_gb = torch.cuda.get_device_properties(0).total_memory / 1e9
print(torch.cuda.get_device_name(0), round(gpu_gb), "GB")
if gpu_gb < 60 and MAX_LEN > 12288:
    MAX_LEN = 12288  # the 40 GB A100 can't hold 16k-token sessions on a 31B even in 4-bit
    print("40 GB card: MAX_LEN ->", MAX_LEN)

# %%
from unsloth import FastModel

if BASE == "qat":
    base_path = kagglehub.model_download(QAT_HANDLE)  # ~62 GB of bf16 shards, quantized to 4-bit on load
else:
    base_path = "unsloth/gemma-4-31B-it-unsloth-bnb-4bit"
print("base:", base_path)

model, tokenizer = FastModel.from_pretrained(
    base_path,
    max_seq_length=MAX_LEN,
    load_in_4bit=True,
    full_finetuning=False,
)
model = FastModel.get_peft_model(
    model,
    finetune_vision_layers=False,
    finetune_language_layers=True,
    finetune_attention_modules=True,
    finetune_mlp_modules=True,
    r=RANK, lora_alpha=RANK, lora_dropout=0, bias="none", random_state=3407,
)
tok = getattr(tokenizer, "tokenizer", tokenizer)

# %% [markdown]
# Render with the exact template the scorer's vLLM uses, then mask everything the model didn't
# write: system/user turns and the `<|tool_response>...<tool_response|>` blocks that sit inside
# model turns.

# %%
tpl = ImmutableSandboxedEnvironment(trim_blocks=True, lstrip_blocks=True).from_string(
    (G4 / "gemma4_chat_template.jinja").read_text())

MODEL_TURN = re.compile(r"<\|turn>model\n(.*?)(?:<turn\|>|$)", re.S)
TOOL_RESP = re.compile(r"<\|tool_response>.*?<tool_response\|>", re.S)


def trainable_spans(text):
    spans = []
    for m in MODEL_TURN.finditer(text):
        a, b = m.start(1), m.end(0)  # include the closing <turn|> so the model learns to stop
        cur = a
        for r in TOOL_RESP.finditer(text, a, m.end(1)):
            if r.start() > cur:
                spans.append((cur, r.start()))
            cur = r.end()
        if b > cur:
            spans.append((cur, b))
    return spans


def encode(ex):
    text = tpl.render(messages=ex["messages"], tools=ex["tools"], bos_token="",
                      add_generation_prompt=False, enable_thinking=True)
    enc = tok(text, add_special_tokens=True, return_offsets_mapping=True)
    ids, offs = enc["input_ids"], enc["offset_mapping"]
    if len(ids) > MAX_LEN:
        # keep the longest prefix that ends right after one of the model's tool calls. a whole agent
        # session is a single model turn (tool responses sit inside it), so there's no turn end to
        # cut at; the spot before a tool response is where the model's own output stops
        limit = offs[MAX_LEN - 1][1]
        cuts = [m.start() for m in TOOL_RESP.finditer(text) if m.start() <= limit]
        if not cuts:
            return None
        text = text[:cuts[-1]]
        enc = tok(text, add_special_tokens=True, return_offsets_mapping=True)
        ids, offs = enc["input_ids"], enc["offset_mapping"]
        if len(ids) < MIN_LEN:
            return None
    spans = trainable_spans(text)
    labels = [-100] * len(ids)
    j = 0
    for i, (s, e) in enumerate(offs):
        while j < len(spans) and spans[j][1] <= s:
            j += 1
        if j < len(spans) and s >= spans[j][0] and e <= spans[j][1] and e > s:
            labels[i] = ids[i]
    return {"input_ids": ids, "labels": labels}


rows = [json.loads(l) for l in open(G4 / "sft.jsonl", encoding="utf-8")]
data = [x for x in (encode(r) for r in rows) if x]
n_train = sum(sum(1 for l in d["labels"] if l != -100) for d in data)
print(f"{len(data)}/{len(rows)} sessions kept (cut to <= {MAX_LEN} tokens); {n_train} trained tokens "
      f"({n_train / sum(len(d['input_ids']) for d in data):.0%} of all tokens)")

# %%
from datasets import Dataset
from transformers import DataCollatorForSeq2Seq, Trainer, TrainingArguments

ds = Dataset.from_list(data).shuffle(seed=3407)
args = TrainingArguments(
    output_dir="/content/ckpt", per_device_train_batch_size=1, gradient_accumulation_steps=GRAD_ACCUM,
    num_train_epochs=EPOCHS, learning_rate=LR, lr_scheduler_type="cosine", warmup_ratio=0.05,
    bf16=True, optim="adamw_8bit", logging_steps=1, save_strategy="no", report_to="none",
    gradient_checkpointing=True,
)
trainer = Trainer(model=model, args=args, train_dataset=ds,
                  data_collator=DataCollatorForSeq2Seq(tok, padding=True, label_pad_token_id=-100))
stats = trainer.train()
print(stats)

# %% [markdown]
# Save in the scorer's layout: `adapters/<name>/{adapter_config.json, adapter_model.safetensors}`.
# The sample submission's adapter keys look like
# `base_model.model.model.language_model.layers.0.self_attn.q_proj.lora_A.weight`.

# %%
model.save_pretrained(str(OUT), safe_serialization=True)
from safetensors import safe_open
with safe_open(str(OUT / "adapter_model.safetensors"), "pt") as f:
    keys = list(f.keys())
print(len(keys), "tensors, e.g.", keys[:2])
assert all("language_model" in k for k in keys), "adapter touches non-language modules"
cfg = json.loads((OUT / "adapter_config.json").read_text())
cfg["base_model_name_or_path"] = "google/gemma-4-31b-it-qat-w4a16-ct"
# unsloth may write target_modules as a regex string; the scorer's vLLM wants a plain list like the
# sample adapter's. the actual modules come from the tensor names, so list exactly those
cfg["target_modules"] = sorted({k.split(".lora_")[0].rsplit(".", 1)[-1] for k in keys})
(OUT / "adapter_config.json").write_text(json.dumps(cfg, indent=2))
size = sum(p.stat().st_size for p in OUT.iterdir())
print(f"saved to {OUT}: {size / 1e6:.0f} MB, r={cfg['r']}, targets={cfg['target_modules']}")
(OUT / "train_log.json").write_text(json.dumps({"base": BASE, "sessions": len(data), "trained_tokens": n_train,
    "log": trainer.state.log_history}, indent=1))

# %% [markdown]
# Push the adapter to Kaggle. The dataset keeps every run as its own folder, and the gate finds an
# adapter by folder name + sha256, so older runs can stay.

# %%
import hashlib
up = Path("/content/adapters_up")
up.mkdir(exist_ok=True)
import shutil
shutil.copytree(OUT, up / RUN_NAME, dirs_exist_ok=True)
sha = hashlib.sha256((OUT / "adapter_model.safetensors").read_bytes()).hexdigest()
kagglehub.dataset_upload("goth55/g4-adapters", str(up), version_notes=f"{RUN_NAME} sha256 {sha[:12]}")
print("uploaded", RUN_NAME, "sha256", sha)
