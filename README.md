# g4-evalkit

Code behind the paper-track writeup *Grading you can trust, tasks you haven't seen* for Google's
[Gemma 4 Developer Agent](https://www.kaggle.com/competitions/gemma-4-developer-agent) competition:
a fixed local grader, the G4-Mine task miner, the Kaggle gate that compares agents on 4xL4, and the
analysis scripts the numbers in the paper come from.

- Writeup: https://www.kaggle.com/competitions/gemma-4-developer-agent-paper/writeups/grading-you-can-trust-tasks-you-havent-seen
- Dataset (345 verified held-out tasks, snapshots, graphs, embeddings): https://www.kaggle.com/datasets/goth55/g4-mined

This is a public snapshot of a working repo, not a polished library. Paths assume Windows + WSL2
(`tools/wsl.sh`), a `~/g4env` venv built by `tools/setup_wsl_env.sh`, and the competition's wheelhouse
unpacked under `data/raw/`. Competition data is not included and never was; get it from Kaggle after
accepting the rules.

## What's where

| path | what |
|---|---|
| `tools/sandbox.py` | `DockerLikeSubprocessManager`: the seven grader fixes from §2 of the paper (isolated base env, task wheels, per-commit resolver, lockfiles, extras, src-layout) |
| `tools/gold_check.py`, `tools/run_gold_all.sh`, `tools/regrade_everything.sh` | gold / empty checks over the dev set; what "gradeable" means |
| `tools/mine/` | G4-Mine: PR mining (`prs.py`), history-truncated snapshots and verification (`build.py`, `verify.sh`), AST call graphs in the harness schema (`graph.py`), CodeT5+ embeddings, problem-statement restatement (`restate.py`) |
| `tools/kaggle_gate.py`, `kaggle/gate_template.py` | phase 1 on a Kaggle 4xL4 batch kernel at concurrency 12 / time scale 3; phase 2 graded locally |
| `tools/compare.py`, `tools/miss_shape.py`, `tools/mech_counts.py`, `tools/run_budget.py`, `tools/stream_speed.py` | paired sign tests, per-task transition matrices, behaviour counts, runtime projection, per-stream throughput (the calibration in §4) |
| `tools/malformed_calls.py`, `tools/repeat_fails.py`, `tools/red_runs.py` | the §5.2 analysis: swallowed arguments, resend streaks, sandbox-side reds |
| `tools/graph_coverage.py`, `tools/graph_replay.py` | §3 coverage and the §5.4 CPU replay through the harness's own graph tools |
| `tools/sft/`, `colab/` | the self-distillation LoRA runs of §5.1 (rank 16, QAT bf16 base) |
| `submissions/` | every agent config we gated, one directory each; `fast4c` and `fast4h` are the final picks |
| `notes/runs.md` | every gate run and leaderboard submission with its numbers; `notes/paper_draft.md` is the writeup text |
| `notes/tasks_meval.txt`, `notes/tasks_mtrain*.txt` | the held-out blocks (G4-Mine task ids) |

The dev-set task lists and per-task gold-node tables stay out of this copy because they derive from
competition data.

## License

Apache-2.0 (the competition requires an OSI license). Mined tasks carry their source repositories'
licenses, listed per task in the dataset.

## Paper

`paper/paper.pdf` is the writeup as submitted to the paper track (built from `notes/paper_draft.md` with `tools/paper_build.py`).
