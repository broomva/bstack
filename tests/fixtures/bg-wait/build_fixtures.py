#!/usr/bin/env python3
"""build_fixtures.py — regenerate the bg-wait replay fixtures from real transcripts (BRO-2815).

NOT run in CI: the source transcripts live only on the machine that recorded them.
The fixtures it wrote are committed beside it; this file records where each came
from and exactly what was kept, so a reviewer can rebuild and diff them.

Each fixture is a SLICE of a real Paseo transcript: every entry up to (not
including) the Stop it ended on, reduced to the fields the task ledger reads, plus
the final assistant entry with its text verbatim. Redaction: commands, tool
output, earlier assistant prose and every human/coordinator message become
"[redacted]"; task notifications keep only their task-id and status; paths are
dropped. Task ids are kept. The final text keeps only what the matcher reads
(trim_final): its closing paragraph, ARC-STATUS trailers, ask-block headings, and
any earlier paragraph carrying a wait phrase (the last-paragraph rule is tested on
it). Every other paragraph becomes "[redacted]".

<name>.payload.json is the Stop input Claude Code would have sent: the final text
as last_assistant_message, and background_tasks as the ledger saw them at that
Stop, frozen here so the test never derives its input from the code under test.

Usage: build_fixtures.py [PROJECTS_DIR]   (default ~/.claude/projects)
"""
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "..", "scripts"))
import bg_wait_guard as g  # noqa: E402

# name, source transcript (relative to PROJECTS_DIR), the 1-based line of the turn's
# final assistant entry (the Stop is the next stop_hook_summary after it; None = find
# one, see below), expected verdict, a task id whose completion records are dropped,
# note.
SPECS = [
    ("stall-0e8bb3ff-ci-watch",
     "-Users-broomva--paseo-worktrees-1knm9c8c-bro-2814-clippy/bf70ef3d-956e-4010-bfe0-0da91961eb19.jsonl",
     562, "block", None, "BRO-2814 session, 2026-10-04 13:28: a gh pr checks --watch auto-moved "
     "to the background; 'I'll wait for the background CI watch task to notify me'"),
    ("stall-0e8bb3ff-monitor",
     "-Users-broomva--paseo-worktrees-1knm9c8c-bro-2814-clippy/bf70ef3d-956e-4010-bfe0-0da91961eb19.jsonl",
     842, "block", None, "same session, 13:53, after the first nudge: 'Monitor armed. I'll wait "
     "for it to report back'"),
    ("stall-aaf0057f-merge-gate-loop",
     "-Users-broomva--paseo-worktrees-0t10n7id-new-octopus/22365d24-5bd5-4c92-ada0-a9a10770f2f7.jsonl",
     1465, "block", None, "workspace#883, 2026-10-03 14:47: 'a background loop will re-run the "
     "Merge Gate once plan-drift concludes. I'll merge when both are green.'"),
    ("stall-9cabd372-reviewer",
     "-Users-broomva--paseo-worktrees-0t10n7id-excited-sheep/7fd0ecd3-111b-44a3-a690-87413447b885.jsonl",
     1617, "block", "a82a45b8d07bdb388", "workspace#885, 2026-10-03 14:57: 'Waiting for the reviewer's "
     "verdict.' The reviewer (resumed via SendMessage) is in flight: its completion, "
     "enqueued 1 s before this Stop, is DROPPED, i.e. the moment the promise was written"),
    ("ok-9cabd372-as-recorded",
     "-Users-broomva--paseo-worktrees-0t10n7id-excited-sheep/7fd0ecd3-111b-44a3-a690-87413447b885.jsonl",
     1617, "allow", None, "the same Stop exactly as recorded: the reviewer's completion was already "
     "queued, nothing is in flight, and the session resumed itself 3 s later. A wait that "
     "completed is not a stall"),
    ("ok-closed-with-task-running",
     "-Users-broomva-broomva/27c0bc75-6715-4af4-ae02-112b4f118a1e.jsonl",
     244, "allow", None, "a real ARC-STATUS: CLOSED ending with a background task still in flight"),
    ("ok-foreground-wait-completed",
     "-Users-broomva-broomva/0e1ae31f-bf67-428a-9134-bc0c1c82ddbb.jsonl",
     1122, "allow", None, "a real turn that ran gh pr checks --watch in the FOREGROUND to "
     "completion, then closed; nothing in flight"),
    ("ok-running-no-promise",
     "-Users-broomva-broomva/99878642-e84a-4e08-a982-925dffcd9ab2.jsonl",
     None, "allow", None, "a real maintenance-tick ending with a task still in flight and no wait "
     "promise in its closing paragraph"),
]

