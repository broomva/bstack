#!/usr/bin/env python3
"""bg_wait_guard.py — Stop hook: a turn that promises a background wait does the wait instead (BRO-2815).

The report: Paseo sessions ended their turn on "I'll wait for the background CI
watch to notify me", went idle, and were nudged by the coordinator, four times on
2026-10-03/04, each under a brief that said "wait in the FOREGROUND". The premise
given was that a background task (Bash run_in_background, a Bash call moved to the
background at its timeout, the Monitor tool, a background Agent or Workflow) never
wakes an idle Paseo session.

MEASURED, AND THE PREMISE DOES NOT HOLD (replay over 570 Paseo sessions, 4,507 turn
endings, 2026-09-25..10-05; numbers in the PR): 1,678 endings had a task in flight;
in 0 of them did a task finish and its notification go undelivered. Of the 942
endings this hook would block, 876 (93%) were woken by the task's own notification
(median ~2 min later). The four reported stalls were nudged 10-60 s after going
idle, before their task finished; two of the four sessions had already been woken
by notifications earlier in the same session. So this hook is OPT-IN: what it buys
is the fleet rule "an idle session is a finished session", not a rescue from a
stranding nobody has observed.

The check, when enabled: block the Stop ONCE when both hold:
  1. the turn's final paragraph promises a background wait (promise_span), and
  2. a task this session launched is still in flight: the Stop input's own
     background_tasks list, or, on a Claude Code that does not send it, a replay of
     the transcript (TaskLedger).

This is a HEURISTIC, not a gate (KG: fail-closed-gate-fail-open-heuristic). A
false block costs a turn and fights a legitimate stop; a missed wait costs a nudge,
the status quo. So:
  - any parse error, missing field or unexpected shape exits 0 with no output;
  - off unless BSTACK_BG_WAIT_GUARD is set: 1/on enables it under Paseo
    (PASEO_AGENT_ID set), force enables it everywhere. In the plain CLI a finished
    task re-invokes the session, so waiting on one is the documented idiom there;
  - at most one block per turn: never while stop_hook_active (this Stop is
    already a hook-driven continuation), and the arc-continuation cap pattern on
    top (autonomous-arc.sh try-block with its own consecutive counter bgwait_count
    at 1, sharing the lifetime total_blocks ceiling with the other Stop checks).

Subcommands:
  hook                 read the Stop payload on stdin; print a block decision or nothing
  inflight FILE        the background tasks still in flight at the end of a transcript,
                       as JSON. For a coordinator tick: an idle session with a task in
                       flight is waiting, not stalled.
  replay FILE...       one JSON line per turn ending in each transcript: the verdict
                       this hook would have returned there (the measurement, and the
                       source of the test fixtures)
"""
import json
import os
import re
import subprocess
import sys

TERMINAL_STATUSES = {"completed", "failed", "killed", "stopped", "cancelled",
                     "canceled", "error", "timeout", "timed_out", "expired", "done"}
CONSEC_MAX = 1          # one rewrite nudge per turn, never a loop
LIFE_MAX = 5            # same lifetime ceiling as arc-continuation (total_blocks is shared)
COUNTER = "bgwait_count"

REASON = (
    "You're about to go idle waiting on a background task. In this fleet a wait runs in "
    "the foreground, so an idle session means a finished one. Do the wait in the "
    "foreground now (`gh pr checks --watch` with a timeout, or Agent "
    "`run_in_background: false`), then continue. If nothing depends on that task, "
    "TaskStop it or say so plainly, then end the turn.")

