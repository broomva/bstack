#!/usr/bin/env python3
"""bg-task-stop-guard.py — Stop hook: under Paseo, refuse a turn-end while this
session's own background task is still in flight (BRO-2815).

The defect: a Paseo session ends its turn on "I'll wait for the background
reviewer / CI watch / verify step" and goes idle. Its own task finishes, the
notification never re-invokes the idle session, and the arc sits until a
coordinator nudges it by hand. Recorded at least 9 times 2026-10-03..10-09, four
of them on 10-09 under briefs that said "wait in the foreground". Prose in a brief
did not hold, so this is a mechanism.

What it reads (measured on Claude Code 2.1.295, payloads in
tests/fixtures/bg-task-stop-guard/): the Stop input carries `background_tasks`,
"in-flight background work (running/pending + backgrounded) registered in this
session", one entry per task with a friendly `type` label. The label map in the
2.1.295 binary is:

    local_bash -> shell        local_agent -> subagent   local_workflow -> workflow
    monitor_mcp/monitor_ws -> monitor   mcp_task -> MCP task   remote_agent -> cloud session
    in_process_teammate -> teammate     dream -> dream         auto_mode_scan -> auto-mode scan
    local_memory_import -> memory import

A Monitor tool watch shows up as `shell`. Only the first row (task types a session
launches) counts. The second row is harness-internal work a session never waits on.
An unknown label falls back to the raw discriminant and is NOT counted: a guard
that blocks on a type it cannot name would fight the operator after an upgrade.

Decision (fail-open: any parse or I/O error allows the stop):

  PASEO_AGENT_ID unset/empty, or BSTACK_BG_TASK_GUARD=0 -> allow
  background_tasks absent (older Claude Code)          -> allow (the coordinator tick is the backstop)
  no counted task in flight                            -> allow
  every in-flight task was already blocked on once     -> allow (CAP: one block per task)
  this session already blocked LIFE_MAX times          -> allow (CAP: lifetime)
  otherwise                                            -> BLOCK, exit 2, reason on stderr

"Paseo" means the env var. A `claude -p` started from inside a Paseo session
inherits PASEO_AGENT_ID and is guarded too.

Loop safety comes from the guard's own state, not from stop_hook_active: that flag
is set after a block by ANY Stop hook (arc-continuation's included), so keying on
it let another hook's block disarm this one. Instead each task id is blocked on at
most once per session. So a stop can only be refused again if a task the guard has
never asked about is in flight, for example a shell a subagent left behind. A task
the model was asked about and kept (a dev server, a standing monitor) is accepted
for the rest of the session, and it does not use up the lifetime budget, which
stays LIFE_MAX blocks per session and never resets. This mirrors arc-continuation's
consecutive and lifetime caps. State lives in
$BROOMVA_AUTONOMOUS_HOME/bg-task-guard/<sid>.json. Every BLOCK, and the first CAP of
each prompt, appends a line to $BROOMVA_AUTONOMOUS_HOME/bg-task-guard.jsonl.
"""
import json
import os
import re
import sys
import time

COUNTED = {"shell", "subagent", "workflow", "monitor", "MCP task", "cloud session"}
TERMINAL = {"completed", "failed", "killed", "stopped", "cancelled", "canceled"}
LIFE_MAX = 5


def _home():
    return os.environ.get("BROOMVA_AUTONOMOUS_HOME") or os.path.join(
        os.path.expanduser("~"), ".config", "broomva", "autonomous")


def in_flight(payload):
    tasks = payload.get("background_tasks")
    if not isinstance(tasks, list):
        return []
    out = []
    for t in tasks:
        if not isinstance(t, dict):
            continue
        if t.get("type") not in COUNTED:
            continue
        if str(t.get("status", "")).lower() in TERMINAL:
            continue
        out.append(t)
    return out


def _clip(s, n=120):
    s = " ".join(str(s or "").split())
    return s if len(s) <= n else s[: n - 1] + "…"


