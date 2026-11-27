#!/usr/bin/env python3
"""run-with-budget.py SECONDS COMMAND [ARG ...] — run a command for at most SECONDS.

scripts/rules-registry.sh runs itself under this when $BSTACK_RULES_BUDGET_S is set, so the
whole step is bounded: finding the skill, reading the workspace, and the skill's own run.

  - The command runs in its own session (a new process group). When the time is up the
    group gets SIGTERM, then SIGKILL three seconds later. SIGTERM first, because the skill
    removes a file it is in the middle of writing when asked to stop and cannot when killed.
    A process that moved itself to yet another session is outside the group and is not
    stopped.
  - The command gets a temporary directory of its own as $TMPDIR, removed when it ends
    however it ends. The rule runner builds scratch repositories there, and a stopped run
    would otherwise leave them behind.

Exit status: the command's own; 5 when the time ran out; 2 when SECONDS is not a finite
number above zero (nothing is run).
"""
import math
import os
import shutil
import signal
import subprocess
import sys
import tempfile

GRACE_S = 3.0


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print(__doc__, file=sys.stderr)
        return 2
    try:
        budget = float(argv[1])
    except ValueError:
        budget = math.nan
    if not (math.isfinite(budget) and budget > 0):
        print(f"rules-registry: BSTACK_RULES_BUDGET_S must be a finite number of seconds above 0, got {argv[1]!r}",
              file=sys.stderr)
        return 2
    try:
        scratch = tempfile.mkdtemp(prefix="bstack-rules-")
    except OSError:
        scratch = None  # no usable temporary directory: the command inherits the caller's
    env = dict(os.environ, TMPDIR=scratch) if scratch else None
    try:
        proc = subprocess.Popen(argv[2:], start_new_session=True, env=env)
        try:
            return proc.wait(timeout=budget)
        except subprocess.TimeoutExpired:
            for sig, wait in ((signal.SIGTERM, GRACE_S), (signal.SIGKILL, None)):
                # The whole group both times: a child that outlives a leader that stopped
                # politely is still stopped.
                try:
                    os.killpg(proc.pid, sig)
                except ProcessLookupError:
                    pass
                try:
                    proc.wait(timeout=wait)
                except subprocess.TimeoutExpired:
                    pass
            print(f"the rule registry step did not finish within {budget:g} s and was stopped; "
                  "nothing is known about the registry.")
            return 5
    finally:
        if scratch:
            shutil.rmtree(scratch, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