# ── the matcher ──────────────────────────────────────────────────────────────
# Phrased as PROMISES, not keywords: "watch" alone is in every `gh pr checks
# --watch` receipt, "background" in every summary of what ran there. Each pattern
# needs the forward-looking shape of a session that is about to wait.
_PROMISE_PATTERNS = [
    # "I'll wait for it", "will wait until", "going to wait", "let me wait"
    r"\b(?:i(?:'|’)?ll|i will|i(?:'|’)?m going to|i am going to|we(?:'|’)?ll|"
    r"will|let me|going to)\s+(?:\w+\s+){0,2}?(?:wait|await|hold|sit tight|stand by)\b",
    # "Waiting for the reviewer's verdict", "waiting on CI", "awaiting the watcher"
    r"\b(?:waiting|awaiting|await)\s+(?:for|on|until|the)\b",
    # "notify me", "be notified", "the notification", "report back", "wake me", "ping me when"
    r"\b(?:notif(?:y|ies|ied|ication|ications)|report(?:s|ing)? back|wakes? me|"
    r"ping me when|hear back|lets? me know when)\b",
    # "Monitor armed", "watcher is running", "a background loop will re-run …"
    r"\b(?:monitor|watch(?:er)?|poller|background (?:task|loop|watch|job|agent|shell|command|"
    r"process|check|run|review(?:er)?))\b[^.\n]{0,60}?\b(?:armed|running|in flight|"
    r"in progress|started|launched|will|is watching|keeps? watching|polls?)\b",
    # "I'll merge when both are green", "will continue once plan-drift concludes"
    r"\b(?:i(?:'|’)?ll|i will|will|then)\s+(?:\w+\s+){0,2}?(?:merge|continue|proceed|"
    r"resume|re-?run|pick (?:it|this|them)? ?up|act|ship|push|land|follow up|decide|post|"
    r"report|record|close|ask|batch|apply|fold|attempt)\b[^.\n]{0,80}?\b"
    r"(?:when|once|after|as soon as|the moment)\b",
    # "... when they report", "once it finishes": the turn hands its next step to a
    # completion it is not waiting for itself
    r"\b(?:when|once|as soon as|after|until|whichever)\s+(?:[\w#'\u2019-]+\s+){1,4}?"
    r"(?:reports?|finish(?:es)?|completes?|concludes?|returns?|comes? back|lands?|resolves?|"
    r"arrives?|settles?|(?:is|are) done)\b",
    # "I'm idle until those numbers arrive"
    r"\b(?:i(?:'|\u2019)m|i am)\s+(?:\w+\s+)?idle\b",
    # "When it's green I'll merge", "once they're both green"
    r"\b(?:when|once|as soon as)\s+(?:it(?:'|\u2019)s|it is|they(?:'|\u2019)re|they are|"
    r"both are|ci is|checks are)\s+(?:all\s+|both\s+)?green\b",
]
PROMISE_RE = re.compile("|".join("(?:%s)" % p for p in _PROMISE_PATTERNS), re.I)
# A trigger is only a trigger if nothing negates it just before ("no background
# task is running", "rather than waiting", "instead of waiting").
NEGATOR_RE = re.compile(
    r"(\bno\b|\bnot\b|\bnever\b|\bwithout\b|\bnothing\b|\bnone\b|\brather than\b|"
    r"\binstead of\b|n(?:'|’)t\b|\bcannot\b|\bno longer\b|\bstopped\b|\bkilled\b)"
    r"[^.;:\n]{0,30}$", re.I)
# "It's waiting on CI", "PRs waiting on review", "it will merge #761 once": a report
# about ANOTHER session or PR (a coordinator's tick), not this turn's own promise.
THIRD_PERSON_RE = re.compile(
    r"(?:\b(?:it|it(?:'|\u2019)s|he|she|they|they(?:'|\u2019)re|which|who|session|agent|"
    r"prs?)|#\d+|\b(?:is|are|was|were))\s+(?:(?:still|now|also|then|only)\s+)?$", re.I)
FENCE_RE = re.compile(r"^\s{0,3}(`{3,}|~{3,}).*?^\s{0,3}\1", re.M | re.S)
INLINE_CODE_RE = re.compile(r"`[^`\n]*`")
QUOTE_RE = re.compile(r"^\s{0,3}>.*$", re.M)
# Legitimate terminal endings even if a wait phrase appears in them: an explicit
# ARC-STATUS declaration (CLOSED, DONE, MERGED, BLOCKED, HANDBACK, ...) is the
# session saying it is stopping on purpose. Only a still-moving status is judged.
ARC_STATUS_RE = re.compile(r"ARC-STATUS\W{0,6}([A-Za-z][\w-]*)")
MOVING_STATUSES = {"open", "active", "in-progress", "in_progress", "running", "waiting"}
ASK_HEAD_RE = re.compile(
    r"^\s{0,3}#{1,4}[^\n]*?(⛔|blocked on you|what i need from you)", re.I | re.M)


def _prose(text):
    """The text a reader takes as the message's own voice: no fenced code, no inline
    code spans, no quoted lines. A message that QUOTES a stall (a review finding, the
    brief itself) is not making the promise."""
    body = FENCE_RE.sub(" ", text or "")
    body = QUOTE_RE.sub(" ", body)
    return INLINE_CODE_RE.sub(" ", body)