def reason(tasks):
    lines = []
    for t in tasks[:6]:
        what = t.get("command") if t.get("type") == "shell" and t.get("command") else t.get("description")
        lines.append("  - %s %s: %s" % (t.get("type"), t.get("id", "?"), _clip(what)))
    if len(tasks) > 6:
        lines.append("  - ... and %d more" % (len(tasks) - 6))
    return (
        "[bstack bg-task-stop-guard, BRO-2815] Do not end this turn yet: %d background "
        "task(s) of this session are still running:\n%s\n"
        "This session runs under Paseo, where a background task finishing does not "
        "reliably wake an idle session, so ending the turn now can strand the arc. "
        "Wait for them in the FOREGROUND now, then act on the result in this same turn:\n"
        "  - Keep making foreground calls until each task's completion notification "
        "arrives (it is delivered between tool calls). A foreground Bash until-loop on "
        "the condition the task produces works, e.g. polling its output file or "
        "`gh pr checks`, with timeout under 10 minutes; run it again if it times out. "
        "A long leading `sleep` is refused by the harness.\n"
        "  - A subagent is done when its completion notification arrives. Its output "
        "file can show an ended turn while a shell it left behind is still working.\n"
        "  - For a CI wait, re-run `p9 watch <pr>` in the foreground (exit 8 means run it again).\n"
        "If a task is meant to outlive this turn (a dev server, a standing monitor), say "
        "so in one line and end the turn. This guard never asks twice about the same task. "
        "Stop a task with TaskStop only if nothing depends on it." % (len(tasks), "\n".join(lines))
    )


def _log(home, rec):
    try:
        os.makedirs(home, exist_ok=True)
        with open(os.path.join(home, "bg-task-guard.jsonl"), "a") as f:
            f.write(json.dumps(rec) + "\n")
    except OSError:
        pass


def _task_key(t):
    return str(t.get("id") or "%s:%s" % (t.get("type"), _clip(t.get("description"), 200)))


def decide(payload, env):
    """Return (verdict, reason_or_None). Pure apart from the state file."""
    if not env.get("PASEO_AGENT_ID"):
        return "ALLOW", None
    if env.get("BSTACK_BG_TASK_GUARD", "1") == "0":
        return "ALLOW", None
    if payload.get("hook_event_name") not in (None, "Stop"):
        return "ALLOW", None
    tasks = in_flight(payload)
    if not tasks:
        return "ALLOW", None
    sid = re.sub(r"[^A-Za-z0-9_.-]", "_", str(payload.get("session_id") or ""))
    if not sid:
        return "ALLOW", None
    home = _home()
    sdir = os.path.join(home, "bg-task-guard")
    spath = os.path.join(sdir, sid + ".json")
    try:
        with open(spath) as f:
            state = json.load(f)
    except (OSError, ValueError):
        state = {}
    if not isinstance(state, dict):
        state = {}
    asked = set(state.get("blocked_tasks") or [])
    fresh = [t for t in tasks if _task_key(t) not in asked]
    pid = str(payload.get("prompt_id") or "")
    total = int(state.get("total_blocks", 0))
    rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "session_id": sid,
           "paseo_agent_id": env.get("PASEO_AGENT_ID"), "prompt_id": pid,
           "tasks": [{"type": t.get("type"), "id": t.get("id")} for t in tasks]}
    if not fresh or total >= LIFE_MAX:
        if state.get("last_cap_prompt") != pid or not pid:
            state["last_cap_prompt"] = pid
            _save(sdir, spath, state)
            rec["verdict"] = "CAP"
            _log(home, rec)
        return "CAP", None
    state["total_blocks"] = total + 1
    state["blocked_tasks"] = sorted(asked | {_task_key(t) for t in tasks})
    _save(sdir, spath, state)
    rec["verdict"] = "BLOCK"
    _log(home, rec)
    return "BLOCK", reason(tasks)


def _save(sdir, spath, state):
    os.makedirs(sdir, exist_ok=True)
    tmp = "%s.%d.tmp" % (spath, os.getpid())
    with open(tmp, "w") as f:
        json.dump(state, f)
    os.replace(tmp, spath)


def main():
    try:
        payload = json.loads(sys.stdin.read() or "{}")
        if not isinstance(payload, dict):
            return 0
        verdict, why = decide(payload, os.environ)
    except Exception:
        return 0
    if verdict == "BLOCK":
        sys.stderr.write(why + "\n")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