NOTE_RE = re.compile(r"<task-notification>(.*?)</task-notification>", re.S)


def _note(s):
    out = []
    for m in NOTE_RE.finditer(s or ""):
        tid = g._ID_RE.search(m.group(1))
        st = g._STATUS_RE.search(m.group(1))
        out.append("<task-notification>\n<task-id>%s</task-id>\n%s</task-notification>" % (
            tid.group(1) if tid else "?", ("<status>%s</status>\n" % st.group(1)) if st else ""))
    return "\n".join(out)


TUR_KEYS = ("backgroundTaskId", "agentId", "isAsync", "taskId", "timeoutMs", "status",
            "task_id", "task", "resumedAgentId", "success", "taskType", "workflowName")


# Operational detail in a KEPT paragraph that a public fixture does not need; the
# replacement keeps the sentence's shape (and its verdict).
SCRUB = [(re.compile(r"Railway and broomva\.tech are still logged out\."),
          "Two deploy CLIs are still logged out."),
         (re.compile(r"`~/\.cache/[^`]+`"), "`[path]`")]


def trim_final(text):
    """The final message reduced to the parts the matcher reads; see the docstring."""
    for rx, rep in SCRUB:
        text = rx.sub(rep, text or "")
    paras = re.split(r"\n\s*\n", text or "")
    body = [i for i, p in enumerate(paras) if p.strip()]
    while len(body) > 1 and g.ARC_STATUS_RE.search(paras[body[-1]]) and len(paras[body[-1]]) < 300:
        body.pop()
    last = body[-1] if body else -1
    out = []
    for i, p in enumerate(paras):
        keep = (i >= last or g.ARC_STATUS_RE.search(p) or g.ASK_HEAD_RE.search(p)
                or g.PROMISE_RE.search(g._prose(p)))
        out.append(p if keep or not p.strip() else "[redacted]")
    return "\n\n".join(out)