def _last_paragraph(body):
    """Where a turn says what it does next. A stall's promise is its closing line; the
    same phrase earlier in a long report ("I'll merge it once CI passes" inside a
    queue summary) describes a plan, not the turn's ending. Measured on 60 hand-labeled
    would-be blocks: judging only this paragraph removed 9 of 10 false positives. A
    trailing ARC-STATUS line is a trailer, not the paragraph."""
    paras = [p for p in re.split(r"\n\s*\n", body) if p.strip()]
    while len(paras) > 1 and ARC_STATUS_RE.search(paras[-1]) and len(paras[-1]) < 300:
        paras.pop()
    return paras[-1] if paras else ""


def promise_span(text):
    """The phrase that makes the message a background-wait promise, or None."""
    if not text or not text.strip():
        return None
    status = ARC_STATUS_RE.search(text)
    if status and status.group(1).lower() not in MOVING_STATUSES:
        return None                         # a declared terminal: closed, merged, handed back
    if ASK_HEAD_RE.search(text):
        return None                         # a handback that asks a human
    body = _last_paragraph(_prose(text))
    for m in PROMISE_RE.finditer(body):
        before = body[max(0, m.start() - 40):m.start()]
        if NEGATOR_RE.search(before):
            continue
        if re.match(r"(?:waiting|awaiting|will)\b", m.group(0), re.I) and THIRD_PERSON_RE.search(before):
            continue
        after = body[m.end():m.end() + 40]
        # "waiting on you", "waiting for your answer": a person, not a task
        if re.match(r"\s*(?:\w+\s+){0,2}?(?:you|your|the user|the operator|a human)\b", after, re.I):
            continue
        return m.group(0)
    return None


def promises_background_wait(text):
    """True when the message promises to wait on something it does not do itself."""
    return promise_span(text) is not None


# ── the running-task check ───────────────────────────────────────────────────
def running_from_input(payload):
    """The Stop payload's own in-flight list, or None when this Claude Code does not
    send one. Present-and-empty means nothing is running, and that is authoritative."""
    tasks = payload.get("background_tasks")
    if not isinstance(tasks, list):
        return None
    out = []
    for t in tasks:
        if not isinstance(t, dict):
            continue
        status = str(t.get("status") or "").lower()
        if status in TERMINAL_STATUSES:
            continue
        out.append({"id": str(t.get("id") or "?"), "type": str(t.get("type") or "task"),
                    "description": str(t.get("description") or t.get("command") or "")[:120]})
    return out


_NOTE_RE = re.compile(r"<task-notification>(.*?)</task-notification>", re.S)
_ID_RE = re.compile(r"<task-id>([^<]+)</task-id>")
_STATUS_RE = re.compile(r"<status>([^<]+)</status>")


