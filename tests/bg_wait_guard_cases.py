#!/usr/bin/env python3
"""bg_wait_guard_cases.py — the bg-wait Stop guard, driven through hooks/hooks.json (BRO-2815).

  bg_wait_guard_cases.py ROOT cases    run every case against the plugin tree at ROOT
  bg_wait_guard_cases.py ROOT mutate   for each mutant: copy ROOT, break ONE rule, and
                                       require the cases to go red

Both polarities are mandatory. The replay fixtures under tests/fixtures/bg-wait are
slices of real Paseo transcripts (build_fixtures.py records where each came from):
the four reported stalls must be blocked, the real normal endings must not be.
Each fixture runs twice: once with the Stop input Claude Code 2.1.280 sends
(last_assistant_message + background_tasks), once with neither, so the transcript
fallback (TaskLedger) is held to the same verdicts.

The mutation arm is what makes the suite mean something: deleting the matcher, the
running-task check, the ledger's end detection, the cap, the gate, or any one of
the matcher's exclusions must turn at least one case red. A rule whose mutant
survives is decoration.
"""
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
FIX = os.path.join(HERE, "fixtures", "bg-wait")

STALLS = ["stall-0e8bb3ff-ci-watch", "stall-0e8bb3ff-monitor",
          "stall-aaf0057f-merge-gate-loop", "stall-9cabd372-reviewer"]
NORMALS = ["ok-9cabd372-as-recorded", "ok-closed-with-task-running",
           "ok-foreground-wait-completed", "ok-running-no-promise"]

# The handback contract's form of 9cabd372's real closing ask (its ARC-STATUS trailer
# dropped, so only the ask-block rule can release it), over the stall transcript in
# which the reviewer is still in flight. It also carries a wait phrase, so the ask
# block is what stands between it and a block.
HANDBACK = (
    "## ⛔ Blocked on you\n\n"
    "| # | Decision | Recommended | Default if you say nothing |\n"
    "|---|---|---|---|\n"
    "| 1 | Pick the product name, or keep the placeholder | Keep the placeholder | "
    "Placeholder stays |\n"
    "| 2 | Send the vendor terms email on day 1 of the build | Send on day 1 | "
    "No email |\n\n"
    "Meanwhile I'll merge once the reviewer reports.")


# The matcher, as a table: realistic closing paragraphs, each judged by promise_span
# in the tree under test. Every pattern and exclusion is load-bearing for a row.
SHOULD_PROMISE = [
    "CI is running. I'll wait for the watcher to report back.",
    "Waiting for the reviewer's verdict.",
    "Standing by for the CI result.",
    "Sleeping until CI is green.",
    "I'm idle until those numbers arrive.",
    "I'll check back when it's done.",
    "Will check in after the reviewer is finished.",
    "Each result will come in as a notification.",
    "The watcher will ping me when it is done, so notify me is wired.",
    "Monitor armed.",
    "I'm watching it in the background.",
    "A background loop will re-run the Merge Gate.",
    "I'll merge when the policy gate allows it.",
    "I'll report back once both verdicts and CI are in.",
    "I'll be back on this when the reviewer reports.",
    "Once both review verdicts arrive, I'll fix what they find.",
    "When it's green I'll merge.",
    "Once #668 goes green I'll merge it.",
    "Opened the PR and will merge, pinned to that commit, once it passes.",
    "I'll merge when CI is green.\n\nARC-STATUS: OPEN",
    "ARC-STATUS is the trailer we use. I'll wait for CI to report back.",
]
SHOULD_NOT = [
    "Sent the Slack notification to the owner; PR #12 is merged.",
    "Users get notified by email now.",
    "Fixed: the hook now exits 0 when the parse returns an error.",
    "The cache is invalidated once the request completes.",
    "Subagents report back through SendMessage.",
    "Shipped. The background job is running on Railway as before.",
    "The hook will wait forever if TaskStop returns taskId.",
    "The bot will merge when green.",
    "It's waiting on its round-2 reviewers.",
    "I'm not waiting on the watcher; it is only a log tail.",
    "Waiting on your answer to the naming question.",
    "The stall looked like `I'll wait for the watcher` and is now fixed.",
    'It ended its turn "waiting for the reviewer", the same stall the spec lists.',
    "Quoted from the brief:\n> I'll wait for the watcher to report back.",
    "Before:\n```\nI'll wait for the watcher to report back.\n```\nNow fixed.",
    "I'll merge when CI is green.\n\nARC-STATUS: DONE",
    "ARC-STATUS: OPEN while I check.\n\nI'll merge when CI is green.\n\nARC-STATUS: CLOSED",
]


