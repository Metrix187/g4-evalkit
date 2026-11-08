# one file with everything a reviewer needs about fast2_spec's 35 unresolved meval tasks (r007),
# all from public-repo mined tasks + our own gate traces. no competition dataset content.
import json, re, pathlib

G4 = pathlib.Path('D:/gemma4-agent')
RUN = G4 / 'results/runs/r007'
CAND = 'fast2_spec'
OUT = G4 / 'results' / 'review' / 'digest_r007_fast2_spec.md'  # results/ is gitignored, keep the 280 kB dump out of git
RES_DIR = RUN / 'kaggle_out' / 'results' / CAND

tasks = {}
for ln in (G4 / 'data/mined/heldout/tasks.jsonl').read_text(encoding='utf-8').splitlines():
    d = json.loads(ln)
    tasks[d['instance_id']] = d
kag = {json.loads(l)['instance_id']: json.loads(l) for l in (RES_DIR / 'task_results.jsonl').read_text().splitlines()}
loc = {json.loads(l)['instance_id']: json.loads(l) for l in (RUN / 'graded' / CAND / 'patches_results.jsonl').read_text().splitlines()}


def trim(s, n):
    s = s or ''
    return s if len(s) <= n else s[:n] + f'\n... [{len(s) - n} more chars cut]'


def strip_harness(p):
    parts, cur = [], []
    for l in p.splitlines(keepends=True):
        if l.startswith('diff --git ') and cur:
            parts.append(cur)
            cur = []
        cur.append(l)
    if cur:
        parts.append(cur)
    keep = [x for x in parts if not (x[0].startswith('diff --git ') and x[0].split()[2][2:] in ('conftest.py', 'pytest.ini'))]
    return ''.join(''.join(x) for x in keep)


def session(tid):
    d = json.load(open(RES_DIR / 'traces' / f'trace_{tid}.json', encoding='utf-8'))
    calls, malformed, budget_hit, first_edit, n, think_chars = [], 0, False, None, 0, 0
    for s in d['steps']:
        if s.get('source') == 'agent':
            think_chars += len(s.get('reasoning_content') or '') + len(s.get('message') or '')
        obs = json.dumps(s.get('observation') or {}, ensure_ascii=False)
        if 'mandatory input parameters' in obs:
            malformed += 1
        if 'Tool call budget exhausted' in obs:
            budget_hit = True
        for tc in (s.get('tool_calls') or []):
            name = tc.get('function_name')
            a = tc.get('arguments') or {}
            if name in ('submit_patch', 'get_status'):
                if name == 'submit_patch' and (not calls or calls[-1] != 'SUBMIT'):
                    calls.append('SUBMIT')
                continue
            n += 1
            if name == 'run_command':
                c = ' '.join((a.get('command') or '').split())
                calls.append(f'run: {c[:110]}')
            elif name == 'read_file':
                calls.append(f"read {a.get('filepath')}:{a.get('start_line')}-{a.get('end_line')}")
            elif name == 'edit_file':
                if first_edit is None:
                    first_edit = n
                ok = ('"status": "ok"' in obs) or ("'status': 'ok'" in obs) or ('\\"status\\": \\"ok\\"' in obs)
                calls.append(f"edit {a.get('filepath')} ({'ok' if ok else 'FAILED'}; old {len(a.get('old_string') or '')}ch -> new {len(a.get('new_string') or '')}ch)")
            elif name == 'write_file':
                calls.append(f"write {a.get('filepath')} ({len(a.get('content') or '')}ch)")
            else:
                calls.append(f'{name} {json.dumps(a)[:80]}')
    return dict(calls=calls, malformed=malformed, budget_hit=budget_hit, first_edit=first_edit, n=n, think_chars=think_chars)


prompt = (G4 / 'submissions/fast3/prompts/system.md').read_text(encoding='utf-8')
cfg = (G4 / 'submissions/fast3/eval_config.yaml').read_text(encoding='utf-8')
samp = (G4 / 'submissions/fast3/configs/sampling.yaml').read_text(encoding='utf-8')