class TaskLedger:
    """Replays a transcript into the set of background tasks still in flight.

    Launch: a tool result carrying backgroundTaskId (Bash, including a call moved to
    the background at its timeout), a Monitor's taskId, an async Agent's agentId, an
    async Workflow's taskId. End: a <task-notification> with a <status> (delivered,
    queued, or merely enqueued), a TaskStop/KillShell result, or a TaskOutput that
    reports a terminal status. Monitor events carry no <status> and end nothing.
    """

    def __init__(self):
        self.launched = {}
        self.ended = set()
        self._tool = {}

    def running(self):
        return [dict(id=k, **v) for k, v in self.launched.items() if k not in self.ended]

    def _notes(self, s):
        if "<task-notification>" not in s:
            return
        for m in _NOTE_RE.finditer(s):
            tid, st = _ID_RE.search(m.group(1)), _STATUS_RE.search(m.group(1))
            if tid and st:
                self.ended.add(tid.group(1).strip())

    def feed(self, o):
        t = o.get("type")
        if t == "queue-operation":
            if isinstance(o.get("content"), str):
                self._notes(o["content"])
            return
        if t == "attachment":
            a = o.get("attachment") or {}
            if isinstance(a.get("prompt"), str):
                self._notes(a["prompt"])
            return
        msg = o.get("message") if isinstance(o.get("message"), dict) else {}
        content = msg.get("content")
        if t == "assistant" and isinstance(content, list):
            for b in content:
                if isinstance(b, dict) and b.get("type") == "tool_use":
                    inp = b.get("input") if isinstance(b.get("input"), dict) else {}
                    self._tool[b.get("id")] = (b.get("name"), inp)
            return
        if t != "user":
            return
        if isinstance(content, str):
            self._notes(content)
        elif isinstance(content, list):
            for b in content:
                if isinstance(b, dict) and b.get("type") == "text":
                    self._notes(b.get("text") or "")
        r = o.get("toolUseResult")
        if not isinstance(r, dict):
            return
        name, inp = None, {}
        if isinstance(content, list):
            for b in content:
                if isinstance(b, dict) and b.get("type") == "tool_result":
                    name, inp = self._tool.get(b.get("tool_use_id"), (None, {}))
        tid = info = None
        if r.get("backgroundTaskId"):
            tid, info = r["backgroundTaskId"], {
                "type": "shell", "description": str(inp.get("command") or "")[:120]}
        elif r.get("isAsync") and r.get("agentId"):
            tid, info = r["agentId"], {
                "type": "subagent", "description": str(r.get("description") or "")[:120]}
        elif r.get("resumedAgentId") and r.get("success") is not False:
            # SendMessage to a finished background agent resumes it under the SAME id;
            # its next notification is what ends it again.
            tid, info = r["resumedAgentId"], {
                "type": "subagent", "description": str(inp.get("summary") or "")[:120]}
        elif r.get("taskId") and (name == "Monitor" or "timeoutMs" in r):
            tid, info = r["taskId"], {
                "type": "monitor", "description": str(inp.get("description") or "")[:120]}
        elif r.get("taskId") and r.get("status") == "async_launched":
            tid, info = r["taskId"], {
                "type": str(r.get("taskType") or "workflow"),
                "description": str(r.get("workflowName") or "")[:120]}
        if tid:
            self.launched[str(tid)] = info
            self.ended.discard(str(tid))
        if r.get("task_id") and name in ("TaskStop", "KillShell", "KillBash"):
            self.ended.add(str(r["task_id"]))
        task = r.get("task")
        if isinstance(task, dict) and task.get("task_id") \
                and str(task.get("status") or "").lower() in TERMINAL_STATUSES:
            self.ended.add(str(task["task_id"]))


_RELEVANT = ("backgroundTaskId", "agentId", "resumedAgentId", "taskId", "task_id",
             "task-notification", '"tool_use"')


def running_from_transcript(path):
    ledger = TaskLedger()
    with open(path, encoding="utf-8", errors="replace") as f:
        for ln in f:
            if not any(k in ln for k in _RELEVANT):
                continue                    # most lines; skip the json parse
            try:
                ledger.feed(json.loads(ln))
            except ValueError:
                continue
    return ledger.running()


def entry_text(o):
    msg = o.get("message") if isinstance(o.get("message"), dict) else o
    c = msg.get("content")
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        return " ".join(b.get("text", "") for b in c
                        if isinstance(b, dict) and b.get("type") == "text").strip()
    return ""


def last_assistant_text(path):
    """Fallback for a Claude Code that does not send last_assistant_message. It can
    read the PREVIOUS entry (CC flushes the final one ~125ms after Stop, BRO-1616);
    that entry is then usually a tool call with no text, which never matches, so the
    race fails open."""
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - 1048576))
            data = f.read().decode("utf-8", "replace")
    except OSError:
        return ""
    last = ""
    for ln in data.splitlines():
        try:
            o = json.loads(ln)
        except ValueError:
            continue
        if isinstance(o, dict) and o.get("type") == "assistant":
            last = entry_text(o)
    return last


def decide(text, running):
    """BLOCK | NO_PROMISE | NOT_RUNNING. Pure; the unit the tests mutate."""
    if not promises_background_wait(text):
        return "NO_PROMISE"
    if not running:
        return "NOT_RUNNING"
    return "BLOCK"


def enabled(env):
    flag = str(env.get("BSTACK_BG_WAIT_GUARD", "")).strip().lower()
    if flag == "force":
        return True
    return flag in ("1", "on", "true", "yes") and bool(env.get("PASEO_AGENT_ID"))


