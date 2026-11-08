"""Are the comp's 256-d node embeddings CodeT5+ 110M? Embed a few node texts and compare.

    tools/wsl.sh python tools/mine/embed_probe.py

cosine ~1.0 on every node = same model and input, so mined graphs can get identical embeddings.
anything well below that = different model or different input text, and we fall back to our own.
"""

import glob
import json
import random

import numpy as np
import torch
from transformers import AutoModel, AutoTokenizer

CKPT = "Salesforce/codet5p-110m-embedding"
tok = AutoTokenizer.from_pretrained(CKPT, trust_remote_code=True)
model = AutoModel.from_pretrained(CKPT, trust_remote_code=True).eval()

f = sorted(glob.glob("data/raw/comp/graphs/requests_*.json"))[0]
g = json.load(open(f, encoding="utf-8"))
emb = np.load(f.replace("graphs", "embeddings").replace(".json", ".npz"))
random.seed(0)
nodes = random.sample(g["nodes"], 8)
for variant in ("text", "name", "id+text"):
    sims = []
    for n in nodes:
        src = {"text": n["text"], "name": n["name"], "id+text": n["id"] + "\n" + n["text"]}[variant]
        ids = tok(src, return_tensors="pt", truncation=True, max_length=512)
        with torch.no_grad():
            v = model(**ids)[0].numpy().ravel()
        ref = emb[n["id"]]
        sims.append(float(v @ ref / (np.linalg.norm(v) * np.linalg.norm(ref) + 1e-9)))
    print(f"{variant:>8}: cosine {np.round(sims, 3).tolist()}  ref norm {np.linalg.norm(ref):.3f}")
