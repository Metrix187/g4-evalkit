"""Check candidate submissions the way the scorer will, before they cost a kaggle run.

    python tools/validate.py submissions/base submissions/v1 ...

runs the structural checks (validate_directory, single base model, file types, size) and then
actually compiles the agent tree with stub tools and a dummy model endpoint, so a YAML typo or a
bad generate_content_config field fails here instead of 40 minutes into a GPU session.
run inside WSL with ~/g4env (tools/wsl.sh python tools/validate.py ...).
"""

from __future__ import annotations

import sys
from pathlib import Path

from adk_submission import ModelRegistry, compile_submission, discover_adapters
from adk_submission.discovery import discover_declared_models, validate_directory
from google.adk.models.lite_llm import LiteLlm
from swegemma.config import ALLOWED_ADAPTER_EXTENSIONS, ALLOWED_SUBMISSION_EXTENSIONS, build_submission_limits


def validate_single_declared_model(agent_dir):
    # swegemma 0.2.11 dropped its own copy of this, adk_submission does the discovery now
    found = discover_declared_models(agent_dir)
    if len(found) != 1:
        raise ValueError(f"needs exactly one declared model, found {sorted(found)}")
    return next(iter(found))

TOOLS = ["run_command", "read_file", "edit_file", "write_file", "get_status", "submit_patch",
         "get_code_neighbors", "search_similar_code", "get_code_subgraph"]


def stub(name):
    def f(**kwargs) -> str:
        return "{}"
    f.__name__ = name
    return f


def check(d: Path) -> bool:
    limits, gen = build_submission_limits()
    try:
        validate_directory(d, limits)
        model = validate_single_declared_model(d)  # whatever the bundle declares (31b, e4b, ...) is what we register
        bad = [p for p in d.rglob("*") if p.is_file() and p.suffix.lower() not in ALLOWED_SUBMISSION_EXTENSIONS]
        if bad:
            raise ValueError(f"disallowed files: {[str(b.relative_to(d)) for b in bad]}")
        size = sum(p.stat().st_size for p in d.rglob("*") if p.is_file())
        models = ModelRegistry()
        models.register(model, LiteLlm(model=f"openai/{model}", api_base="http://127.0.0.1:9/v1", api_key="x"))
        adapters = discover_adapters(str(d), adapter_extensions=ALLOWED_ADAPTER_EXTENSIONS)
        agent = compile_submission(d, {t: stub(t) for t in TOOLS}, models, limits=limits,
                                   generation_constraints=gen, adapter_manifest=adapters)
        n_agents = 1 + len(getattr(agent, "sub_agents", []) or [])
        print(f"ok    {d.name:<20} {size / 1e6:8.2f} MB  root={agent.name} sub_agents={n_agents - 1}")
        return True
    except Exception as e:  # report every candidate, don't stop at the first broken one
        print(f"FAIL  {d.name:<20} {type(e).__name__}: {str(e)[:300]}")
        return False


if __name__ == "__main__":
    results = [check(Path(p)) for p in sys.argv[1:]]
    sys.exit(0 if all(results) else 1)