# ── cap (the arc-continuation pattern, through the same helper) ──────────────
def _arc(*args):
    helper = os.path.join(os.path.dirname(os.path.abspath(__file__)), "autonomous-arc.sh")
    if not os.path.isfile(helper):
        return ""
    try:
        p = subprocess.run(["bash", helper] + list(args), capture_output=True, text=True,
                           timeout=5)
    except (OSError, subprocess.SubprocessError):
        return ""
    return p.stdout.strip()


def _trace(record):
    """One line per BLOCK/CAP, so a block that fired is measurable later. Best effort."""
    home = os.environ.get("BROOMVA_AUTONOMOUS_HOME") or os.path.expanduser(
        "~/.config/broomva/autonomous")
    try:
        os.makedirs(home, exist_ok=True)
        with open(os.path.join(home, "bg-wait-guard.jsonl"), "a") as f:
            f.write(json.dumps(record) + "\n")
    except OSError:
        pass


def hook(stdin_text, env):
    try:
        payload = json.loads(stdin_text or "{}")
    except ValueError:
        return None
    if not isinstance(payload, dict) or not enabled(env):
        return None
    sid = payload.get("session_id") or payload.get("sessionId") or ""
    transcript = payload.get("transcript_path") or payload.get("transcriptPath") or ""
    text = payload.get("last_assistant_message")
    if not isinstance(text, str):
        text = last_assistant_text(transcript) if transcript and os.path.isfile(transcript) else ""
    if not promises_background_wait(text):
        # A turn that ends without the promise is the "productive" reset of the
        # arc-continuation pattern. Read before writing: never create state for a
        # session this hook has never blocked.
        if sid and _arc("get", sid, COUNTER) not in ("", "0"):
            _arc("reset", sid, COUNTER)
        return None
    if payload.get("stop_hook_active") or payload.get("stopHookActive"):
        return None                         # already a hook-driven continuation this turn
    running = running_from_input(payload)
    if running is None:
        running = running_from_transcript(transcript) if transcript and os.path.isfile(transcript) else []
    if decide(text, running) != "BLOCK" or not sid:
        return None
    verdict = _arc("try-block", sid, str(CONSEC_MAX), str(LIFE_MAX), COUNTER)
    _trace({"session_id": sid, "verdict": verdict or "NO_HELPER",
            "tasks": [t.get("id") for t in running][:10], "text": text[-200:]})
    if verdict != "BLOCK":
        return None
    listed = "; ".join("%s %s%s" % (t.get("type"), t.get("id"),
                                    (" (%s)" % t["description"]) if t.get("description") else "")
                       for t in running[:5])
    return {"decision": "block", "reason": REASON + " Still in flight: " + listed + "."}


# ── replay: what the hook would have said at every turn ending ───────────────
def replay(path):
    """Yield one record per turn ending. A turn ends where Claude Code ran the Stop
    hooks (a system stop_hook_summary entry); the ending's text is the last assistant
    entry before it, and the in-flight set is the ledger at that point."""
    ledger = TaskLedger()
    last = None
    paseo = False
    with open(path, encoding="utf-8", errors="replace") as f:
        for i, ln in enumerate(f, 1):
            try:
                o = json.loads(ln)
            except ValueError:
                continue
            if not isinstance(o, dict):
                continue
            if o.get("entrypoint") == "sdk-cli":
                paseo = True
            ledger.feed(o)
            if o.get("type") == "assistant" and not o.get("isSidechain"):
                last = (i, o)
            if o.get("type") == "system" and o.get("subtype") == "stop_hook_summary" and last:
                li, lo = last
                text = entry_text(lo)
                running = ledger.running()
                yield {"file": path, "line": li, "sdk": paseo,
                       "promise": promise_span(text),
                       "running": [t["id"] for t in running],
                       "verdict": decide(text, running), "text": text[-400:]}
                last = None


def main(argv):
    cmd = argv[1] if len(argv) > 1 else "hook"
    if cmd == "inflight":
        try:
            print(json.dumps(running_from_transcript(argv[2])))
        except (IndexError, OSError) as e:
            print(json.dumps({"error": str(e)}))
            return 1
        return 0
    if cmd == "replay":
        for p in argv[2:]:
            try:
                for rec in replay(p):
                    print(json.dumps(rec))
            except OSError as e:
                print(json.dumps({"file": p, "error": str(e)}))
        return 0
    try:
        out = hook(sys.stdin.read(), os.environ)
    except Exception:                       # heuristic: fail OPEN on anything
        return 0
    if out:
        print(json.dumps(out))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