def load_guard(root):
    spec = importlib.util.spec_from_file_location(
        "bgw_%d" % abs(hash(root)), os.path.join(root, "scripts", "bg_wait_guard.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def hook_command(root):
    hooks = json.load(open(os.path.join(root, "hooks", "hooks.json")))["hooks"]
    hits = [h["command"] for b in hooks.get("Stop", []) for h in b["hooks"]
            if "bg_wait_guard.py" in h["command"]]
    if len(hits) != 1:
        raise SystemExit("hooks.json: %d Stop commands name bg_wait_guard.py" % len(hits))
    return hits[0]


class Runner:
    def __init__(self, root):
        self.root = root
        self.cmd = hook_command(root)
        self.tmp = tempfile.mkdtemp(prefix="bgw-")
        self.home = os.path.join(self.tmp, "arcs")
        self.neutral = os.path.join(self.tmp, "neutral")
        os.makedirs(self.neutral)

    def close(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run(self, payload, env=None, raw=None):
        e = {k: v for k, v in os.environ.items()
             if k not in ("BSTACK_BG_WAIT_GUARD", "PASEO_AGENT_ID", "PYTHONPATH")}
        e.update({"CLAUDE_PLUGIN_ROOT": self.root, "BROOMVA_AUTONOMOUS_HOME": self.home,
                  "BSTACK_BG_WAIT_GUARD": "force"})
        e.update(env or {})
        e = {k: v for k, v in e.items() if v is not None}
        stdin = raw if raw is not None else json.dumps(payload)
        p = subprocess.run(["bash", "-c", self.cmd], input=stdin, capture_output=True,
                           text=True, env=e, cwd=self.neutral, timeout=60)
        out = p.stdout.strip()
        try:
            decision = json.loads(out) if out else None
        except ValueError:
            decision = {"unparseable": out}
        return p.returncode, decision


def fixture_payload(name, sid, mode="input"):
    payload = json.load(open(os.path.join(FIX, name + ".payload.json")))
    payload["session_id"] = sid
    payload["transcript_path"] = os.path.join(FIX, name + ".jsonl")
    if mode == "fallback":
        payload.pop("last_assistant_message", None)
        payload.pop("background_tasks", None)
    return payload


def cases(root):
    """Every case, as (label, passed). Never raises on a wrong verdict."""
    r = Runner(root)
    results = []
    n = [0]

    def sid():
        n[0] += 1
        return "S%03d" % n[0]

    def check(label, ok):
        results.append((label, bool(ok)))

    def blocked(d):
        return isinstance(d, dict) and d.get("decision") == "block"

    try:
        # ── replay fixtures, both input modes ──────────────────────────────
        for mode in ("input", "fallback"):
            for name in STALLS:
                rc, d = r.run(fixture_payload(name, sid(), mode))
                task = json.load(open(os.path.join(FIX, name + ".payload.json")))["background_tasks"][0]["id"]
                check("[%s] %s is blocked" % (mode, name), rc == 0 and blocked(d))
                check("[%s] %s: the reason names the in-flight task" % (mode, name),
                      blocked(d) and task in d.get("reason", "") and "foreground" in d.get("reason", ""))
            for name in NORMALS:
                rc, d = r.run(fixture_payload(name, sid(), mode))
                check("[%s] %s is not blocked" % (mode, name), rc == 0 and d is None)
            hb = fixture_payload("stall-9cabd372-reviewer", sid(), mode)
            if mode == "input":
                hb["last_assistant_message"] = HANDBACK
                rc, d = r.run(hb)
                check("[input] a handback ask block with a task in flight is not blocked",
                      rc == 0 and d is None)

        # ── the matcher's exclusions, each on a payload with a task in flight ──
        running = [{"id": "bTEST", "type": "local_bash", "status": "running", "description": "x"}]

        def verdict(text, tasks=running, **extra):
            p = {"session_id": sid(), "stop_hook_active": False,
                 "last_assistant_message": text, "background_tasks": tasks}
            p.update(extra)
            return r.run(p)[1]

        check("matcher: a plain stall line blocks",
              blocked(verdict("CI is running. I'll wait for the watcher to report back.")))
        check("matcher: a wait phrase only in an EARLIER paragraph does not block",
              verdict("Queue:\n- #781: I'll merge it once CI is green.\n\n"
                      "Railway is still logged out; this run didn't need it.") is None)
        check("matcher: a third-person report ('It's waiting on ...') does not block",
              verdict("It's waiting on its round-2 reviewers.") is None)
        check("matcher: a negated wait does not block",
              verdict("I'm not waiting on the watcher; it is only a log tail.") is None)
        check("matcher: waiting on a person does not block",
              verdict("Waiting on your answer to the naming question.") is None)
        check("matcher: a wait phrase inside inline code does not block",
              verdict("The stall looked like `I'll wait for the watcher` and is now fixed.") is None)
        check("matcher: a wait phrase in double quotes (a cited stall) does not block",
              verdict('The worker opened its PR. It ended its turn "waiting for the reviewer", '
                      'the same stall the spec lists as a trap.') is None)
        check("matcher: a promise with a double-quoted name still blocks",
              blocked(verdict('I\'ll wait for the "plan-drift" check to report back.')))
        g = load_guard(root)
        for text in SHOULD_PROMISE:
            check("matcher table: promise: %r" % text[:60], g.promise_span(text) is not None)
        for text in SHOULD_NOT:
            check("matcher table: no promise: %r" % text[:60], g.promise_span(text) is None)
        check("matcher: a declared terminal (ARC-STATUS: DONE) does not block",
              verdict("I'll merge when CI is green.\n\nARC-STATUS: DONE") is None)
        check("matcher: a moving ARC-STATUS (OPEN) still blocks",
              blocked(verdict("I'll merge when CI is green.\n\nARC-STATUS: OPEN")))

        # ── the running-task check ─────────────────────────────────────────
        stall_text = "Monitor armed. I'll wait for it to report back."
        check("running: background_tasks present and empty is authoritative (no block)",
              verdict(stall_text, tasks=[],
                      transcript_path=os.path.join(FIX, "stall-0e8bb3ff-monitor.jsonl")) is None)
        check("running: only terminal-status tasks (no block)",
              verdict(stall_text, tasks=[{"id": "b1", "type": "local_bash", "status": "completed"},
                                         {"id": "b2", "type": "monitor", "status": "killed"}]) is None)
        check("running: a pending task counts as in flight",
              blocked(verdict(stall_text, tasks=[{"id": "b3", "type": "local_bash", "status": "pending"}])))
        check("running: a harness-internal task (dream) is not the session's wait",
              verdict(stall_text, tasks=[{"id": "d1", "type": "dream", "status": "running"}]) is None)
        check("running: an unknown task type does not count (fail open)",
              verdict(stall_text, tasks=[{"id": "u1", "status": "running"}]) is None)
        d = verdict(stall_text)
        check("reason: points at TaskOutput on the running task, not a relaunch",
              blocked(d) and "TaskOutput" in d["reason"] and "launch it again" in d["reason"])

        # ── the transcript ledger (fallback mode: no background_tasks) ───────
        def transcript(name, rows):
            path = os.path.join(r.tmp, name + ".jsonl")
            with open(path, "w") as f:
                for row in rows:
                    f.write(json.dumps(row) + "\n")
            return path

        def launch(tool_id, name, result, ts):
            return [{"type": "assistant", "timestamp": ts, "message": {"content": [
                        {"type": "tool_use", "id": tool_id, "name": name, "input": {}}]}},
                    {"type": "user", "timestamp": ts, "message": {"content": [
                        {"type": "tool_result", "tool_use_id": tool_id, "content": "x"}]},
                     "toolUseResult": result}]

        def fallback(path):
            return r.run({"session_id": sid(), "stop_hook_active": False,
                          "last_assistant_message": stall_text, "transcript_path": path})[1]
        t0, t1, t4 = "2026-10-05T10:00:00Z", "2026-10-05T10:20:00Z", "2026-10-05T13:00:00Z"
        mon = launch("m", "Monitor", {"taskId": "mon1", "timeoutMs": 1}, t0)
        stop_camel = launch("s", "TaskStop", {"taskId": "mon1"}, t1)
        check("ledger: a Monitor launch is in flight",
              blocked(fallback(transcript("mon", mon))))
        check("ledger: a TaskStop reporting taskId ends it",
              fallback(transcript("mon-stop", mon + stop_camel)) is None)
        agent = launch("a", "Agent", {"isAsync": True, "agentId": "ag1"}, t0)
        intr = [{"type": "user", "timestamp": t1,
                 "message": {"content": "[Request interrupted by user]"}}]
        check("ledger: a background subagent is in flight",
              blocked(fallback(transcript("ag", agent))))
        check("ledger: a user interrupt ends a background subagent",
              fallback(transcript("ag-intr", agent + intr)) is None)
        later = [{"type": "system", "timestamp": t4, "content": "tick"}]
        check("ledger: a launch older than STALE_SECONDS is not in flight",
              fallback(transcript("ag-stale", agent + later)) is None)

        # ── once per turn, and the arc-continuation cap ────────────────────
        check("cap: stop_hook_active (a hook-driven continuation) is never blocked",
              verdict(stall_text, stop_hook_active=True) is None)
        s = sid()

        def same(text, active=False):
            return r.run({"session_id": s, "stop_hook_active": active,
                          "last_assistant_message": text, "background_tasks": running})[1]
        first, second = same(stall_text), same(stall_text)
        check("cap: the first promise in a session blocks", blocked(first))
        check("cap: a second consecutive promise is not blocked (one nudge, never a loop)",
              second is None)
        same("Merged as abc123; CI green.")
        check("cap: a turn without the promise resets the consecutive counter",
              blocked(same(stall_text)))
        arc = os.path.join(root, "scripts", "autonomous-arc.sh")

        def arcget(session, field):
            env = dict(os.environ, BROOMVA_AUTONOMOUS_HOME=r.home)
            return subprocess.run(["bash", arc, "get", session, field], capture_output=True,
                                  text=True, env=env).stdout.strip()
        check("cap: a guard block never spends the shared total_blocks",
              arcget(s, "total_blocks") in ("", "0") and arcget(s, "bgwait_total") == "2")
        for _ in range(6):
            same("Merged as abc123; CI green.")
            same(stall_text)
        same("Merged as abc123; CI green.")
        check("cap: the guard's own lifetime ceiling (bgwait_total) holds",
              same(stall_text) is None)
        s2 = sid()
        env = dict(os.environ, BROOMVA_AUTONOMOUS_HOME=r.home)
        for _ in range(5):          # arc-continuation spends the shared ceiling
            subprocess.run(["bash", arc, "try-block", s2, "9", "5"], capture_output=True, env=env)
        check("cap: the shared total_blocks ceiling still stops the guard",
              r.run({"session_id": s2, "stop_hook_active": False,
                     "last_assistant_message": stall_text, "background_tasks": running})[1] is None)

        # ── the gate: opt-in, Paseo only unless forced ─────────────────────
        p = fixture_payload("stall-0e8bb3ff-monitor", sid())
        check("gate: off by default (no BSTACK_BG_WAIT_GUARD)",
              r.run(p, env={"BSTACK_BG_WAIT_GUARD": None, "PASEO_AGENT_ID": "a1"})[1] is None)
        check("gate: BSTACK_BG_WAIT_GUARD=1 outside Paseo does nothing",
              r.run(dict(p, session_id=sid()), env={"BSTACK_BG_WAIT_GUARD": "1"})[1] is None)
        check("gate: BSTACK_BG_WAIT_GUARD=1 under Paseo blocks",
              blocked(r.run(dict(p, session_id=sid()),
                            env={"BSTACK_BG_WAIT_GUARD": "1", "PASEO_AGENT_ID": "a1"})[1]))
        check("gate: BSTACK_BG_WAIT_GUARD=0 is off even under Paseo",
              r.run(dict(p, session_id=sid()),
                    env={"BSTACK_BG_WAIT_GUARD": "0", "PASEO_AGENT_ID": "a1"})[1] is None)

        # ── fail open ──────────────────────────────────────────────────────
        for label, raw in (("not JSON", "{oops"), ("a JSON list", "[1,2]"), ("empty", "")):
            rc, d = r.run(None, raw=raw)
            check("fail-open: %s on stdin exits 0 silently" % label, rc == 0 and d is None)
        rc, d = r.run({"session_id": sid(), "last_assistant_message": stall_text,
                       "background_tasks": "nonsense", "transcript_path": "/nonexistent"})
        check("fail-open: a malformed background_tasks with no transcript does not block",
              rc == 0 and d is None)
        bad = os.path.join(r.tmp, "garbage.jsonl")
        with open(bad, "w") as f:
            f.write("not json\n{\"type\": 3}\n[]\n")
        rc, d = r.run({"session_id": sid(), "transcript_path": bad})
        check("fail-open: a garbage transcript exits 0 silently", rc == 0 and d is None)

        # ── a block leaves a trace ─────────────────────────────────────────
        trace = os.path.join(r.home, "bg-wait-guard.jsonl")
        check("trace: every block decision is recorded",
              os.path.isfile(trace) and '"verdict": "BLOCK"' in open(trace).read())
    finally:
        r.close()
    return results


MUTANTS = [
    # (label, file, old, new): exactly one occurrence of old must exist
    ("matcher deleted (always a promise)", "scripts/bg_wait_guard.py",
     "    return promise_span(text) is not None", "    return True"),
    ("matcher deleted (never a promise)", "scripts/bg_wait_guard.py",
     "    return promise_span(text) is not None", "    return False"),
    ("running-task check deleted", "scripts/bg_wait_guard.py",
     "    if not running:\n        return \"NOT_RUNNING\"\n", ""),
    ("running-task check never sees a task", "scripts/bg_wait_guard.py",
     "    if not running:\n        return \"NOT_RUNNING\"\n",
     "    if True:\n        return \"NOT_RUNNING\"\n"),
    ("ledger ignores completion notifications", "scripts/bg_wait_guard.py",
     "                self.ended.add(tid.group(1).strip())", "                pass"),
    ("ledger pre-filter drops SendMessage resumes", "scripts/bg_wait_guard.py",
     "\"agentId\", \"resumedAgentId\", \"taskId\"", "\"agentId\", \"taskId\""),
    ("ledger ignores launches", "scripts/bg_wait_guard.py",
     "            self.launched[str(tid)] = info", "            pass"),
    ("stop_hook_active ignored", "scripts/bg_wait_guard.py",
     "        return None                         # already a hook-driven continuation this turn",
     "        pass"),
    ("consecutive cap lifted", "scripts/bg_wait_guard.py",
     "CONSEC_MAX = 1 ", "CONSEC_MAX = 99 "),
    ("gate always on", "scripts/bg_wait_guard.py",
     "    if flag == \"force\":\n        return True\n", "    return True\n"),
    ("ARC-STATUS terminal rule deleted", "scripts/bg_wait_guard.py",
     "    if declared and declared[-1].lower() not in MOVING_STATUSES:",
     "    if False:"),
    ("ask-block rule deleted", "scripts/bg_wait_guard.py",
     "    if ASK_HEAD_RE.search(text):", "    if False:"),
    ("last-paragraph rule deleted", "scripts/bg_wait_guard.py",
     "    body = _last_paragraph(_prose(text))", "    body = _prose(text)"),
    ("third-person rule deleted", "scripts/bg_wait_guard.py",
     "and THIRD_PERSON_RE.search(before):", "and False:"),
    ("negator deleted", "scripts/bg_wait_guard.py",
     "        if NEGATOR_RE.search(before):", "        if False:"),
    ("human-wait rule deleted", "scripts/bg_wait_guard.py",
     "(?:you|your|the user|the operator|a human)\\b\", after, re.I):",
     "(?:you|your|the user|the operator|a human)\\b\", after, re.I) and False:"),
    ("inline-code strip deleted", "scripts/bg_wait_guard.py",
     "    body = INLINE_CODE_RE.sub(\" \", body)", "    pass"),
    ("double-quote strip deleted", "scripts/bg_wait_guard.py",
     "    return DQUOTE_RE.sub(\" \", body)", "    return body"),
    ("block-quote strip deleted", "scripts/bg_wait_guard.py",
     "    body = QUOTE_RE.sub(\" \", body)", "    pass"),
    ("fence strip deleted", "scripts/bg_wait_guard.py",
     "    body = FENCE_RE.sub(\" \", text or \"\")", "    body = text or \"\""),
    ("ARC-STATUS: first declaration wins", "scripts/bg_wait_guard.py",
     "declared[-1].lower()", "declared[0].lower()"),
    ("ARC-STATUS separator not required", "scripts/bg_wait_guard.py",
     "ARC-STATUS\\s*[*_`]*\\s*[:=]", "ARC-STATUS\\s*[*_`]*\\s*[:=]?"),
    ("task-type allowlist deleted", "scripts/bg_wait_guard.py",
     "        if str(t.get(\"type\") or \"\") not in SESSION_TASK_TYPES:\n            continue\n", ""),
    ("ledger: TaskStop taskId ignored", "scripts/bg_wait_guard.py",
     "stopped = r.get(\"task_id\") or r.get(\"taskId\")", "stopped = r.get(\"task_id\")"),
    ("ledger: interrupt rule deleted", "scripts/bg_wait_guard.py",
     "            if x.startswith(_INTERRUPT):", "            if False:"),
    ("ledger: stale bound deleted", "scripts/bg_wait_guard.py",
     "self.now - born > STALE_SECONDS", "False"),
    ("guard spends total_blocks", "scripts/bg_wait_guard.py",
     "LIFE_COUNTER = \"bgwait_total\"", "LIFE_COUNTER = \"total_blocks\""),
    ("try-block ignores the total_blocks ceiling", "scripts/autonomous-arc.sh",
     "lc < life_max and tb < life_max", "lc < life_max"),
    ("reason suggests a relaunch", "scripts/bg_wait_guard.py",
     "TaskOutput \"\n    \"with block=true on the task that is already running (don't launch it again)",
     "Agent \"\n    \"run_in_background: false"),
] + [("matcher pattern %d deleted" % i, "scripts/bg_wait_guard.py",
      "PROMISE_RE = re.compile(", "del _PROMISE_PATTERNS[%d]\nPROMISE_RE = re.compile(" % i)
     for i in range(10)] + [
    ("registration removed from hooks.json", "hooks/hooks.json",
     "bg_wait_guard.py", "bg_wait_guard_unregistered.py"),
]


def mutate(root):
    fails = 0
    for label, rel, old, new in MUTANTS:
        d = tempfile.mkdtemp(prefix="bgw-mut-")
        try:
            for sub in ("scripts", "hooks"):
                shutil.copytree(os.path.join(root, sub), os.path.join(d, sub))
            p = os.path.join(d, rel)
            text = open(p).read()
            if text.count(old) != 1:
                print("  FAIL mutant setup: %r occurs %d times in %s" % (old[:50], text.count(old), rel))
                fails += 1
                continue
            open(p, "w").write(text.replace(old, new))
            try:
                red = [l for l, ok in cases(d) if not ok]
            except SystemExit as e:          # e.g. the registration mutant
                red = [str(e)]
            if red:
                print("  ok   mutant killed: %s (%d red: %s)" % (label, len(red), "; ".join(red[:3])))
            else:
                print("  FAIL mutant SURVIVED: %s" % label)
                fails += 1
        finally:
            shutil.rmtree(d, ignore_errors=True)
    return fails


def main(argv):
    root, mode = argv[1], argv[2]
    if mode == "cases":
        res = cases(root)
        for label, ok in res:
            print("  %s %s" % ("ok  " if ok else "FAIL", label))
        bad = sum(1 for _, ok in res if not ok)
        print("bg-wait-guard cases: %d passed, %d failed" % (len(res) - bad, bad))
        return 1 if bad else 0
    if mode == "mutate":
        fails = mutate(root)
        print("bg-wait-guard mutants: %d of %d killed" % (len(MUTANTS) - fails, len(MUTANTS)))
        return 1 if fails else 0
    raise SystemExit("usage: bg_wait_guard_cases.py ROOT cases|mutate")


if __name__ == "__main__":
    sys.exit(main(sys.argv))
