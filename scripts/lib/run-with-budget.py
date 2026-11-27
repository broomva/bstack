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

  - When this process is itself stopped (Ctrl-C, SIGTERM, a closed terminal), the command
    is stopped the same way before this exits.

Exit status: the command's own; 5 when the time ran out; 2 when SECONDS is not a finite
number above zero (nothing is run); 128 + the signal number when this process was stopped.
"""
import math
import os
import shutil
import signal
import subprocess
import sys
import tempfile

GRACE_S = 3.0


class Stopped(Exception):
    """This process was told to stop (Ctrl-C, SIGTERM, a closed terminal)."""


def stop_group(proc: subprocess.Popen) -> None:
    """SIGTERM to the command's whole process group, then SIGKILL. Both go to the group: a
    child that outlives a leader that stopped politely is still stopped."""
    for sig, wait in ((signal.SIGTERM, GRACE_S), (signal.SIGKILL, None)):
        try:
            os.killpg(proc.pid, sig)
        except ProcessLookupError:
            pass
        try:
            proc.wait(timeout=wait)
        except subprocess.TimeoutExpired:
            pass


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

    def stopped(signum: int, _frame: object) -> None:
        raise Stopped(signum)

    # The command is in a session of its own, so a Ctrl-C or a hang-up that reaches this
    # process does not reach it. Without these it would go on running, and writing, after
    # the caller believed it had stopped everything.
    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, stopped)
    try:
        scratch = tempfile.mkdtemp(prefix="bstack-rules-")
    except OSError:
        scratch = None  # no usable temporary directory: the command inherits the caller's
    env = dict(os.environ, TMPDIR=scratch) if scratch else None
    proc = None
    try:
        proc = subprocess.Popen(argv[2:], start_new_session=True, env=env)
        try:
            return proc.wait(timeout=budget)
        except subprocess.TimeoutExpired:
            stop_group(proc)
            print(f"the rule registry step did not finish within {budget:g} s and was stopped; "
                  "nothing is known about the registry.")
            return 5
    except Stopped as exc:
        return 128 + int(exc.args[0])
    finally:
        if proc is not None and proc.poll() is None:
            stop_group(proc)
        if scratch:
            shutil.rmtree(scratch, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
