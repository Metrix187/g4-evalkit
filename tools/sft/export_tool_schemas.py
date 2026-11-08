"""Dump the harness's tool declarations as OpenAI-style function schemas.

    tools/wsl.sh python tools/sft/export_tool_schemas.py      # -> notes/tool_schemas.json

the chat template needs the same tool list the model saw at inference, so we ask swegemma for
it instead of retyping signatures.
"""

import json
from pathlib import Path

from swegemma.context import SwegemmaContext

ctx = SwegemmaContext(docker_manager=None, container_id="", problem_statement="", hints_text="",
                      task=None, repo="x/y", graph_dir="", embeddings_dir="")
from google.adk.tools import FunctionTool  # noqa: E402

tools = ctx.create_tools()
schemas = []
for name, fn in (tools.items() if isinstance(tools, dict) else ((getattr(t, "name", ""), t) for t in tools)):
    tool = fn if hasattr(fn, "_get_declaration") else FunctionTool(fn)
    decl = tool._get_declaration()
    d = decl.model_dump(exclude_none=True) if hasattr(decl, "model_dump") else dict(decl)
    params = d.get("parameters") or {}
    schemas.append({"type": "function", "function": {
        "name": d["name"], "description": d.get("description", ""),
        "parameters": json.loads(json.dumps(params, default=str).replace('"OBJECT"', '"object"')
                                 .replace('"STRING"', '"string"').replace('"INTEGER"', '"integer"')
                                 .replace('"BOOLEAN"', '"boolean"').replace('"ARRAY"', '"array"')
                                 .replace('"NUMBER"', '"number"')),
    }})
out = Path(__file__).resolve().parents[2] / "notes" / "tool_schemas.json"
out.write_text(json.dumps(schemas, indent=1))
print(f"{len(schemas)} tools -> {out}:", [s["function"]["name"] for s in schemas])
