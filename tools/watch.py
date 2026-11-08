"""One 20-minute check on LB submissions and gate kernels: poll every 2 min, log kernel transitions,
quit early once a submission scores/errors or a kernel finishes/dies. run it in the background and
relaunch it when it exits; sky's cadence is a check every 20 min.

    python tools/watch.py --subs 56979327 --kernels goth55/g4-gate goth55/g4-gate2
    CYCLE=0 python tools/watch.py --subs 56979327        # just print the current state once

run with the kaggleulturie venv python (it has the kaggle package + creds). a gate kernel always ends
on ERROR, even when it worked (nbconvert can't import jinja2 after the run), so check its log for
"done" before believing it.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import os
import sys
import time

from kaggle.api.kaggle_api_extended import KaggleApi

COMP = "gemma-4-developer-agent"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--subs", nargs="*", type=int, default=[])
    ap.add_argument("--kernels", nargs="*", default=[])
    args = ap.parse_args()
    cycle = float(os.environ.get("CYCLE", 20 * 60))
    api = KaggleApi()
    api.authenticate()
    utc = lambda: time.strftime("%H:%M", time.gmtime())

    def poll_subs():
        if not args.subs:
            return {}
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                subs = api.competition_submissions(COMP)
        except Exception as e:  # one flaky poll shouldn't end the cycle
            print(utc(), "sub poll failed:", str(e)[:100], flush=True)
            return None
        out = {}
        for ref in args.subs:
            s = next((x for x in subs if int(x.ref) == ref), None)
            out[ref] = None if s is None else (str(s.status).replace("SubmissionStatus.", ""),
                                               getattr(s, "public_score", None), getattr(s, "error_description", None))
        return out

    def poll_kernels():
        out = {}
        for k in args.kernels:
            try:
                out[k] = str(getattr(api.kernels_status(k), "status", "?")).replace("KernelWorkerStatus.", "")
            except Exception as e:
                out[k] = None
                print(utc(), k, "poll failed:", str(e)[:100], flush=True)
        return out

    sub_done = lambda v: bool(v) and v[0] in ("COMPLETE", "ERROR")
    k_done = lambda v: v is not None and any(x in v for x in ("COMPLETE", "ERROR", "CANCEL"))

    s0, k0 = poll_subs() or {}, poll_kernels()
    print(utc(), "UTC start", s0, k0, flush=True)
    seen_subs = {r for r, v in s0.items() if sub_done(v)}
    seen_k = {k for k, v in k0.items() if k_done(v)}
    last_k = dict(k0)
    t0 = time.time()
    while time.time() - t0 < cycle:
        time.sleep(120)
        s, k = poll_subs(), poll_kernels()
        for name, v in k.items():
            if v is not None and v != last_k.get(name):
                print(utc(), "UTC", name, last_k.get(name), "->", v, flush=True)
                last_k[name] = v
        fresh_s = [r for r, v in (s or {}).items() if sub_done(v) and r not in seen_subs]
        fresh_k = [n for n, v in k.items() if k_done(v) and n not in seen_k]
        if fresh_s or fresh_k:
            print(utc(), "UTC DONE", {r: s[r] for r in fresh_s}, {n: k[n] for n in fresh_k}, "| all:", s, k, flush=True)
            sys.exit(0)
    print(utc(), "UTC end", poll_subs(), poll_kernels(), flush=True)


if __name__ == "__main__":
    main()