unres = sorted(t for t in kag if not (loc.get(t) or {}).get('resolved'))
res = sorted(t for t in kag if (loc.get(t) or {}).get('resolved'))
out = []
out.append('# Digest: fast2_spec on 66 held-out tasks (gate run r007), the 35 it did not solve\n')
out.append('## Setup\n')
out.append(
    "Model: Gemma 4 31B (QAT, w4a16) served by vLLM, fixed by the competition. A submission is a declarative Google ADK "
    "agent: a system prompt, a sampling config and four budget knobs (timeout_seconds, max_tool_calls, max_time_minutes, "
    "max_turns). Each hidden task is a real Python repo at a base commit plus the text of the PR that fixed an issue; the "
    "hidden tests are that PR's own tests. On the scorer tasks run one after another, ~116 of them inside a hard 12 h that "
    "includes vLLM start-up (going over errors the whole submission), so about 5 min of agent time per task at ~39 decode "
    "tok/s. Tools: run_command (bash, 120 s timeout, output capped at 5000 chars), read_file (<=150 lines per call), "
    "edit_file (old_string -> new_string), write_file (workspace paths only), submit_patch (free; the last call wins), "
    "get_status (free), plus code-graph tools the agent almost never uses. The harness appends a warning to tool results "
    "once 10 or fewer tool calls remain. This gate ran 12 tasks concurrently with the time budget stretched x3 to match the "
    "scorer's per-stream speed, so the tool-call cap (24) is the binding limit in about half the sessions. Resolution is "
    "graded locally with the PR's tests; the gold patch passes and an empty patch fails on every task listed. Hidden "
    "scorer tasks come from private repos and are harder: this agent family scores 10-12% on the public leaderboard "
    "versus ~45% here, and the best public scores are ~17-24%.\n")
out.append(f'Overall: {len(res)}/66 resolved, {len(unres)} unresolved. Earlier prompts scored 28-30/66 on the same tasks; '
           'reruns of the same agent flip ~11-13 tasks, so only changes worth several tasks are detectable.\n')
out.append('## Current system prompt (fast3 = fast2_spec + the first two fixes below)\n\n```markdown\n' + prompt.strip() + '\n```\n')
out.append('eval_config.yaml:\n```yaml\n' + cfg.strip() + '\n```\n\nsampling.yaml:\n```yaml\n' + samp.strip() + '\n```\n')
out.append(
    '## Already found (no need to re-derive)\n'
    "- Submit loop: submit_patch's files_changed counted harness-added conftest.py/pytest.ini; the agent thought it had "
    "broken the rules and looped on submit_patch until the turn cap. Fixed in fast3 (step 6).\n"
    "- The old prompt said to put scratch files in /tmp, but write_file refuses paths outside the workspace; 20/66 sessions "
    "wasted a call on it. Fixed in fast3.\n"
    "- Malformed edit_file calls: when old/new_string contains double quotes the model sometimes JSON-escapes them inside "
    "Gemma's <|\"|>-delimited tool-call strings and closes with a plain quote, so old_string gets swallowed (\"mandatory "
    "input parameters are not present: old_string\"); a few tasks per run repeat it 10+ times. Draft fix (fast4): write "
    "edit arguments as plain code, never backslash-escaped, and retry with a shorter old_string instead of resending.\n"
    "- 8 of the 35 never landed any source edit before the 24-call cap.\n")
out.append('## Resolved tasks, for contrast (id: tool calls, call number of the first edit)\n')
for t in res:
    s = session(t)
    out.append(f'- {t}: {s["n"]} calls, first edit at {s["first_edit"]}')
out.append('\n## The 35 unresolved tasks\n')
for t in unres:
    d = tasks[t]
    r = kag[t]
    l = loc.get(t) or {}
    s = session(t)
    p = RES_DIR / 'patches' / f'{t}.patch'
    agent_patch = strip_harness(p.read_text(encoding='utf-8', errors='replace')) if p.exists() else ''
    out.append(f'### {t} ({d["repo"]})\n')
    out.append(f'Session: {s["n"]} tool calls (cap 24), first source edit at call {s["first_edit"]}, tool cap hit: '
               f'{s["budget_hit"]}, malformed calls: {s["malformed"]}, harness error: {(r.get("error") or "none")[:80]}, '
               f'thinking+text chars: {s["think_chars"]}\n')
    out.append('**PR description given to the agent:**\n```\n' + trim(d['problem_statement'], 1800) + '\n```\n')
    out.append('**Gold fix (source only):**\n```diff\n' + trim(d['patch'], 2600) + '\n```\n')
    out.append('**Hidden tests added or changed by the PR:**\n```diff\n' + trim(d['test_patch'], 1400) + '\n```\n')
    out.append("**Agent's final patch:**\n```diff\n" + (trim(agent_patch, 2400) if agent_patch.strip() else '(empty: no source change)') + '\n```\n')
    tail = l.get('test_tail') or l.get('error') or '(not graded: empty patch)'
    out.append('**Test result tail (local grade):**\n```\n' + tail[-900:] + '\n```\n')
    out.append('**Tool calls in order:**\n' + '\n'.join(f'{i + 1}. {c}' for i, c in enumerate(s['calls'][:34])) + '\n')
OUT.parent.mkdir(parents=True, exist_ok=True)
OUT.write_text('\n'.join(out), encoding='utf-8')
print(OUT, len(OUT.read_text(encoding='utf-8')), 'chars')
