"""Render one SFT example through the real gemma 4 chat template and show the first model turns."""
import json
import sys
from pathlib import Path

from jinja2.sandbox import ImmutableSandboxedEnvironment

REPO = Path(__file__).resolve().parents[2]
tpl = ImmutableSandboxedEnvironment(trim_blocks=True, lstrip_blocks=True).from_string(
    (REPO / "notes" / "gemma4_chat_template.jinja").read_text())
ex = json.loads(open(sys.argv[1], encoding="utf-8").readline())
text = tpl.render(messages=ex["messages"], tools=ex["tools"], bos_token="<bos>",
                  add_generation_prompt=False, enable_thinking=True)
start = text.find("<|turn>model")
print(len(text), "chars total. first model turns:\n")
print(text[start:start + 1600])