def redact(o, final):
    t = o.get("type")
    base = {"type": t, "uuid": o.get("uuid"), "entrypoint": o.get("entrypoint"),
            "isSidechain": bool(o.get("isSidechain"))}
    if t == "queue-operation":
        c = _note(o.get("content"))
        return dict(base, operation=o.get("operation"), content=c) if c else None
    if t == "attachment":
        a = o.get("attachment") or {}
        c = _note(a.get("prompt"))
        return dict(base, attachment={"type": a.get("type"), "prompt": c}) if c else None
    msg = o.get("message") if isinstance(o.get("message"), dict) else {}
    content = msg.get("content")
    if t == "assistant":
        blocks = []
        for b in content if isinstance(content, list) else []:
            if not isinstance(b, dict):
                continue
            if b.get("type") == "tool_use":
                inp = b.get("input") if isinstance(b.get("input"), dict) else {}
                keep = {k: inp[k] for k in ("run_in_background",) if k in inp}
                keep["description"] = "[redacted]"
                blocks.append({"type": "tool_use", "id": b.get("id"), "name": b.get("name"),
                               "input": keep})
            elif b.get("type") == "text" and final:
                blocks.append({"type": "text", "text": trim_final(b.get("text", ""))})
        if not blocks:
            return None
        return dict(base, message={"role": "assistant", "content": blocks})
    if t == "user":
        r = o.get("toolUseResult")
        if isinstance(content, list) and isinstance(r, dict):
            tr = [{"type": "tool_result", "tool_use_id": b.get("tool_use_id"),
                   "content": "[redacted]"}
                  for b in content if isinstance(b, dict) and b.get("type") == "tool_result"]
            kept = {k: r[k] for k in TUR_KEYS if k in r}
            if isinstance(kept.get("task"), dict):
                kept["task"] = {k: kept["task"].get(k) for k in ("task_id", "status")}
            if not tr or not kept:
                return None
            return dict(base, message={"role": "user", "content": tr}, toolUseResult=kept)
        text = content if isinstance(content, str) else " ".join(
            b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text") \
            if isinstance(content, list) else ""
        c = _note(text)
        return dict(base, message={"role": "user", "content": c}) if c else None
    return None


def last_ending_with_running_and_no_promise(path):
    """For the spec whose line is None: the last ending with a task in flight and no
    promise, so the fixture tests the matcher and not the ARC-STATUS rule."""
    best = None
    for rec in g.replay(path):
        if rec["running"] and not rec["promise"] and "ARC-STATUS" not in rec["text"]:
            best = rec["line"]
    return best


def stop_after(path, line):
    with open(path, encoding="utf-8", errors="replace") as f:
        for i, ln in enumerate(f, 1):
            if i > line and '"stop_hook_summary"' in ln:
                return i
    return None


def completes(o, tid):
    """True for any record that ends task tid: a notification carrying it, in any of
    the three places Claude Code writes one."""
    blob = json.dumps(o)
    return "<task-id>%s</task-id>" % tid in blob and "<status>" in blob


def build(projects):
    for name, rel, line, expect, drop, note in SPECS:
        src = os.path.join(projects, rel)
        if not os.path.isfile(src):
            print("skip %s: %s not found" % (name, rel))
            continue
        if line is None:
            line = last_ending_with_running_and_no_promise(src)
        stop = stop_after(src, line) if line else None
        if stop is None:
            print("skip %s: no qualifying ending" % name)
            continue
        rows = []
        with open(src, encoding="utf-8", errors="replace") as f:
            for i, ln in enumerate(f, 1):
                if i >= stop:
                    break
                try:
                    o = json.loads(ln)
                except ValueError:
                    continue
                if drop and completes(o, drop):
                    continue
                rows.append((i, o))
        final_i = max(i for i, o in rows if o.get("type") == "assistant" and not o.get("isSidechain"))
        out, ledger, text = [], g.TaskLedger(), ""
        for i, o in rows:
            ledger.feed(o)
            r = redact(o, final=(i == final_i))
            if r is not None:
                out.append(r)
            if i == final_i:
                text = trim_final(g.entry_text(o))
        with open(os.path.join(HERE, name + ".jsonl"), "w") as f:
            for r in out:
                f.write(json.dumps(r, sort_keys=True) + "\n")
        payload = {
            "hook_event_name": "Stop", "session_id": "fixture-" + name,
            "stop_hook_active": False, "last_assistant_message": text,
            "background_tasks": [{"id": t["id"], "type": t["type"], "status": "running",
                                  "description": "[redacted]"} for t in ledger.running()],
        }
        meta = {"expect": expect, "source": rel, "final_line": final_i, "stop_line": stop,
                "dropped_completions_of": drop,
                "note": note}
        with open(os.path.join(HERE, name + ".payload.json"), "w") as f:
            json.dump(payload, f, indent=1, sort_keys=True)
            f.write("\n")
        with open(os.path.join(HERE, name + ".meta.json"), "w") as f:
            json.dump(meta, f, indent=1, sort_keys=True)
            f.write("\n")
        print("%-34s %-5s entries=%-4d in-flight=%s" % (
            name, expect, len(out), [t["id"] for t in ledger.running()]))


if __name__ == "__main__":
    build(sys.argv[1] if len(sys.argv) > 1 else os.path.expanduser("~/.claude/projects"))
