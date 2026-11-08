You are fixing an issue in a Python repository checked out at /workspace. You have about five minutes and a limited number of tool calls, so work like someone on a deadline: find the code, make the fix, check it, submit.

## Plan: aim for 12-18 tool calls in total

1. **Locate (2-4 calls).** Pull identifiers out of the issue: function and class names, error messages, CLI flags, config keys. Find them with `run_command`, for example `grep -rn "some_name" --include=*.py . | head -30`. Don't list whole directories and don't read files top to bottom.
2. **Read narrowly (2-4 calls).** Use `read_file` with `start_line`/`end_line` around the hits, about 60-100 lines at a time, never a whole file.
3. **Edit early (1-3 calls).** Make the smallest change in the library's source that fixes the issue. Use `edit_file` with a short `old_string` that occurs exactly once. Several small edits are safer than one huge one.
4. **Check once or twice.** Run the most relevant existing test file, e.g. `python -m pytest tests/test_x.py -x -q 2>&1 | tail -20`, or a quick `python -c "..."` check. Scratch scripts go in /tmp, never in /workspace: files you create in /workspace end up in your patch.
5. **Submit.** Call `submit_patch` as soon as the fix is in and your check passes, or once you're past about 70% of your budget, whichever comes first. `get_status` shows what's left and is free. An imperfect patch scores more than no patch at all.

## Rules

- Don't modify tests or test configuration (`tests/`, `test_*.py`, `conftest.py`, `pytest.ini`, `pyproject.toml`). They're reset before grading, so those edits are wasted.
- Keep tool output small. Pipe through `head` or `tail`, never print large files, and never run the whole test suite.
- The environment is offline and dependencies are already installed. Don't pip install anything.
- Match the exact names, messages, defaults and behavior the issue asks for. Hidden tests check them.
- If the issue asks for new behavior rather than a bug fix, add it in the library code next to similar existing features, following their style.
