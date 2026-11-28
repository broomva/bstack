#!/usr/bin/env bash
# tests/fleet-sensor.test.sh — BRO-3955 host-wide fleet scan: m6 by repo, agent wait time.
# Hermetic: synthetic Claude + Codex transcripts and throwaway git repos under $TMP.
# Every expected number below is computed by hand from the fixture timeline.
set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/.." && pwd)"
FLEET="$REPO/scripts/leverage-fleet-sensor.py"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

PASS=0; FAIL=0
ok(){ PASS=$((PASS+1)); echo "  ok   $1"; }
bad(){ FAIL=$((FAIL+1)); echo "  FAIL $1"; }
eq(){ if [ "$2" = "$3" ]; then ok "$1 ($2)"; else bad "$1 (got '$2' want '$3')"; fi; }

# Two repos with GitHub remotes: one product, one meta.
for r in prod meta; do
  mkdir -p "$TMP/$r/src" "$TMP/$r/docs"
  git -C "$TMP/$r" init -q && git -C "$TMP/$r" remote add origin "https://github.com/acme/$r.git"
done
CR="$TMP/claude"; XR="$TMP/codex"; EMPTY="$TMP/empty"; mkdir -p "$CR" "$XR" "$EMPTY"

python3 - "$TMP" <<'PY'
import json, os, sys, time
T = sys.argv[1]; now = time.time()
iso = lambda t: time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(t)) + ".000Z"
def W(path, rows, mtime):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        for r in rows: f.write(json.dumps(r) + "\n")
    os.utime(path, (mtime, mtime))
def human(t, u, cwd, text="SECRET-PROMPT-TEXT"):
    return {"type": "user", "uuid": u, "timestamp": iso(t), "cwd": cwd, "entrypoint": "sdk-ts",
            "message": {"content": text}}
def asst(t, u, cwd, tools=()):
    return {"type": "assistant", "uuid": u, "timestamp": iso(t), "cwd": cwd,
            "message": {"content": [{"type": "tool_use", "name": n, "input": i} for n, i in tools]}}
meta, prod = f"{T}/meta", f"{T}/prod"
# S1 — meta, interactive. Turns: 300 s, 120 s, 60 s. Gaps: 600 s, 7200 s (capped to 1800).
t0 = now - 3 * 3600
s1 = [human(t0, "a1", meta), asst(t0 + 60, "a2", meta, [("Edit", {"file_path": f"{meta}/docs/x.md"})]),
      asst(t0 + 300, "a3", meta),
      # a subagent line editing PRODUCT: must be ignored, or S1 would flip to product
      {"type": "assistant", "uuid": "a3s", "isSidechain": True, "timestamp": iso(t0 + 310), "cwd": meta,
       "message": {"content": [{"type": "tool_use", "name": "Write", "input": {"file_path": f"{prod}/src/z.ts"}}]}},
      human(t0 + 900, "a4", meta), asst(t0 + 1020, "a5", meta),
      human(t0 + 8220, "a6", meta), asst(t0 + 8280, "a7", meta)]
W(f"{T}/claude/-p-meta/s1.jsonl", s1, now - 7000)
# Resumed copy of S1 in a NEWER file: same uuids, must add nothing.
W(f"{T}/claude/-p-meta/s1-resumed.jsonl", s1, now - 60)
# S2 — product edit by tool, one prompt (not interactive).
W(f"{T}/claude/-p-prod/s2.jsonl", [human(now - 7200, "b1", prod),
   asst(now - 7100, "b2", prod, [("Write", {"file_path": f"{prod}/src/a.ts"})])], now - 7000)
# S3 — started in META, writes into PRODUCT through the shell -> product.
W(f"{T}/claude/-p-meta/s3.jsonl", [human(now - 5000, "c1", meta),
   asst(now - 4900, "c2", meta, [("Bash", {"command": f"echo hi > {prod}/src/b.ts"})])], now - 4800)
# S4 — a scripted `claude -p` run in META editing meta: not interactive, still editing.
W(f"{T}/claude/-p-meta/s4.jsonl", [dict(human(now - 4000, "d1", meta), entrypoint="sdk-cli"),
   asst(now - 3990, "d2", meta, [("Edit", {"file_path": f"{meta}/docs/y.md"})])], now - 3900)
# Codex — interactive, apply_patch into PRODUCT. Turns 100 s and 50 s, gap 300 s.
c0 = now - 10 * 3600
def ev(typ, **p): return {"timestamp": iso(now - 30), "type": "event_msg", "payload": dict(type=typ, **p)}
cx = [{"timestamp": iso(c0), "type": "session_meta", "payload": {"cwd": prod, "source": "vscode"}},
      ev("task_started", turn_id="t1", started_at=str(int(c0))),
      {"timestamp": iso(c0 + 10), "type": "response_item", "payload": {"type": "custom_tool_call",
       "input": "*** Begin Patch\n*** Update File: src/c.ts\n@@\n-a\n+b\n*** End Patch"}},
      ev("task_complete", turn_id="t1", started_at=str(int(c0)), completed_at=str(int(c0 + 100))),
      ev("task_started", turn_id="t2", started_at=str(int(c0 + 400))),
      ev("task_complete", turn_id="t2", started_at=str(int(c0 + 400)), completed_at=str(int(c0 + 450)))]
W(f"{T}/codex/2026/11/19/rollout-a.jsonl", cx, now - 600)
# A fork replays the same turns stamped with the replay time: must add nothing.
W(f"{T}/codex/2026/11/19/rollout-fork.jsonl", cx, now - 30)
PY

# Round 1 regressions, in their own roots so the hand-computed numbers above hold.
python3 - "$TMP" <<'PY'
import json, os, sys, time
T = sys.argv[1]; now = time.time()
iso = lambda t: time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(t)) + ".000Z"
def W(path, rows, mtime):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        for r in rows: f.write(json.dumps(r) + "\n")
    os.utime(path, (mtime, mtime))
meta = f"{T}/meta"
h = lambda t, u: {"type": "user", "uuid": u, "timestamp": iso(t), "cwd": meta, "entrypoint": "sdk-ts", "message": {"content": "x"}}
a = lambda t, u: {"type": "assistant", "uuid": u, "timestamp": iso(t), "cwd": meta, "message": {"content": []}}
# B1 — a turn 8 days ago, then two turns yesterday with a 600 s wait.
y = now - 86400
W(f"{T}/r1-boundary/-p/s.jsonl", [h(now - 8 * 86400, "o1"), a(now - 8 * 86400 + 60, "o2"),
  h(y, "o3"), a(y + 60, "o4"), h(y + 660, "o5"), a(y + 720, "o6")], now - 3600)
# B2 — ordering: the ORIGINAL (older mtime) has 2 turns; the resumed copy (newer)
# repeats them and adds a third. Newest-first gives the copy the whole session:
# 2 replies, the true count. Oldest-first would leave the copy one turn and lose
# the wait where the session resumed: 1 reply.
o = [h(y, "p1"), a(y + 60, "p2"), h(y + 660, "p3"), a(y + 720, "p4")]
W(f"{T}/r1-order/-p/orig.jsonl", o, now - 7200)
W(f"{T}/r1-order/-p/copy.jsonl", o + [h(y + 1320, "p5"), a(y + 1380, "p6")], now - 60)
# B3 — Codex: a human turn, then a turn another agent started (parent_turn_id).
c0 = now - 5 * 3600
ev = lambda typ, **p: {"timestamp": iso(now), "type": "event_msg", "payload": dict(type=typ, **p)}
W(f"{T}/r1-codex/s.jsonl", [{"timestamp": iso(c0), "type": "session_meta", "payload": {"cwd": meta, "source": "vscode"}},
  ev("task_started", turn_id="h1", started_at=str(int(c0))), ev("task_complete", turn_id="h1", completed_at=str(int(c0 + 50))),
  ev("task_started", turn_id="h2", started_at=str(int(c0 + 650))), ev("task_complete", turn_id="h2", completed_at=str(int(c0 + 700))),
  ev("task_started", turn_id="m1", started_at=str(int(c0 + 900)), turn_attribution={"parent_turn_id": "zz"}),
  ev("task_complete", turn_id="m1", completed_at=str(int(c0 + 950)))], now - 60)
PY

# Round 2 regressions.
python3 - "$TMP" <<'PY'
import json, os, sys, time
T = sys.argv[1]; now = time.time()
iso = lambda t: time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(t)) + ".000Z"
def W(path, rows, mtime):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        for r in rows: f.write(json.dumps(r) + "\n")
    os.utime(path, (mtime, mtime))
meta, prod = f"{T}/meta", f"{T}/prod"
h = lambda t, u, c="x": {"type": "user", "uuid": u, "timestamp": iso(t), "cwd": meta, "entrypoint": "sdk-ts", "message": {"content": c}}
a = lambda t, u, tools=(): {"type": "assistant", "uuid": u, "timestamp": iso(t), "cwd": meta,
     "message": {"content": [{"type": "tool_use", "name": n, "input": i} for n, i in tools]}}
# C1 — one session: a PRODUCT edit 8 days ago, a META edit yesterday. In a 7-day
# window it is meta; the old edit is outside the window.
y = now - 86400
W(f"{T}/r2-window/-p/s.jsonl", [h(now - 8 * 86400, "w1"), a(now - 8 * 86400 + 30, "w2", [("Write", {"file_path": f"{prod}/src/old.ts"})]),
  h(y, "w3"), a(y + 30, "w4", [("Edit", {"file_path": f"{meta}/docs/new.md"})])], now - 600)
# C2 — a task notification without turnOrigin is not a human reply.
W(f"{T}/r2-notify/-p/s.jsonl", [h(y, "n1"), a(y + 60, "n2"),
  h(y + 660, "n3", "<task-notification><task-id>x</task-id></task-notification>"), a(y + 720, "n4"),
  h(y + 1320, "n5"), a(y + 1380, "n6")], now - 600)
# C3 — Codex: a parent edits META; a subagent file starts with its OWN
# session_meta, replays the parent's meta, turn and patch, then runs one turn of
# its own (started by the parent, no edits).
c0 = now - 3 * 3600
ev = lambda typ, **p: {"timestamp": iso(now), "type": "event_msg", "payload": dict(type=typ, **p)}
patch_ = {"timestamp": iso(now), "type": "response_item", "payload": {"type": "custom_tool_call",
          "input": "*** Begin Patch\n*** Update File: docs/a.md\n@@\n-a\n+b\n*** End Patch"}}
# The parent has two human turns (interactive); it spawned the child during P1.
parent_head = [{"timestamp": iso(c0), "type": "session_meta", "payload": {"cwd": meta, "source": "vscode"}},
          ev("task_started", turn_id="P1", started_at=str(int(c0))), patch_,
          ev("task_complete", turn_id="P1", completed_at=str(int(c0 + 60)))]
W(f"{T}/r2-codex/parent.jsonl", parent_head + [
          ev("task_started", turn_id="P2", started_at=str(int(c0 + 660))),
          ev("task_complete", turn_id="P2", completed_at=str(int(c0 + 700)))], now - 7200)
# The child's own two turns carry no attribution (a thread a person can also
# message): only its own session_meta marks it a subagent.
child = ([{"timestamp": iso(c0 + 100), "type": "session_meta", "payload": {"cwd": meta, "source": {"subagent": {"thread_spawn": {}}}}}]
         + parent_head +
         [ev("task_started", turn_id="K1", started_at=str(int(c0 + 120))),
          ev("task_complete", turn_id="K1", completed_at=str(int(c0 + 180))),
          ev("task_started", turn_id="K2", started_at=str(int(c0 + 300))),
          ev("task_complete", turn_id="K2", completed_at=str(int(c0 + 360)))])
W(f"{T}/r2-codex/child.jsonl", child, now - 60)
# C4 — one Codex file with a malformed started_at must not stop the scan.
W(f"{T}/r2-bad/bad.jsonl", [{"timestamp": iso(c0), "type": "session_meta", "payload": {"cwd": meta, "source": "vscode"}},
  ev("task_started", turn_id="B1", started_at="not-a-number"), ev("task_complete", turn_id="B1", completed_at="also-bad")], now - 60)
W(f"{T}/r2-bad/good.jsonl", [{"timestamp": iso(c0), "type": "session_meta", "payload": {"cwd": meta, "source": "vscode"}},
  ev("task_started", turn_id="G1", started_at=str(int(c0))), patch_,
  ev("task_complete", turn_id="G1", completed_at=str(int(c0 + 60)))], now - 120)
PY

# Round 3 regressions.
python3 - "$TMP" <<'PY'
import json, os, sys, time
T = sys.argv[1]; now = time.time()
iso = lambda t: time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(t)) + ".000Z"
def W(path, rows, mtime):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        for r in rows: f.write(json.dumps(r) + "\n")
    os.utime(path, (mtime, mtime))
meta = f"{T}/meta"
ev = lambda t, typ, **p: {"timestamp": iso(t), "type": "event_msg", "payload": dict(type=typ, **p)}
pt = lambda t, f: {"timestamp": iso(t), "type": "response_item", "payload": {"type": "custom_tool_call",
                   "input": f"*** Begin Patch\n*** Update File: {f}\n@@\n-a\n+b\n*** End Patch"}}
# D1 — the REAL order: the parent is CREATED first but MODIFIED last (it records
# the end of its turn after the child returns). The child replays P1.
c0 = now - 3 * 3600
p_head = [{"timestamp": iso(c0), "type": "session_meta", "payload": {"cwd": meta, "source": "vscode"}},
          ev(c0, "task_started", turn_id="Q1", started_at=str(int(c0))), pt(c0 + 10, "docs/a.md"),
          ev(c0 + 60, "task_complete", turn_id="Q1", completed_at=str(int(c0 + 60)))]
W(f"{T}/r3-order/parent.jsonl", p_head + [ev(c0 + 660, "task_started", turn_id="Q2", started_at=str(int(c0 + 660))),
  ev(c0 + 700, "task_complete", turn_id="Q2", completed_at=str(int(c0 + 700)))], now - 30)
W(f"{T}/r3-order/child.jsonl",
  [{"timestamp": iso(c0 + 100), "type": "session_meta", "payload": {"cwd": meta, "source": {"subagent": {}}}}]
  + [dict(r, timestamp=iso(c0 + 100)) for r in p_head]
  + [ev(c0 + 120, "task_started", turn_id="L1", started_at=str(int(c0 + 120))),
     ev(c0 + 180, "task_complete", turn_id="L1", completed_at=str(int(c0 + 180)))], now - 3000)
# D2 — a Codex turn that began 8 days ago and edited yesterday counts this week.
s0, y = now - 8 * 86400, now - 86400
W(f"{T}/r3-long/s.jsonl", [{"timestamp": iso(s0), "type": "session_meta", "payload": {"cwd": meta, "source": "vscode"}},
  ev(s0, "task_started", turn_id="R1", started_at=str(int(s0))), pt(y, "docs/b.md"),
  ev(y + 60, "task_complete", turn_id="R1", completed_at=str(int(y + 60)))], now - 60)
PY

# Review regressions.
python3 - "$TMP" <<'PY'
import json, os, sys, time
T = sys.argv[1]; now = time.time()
iso = lambda t: time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(t)) + ".000Z"
def W(path, rows, mtime):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        for r in rows: f.write(json.dumps(r) + "\n")
    os.utime(path, (mtime, mtime))
meta, prod = f"{T}/meta", f"{T}/prod"
c0 = now - 3600
ev = lambda t, typ, **p: {"timestamp": iso(t), "type": "event_msg", "payload": dict(type=typ, **p)}
# E1 — session cwd is META; a shell call carries workdir=PROD and an apply_patch
# with a relative path: the edit lands in PROD.
call = {"timestamp": iso(c0 + 10), "type": "response_item", "payload": {"type": "function_call", "arguments": json.dumps(
    {"cmd": ["apply_patch", "*** Begin Patch\n*** Update File: src/app.py\n@@\n-a\n+b\n*** End Patch"], "workdir": prod})}}
W(f"{T}/rv-workdir/s.jsonl", [{"timestamp": iso(c0), "type": "session_meta", "payload": {"cwd": meta, "source": "vscode"}},
  ev(c0, "task_started", turn_id="W1", started_at=str(int(c0))), call,
  ev(c0 + 60, "task_complete", turn_id="W1", completed_at=str(int(c0 + 60)))], now - 60)
# E2 — one Claude session edits META and an EXTERNAL repo: not meta-only.
h = lambda t, u: {"type": "user", "uuid": u, "timestamp": iso(t), "cwd": meta, "entrypoint": "sdk-ts", "message": {"content": "x"}}
a = lambda t, u, fp: {"type": "assistant", "uuid": u, "timestamp": iso(t), "cwd": meta,
     "message": {"content": [{"type": "tool_use", "name": "Edit", "input": {"file_path": fp}}]}}
W(f"{T}/rv-mixed/-p/s.jsonl", [h(c0, "x1"), a(c0 + 10, "x2", f"{meta}/docs/a.md"), a(c0 + 20, "x3", f"{prod}/src/b.ts")], now - 60)
# E4 — Codex with NO working directory anywhere: an absolute META patch plus a
# relative patch that nothing can anchor.
c1 = now - 1800
W(f"{T}/rv-nocwd/s.jsonl", [{"timestamp": iso(c1), "type": "session_meta", "payload": {"source": "vscode"}},
  ev(c1, "task_started", turn_id="N1", started_at=str(int(c1))),
  {"timestamp": iso(c1 + 5), "type": "response_item", "payload": {"type": "custom_tool_call",
   "input": f"*** Begin Patch\n*** Update File: {meta}/docs/a.md\n@@\n-a\n+b\n*** Update File: src/x.ts\n@@\n-a\n+b\n*** End Patch"}},
  ev(c1 + 60, "task_complete", turn_id="N1", completed_at=str(int(c1 + 60)))], now - 60)
# E3 — META plus an edit into a clone that no longer exists (no repo resolvable).
W(f"{T}/rv-gone/-p/s.jsonl", [h(c0, "g1"), a(c0 + 10, "g2", f"{meta}/docs/a.md"), a(c0 + 20, "g3", f"{T}/gone-clone/src/x.ts")], now - 60)
PY

CFG="{\"product_repos\":[\"acme/prod\"],\"meta_repos\":[\"acme/meta\"],\"scratch_prefixes\":[]}"
run(){ python3 -I "$FLEET" --workspace "$TMP" --claude-root "$1" --codex-root "$2" --config-json "$3" --json --no-store; }
J="$(run "$CR" "$XR" "$CFG")"
get(){ python3 -c "import json,sys;d=json.load(sys.stdin);print(d$1)" <<<"$J"; }

echo "== m6 by the repo each edit lands in =="
eq "meta sessions (S1, S4)" "$(get "['raw']['meta_sessions']")" "2"
eq "product sessions (S2, S3 by shell, codex)" "$(get "['raw']['product_sessions']")" "3"
eq "m6 = 2/5" "$(get "['metrics']['m6_meta_work_session_ratio']")" "0.4"
eq "a shell write decided S3" "$(get "['raw']['shell_write_attributed_sessions']")" "1"
eq "tool-edits-only m6 = 2/4" "$(get "['raw']['m6_tool_edits_only']")" "0.5"
eq "subagent line did not flip S1; nothing unclassified" "$(get "['raw']['unclassified_sessions']")" "0"

echo "== wait time =="
# interactive: S1 (3 prompts) + codex (2 turns). S2/S3 have one prompt; S4 is sdk-cli.
eq "interactive sessions" "$(get "['raw']['interactive_sessions']")" "2"
eq "replies (resume copy and fork deduplicated)" "$(get "['raw']['human_replies']")" "3"
# active = 300+120+60+100+50 = 630 s; capped waits = 600+1800+300 = 2700 s
eq "w1 = 2700/3330" "$(get "['metrics']['w1_agent_idle_share']")" "0.8108"
# raw waits = 600+7200+300 = 8100 s -> 8100/8730
eq "uncapped idle share" "$(get "['raw']['idle_share_uncapped']")" "0.9278"
eq "w2 median reply = 600 s" "$(get "['metrics']['w2_reply_latency_p50_min']")" "10.0"
eq "replies after the 30-min cap" "$(get "['raw']['replies_after_cap']")" "1"
eq "w3 = 1 (the two sessions never wait at the same time)" "$(get "['metrics']['w3_waiting_sessions_mean']")" "1.0"

echo "== the inputs are actually read (each removal moves the result) =="
J="$(run "$CR" "$EMPTY" "$CFG")"
eq "without Codex: product drops to 2" "$(get "['raw']['product_sessions']")" "2"
eq "without Codex: 2 replies" "$(get "['raw']['human_replies']")" "2"
J="$(run "$EMPTY" "$XR" "$CFG")"
eq "without Claude: m6 = 0/1" "$(get "['metrics']['m6_meta_work_session_ratio']")" "0.0"

echo "== fail closed without repo lists =="
J="$(run "$CR" "$XR" '{"scratch_prefixes":[]}')"
eq "m6 null when unconfigured" "$(get "['metrics']['m6_meta_work_session_ratio']")" "None"
grep -q "not both configured" <<<"$J" && ok "says why" || bad "no note on missing config"
eq "wait time still measured" "$(get "['metrics']['w1_agent_idle_share']")" "0.8108"

echo "== external repos sit on neither side of m6 =="
J="$(run "$CR" "$XR" '{"product_repos":["acme/x"],"meta_repos":["acme/meta"],"external_repos":["acme/prod"],"scratch_prefixes":[]}')"
eq "prod-repo sessions counted as external" "$(get "['raw']['external_sessions']")" "3"
eq "and not as unclassified" "$(get "['raw']['unclassified_sessions']")" "0"
eq "m6 has no product side -> 2/2" "$(get "['metrics']['m6_meta_work_session_ratio']")" "1.0"

echo "== scratch sessions are excluded =="
J="$(run "$CR" "$XR" "{\"product_repos\":[\"acme/prod\"],\"meta_repos\":[\"acme/meta\"],\"scratch_prefixes\":[\"$TMP/meta\"]}")"
eq "sessions started in a scratch prefix leave m6" "$(get "['raw']['meta_sessions']")" "0"

echo "== round 1: window boundary, file order, Codex machine turns, prefix match =="
J="$(run "$TMP/r1-boundary" "$EMPTY" "$CFG")"
eq "a turn before the window adds no reply" "$(get "['raw']['human_replies']")" "1"
eq "w2 is the in-window wait (10 min), not 8 days" "$(get "['metrics']['w2_reply_latency_p50_min']")" "10.0"
eq "w3 still computes" "$(get "['metrics']['w3_waiting_sessions_mean']")" "1.0"
J="$(run "$TMP/r1-order" "$EMPTY" "$CFG")"
eq "original + longer resumed copy -> 2 replies (newest-first)" "$(get "['raw']['human_replies']")" "2"
J="$(run "$EMPTY" "$TMP/r1-codex" "$CFG")"
eq "a turn another agent started is not a human reply" "$(get "['raw']['human_replies']")" "1"
J="$(run "$CR" "$XR" "{\"product_repos\":[\"acme/prod\"],\"meta_repos\":[\"acme/meta\"],\"scratch_prefixes\":[\"$TMP/me\"]}")"
eq "a scratch prefix matches whole path segments only" "$(get "['raw']['meta_sessions']")" "2"

echo "== round 2: edits are windowed, subagent replays add nothing, one bad line is one bad line =="
J="$(run "$TMP/r2-window" "$EMPTY" "$CFG")"
eq "a product edit 8 days ago does not make this week's session product" "$(get "['raw']['product_sessions']")" "0"
eq "the in-window meta edit classifies it" "$(get "['raw']['meta_sessions']")" "1"
J="$(run "$TMP/r2-notify" "$EMPTY" "$CFG")"
eq "a task notification without turnOrigin is not a human reply" "$(get "['raw']['human_replies']")" "1"
J="$(run "$EMPTY" "$TMP/r2-codex" "$CFG")"
eq "parent + replaying subagent = one editing session" "$(get "['raw']['editing_sessions']")" "1"
eq "and it is meta" "$(get "['raw']['meta_sessions']")" "1"
eq "the parent keeps its human turns; the subagent is not interactive" "$(get "['raw']['interactive_sessions']")" "1"
eq "the parent's one reply (P1 -> P2) is counted" "$(get "['raw']['human_replies']")" "1"
J="$(run "$EMPTY" "$TMP/r2-bad" "$CFG")"
eq "a malformed Codex timestamp does not abort the scan" "$(get "['raw']['meta_sessions']")" "1"
R="$(python3 -I - "$FLEET" "$TMP/meta" <<'PY'
import importlib.util, sys
spec = importlib.util.spec_from_file_location("f", sys.argv[1]); f = importlib.util.module_from_spec(spec); spec.loader.exec_module(f)
print(sorted(f.shell_write_repos("awk '$3 > 0.5' data.tsv", sys.argv[2])), sorted(f.shell_write_repos("cut -f1 a > out.tsv", sys.argv[2])))
PY
)"
eq "'> 0.5' is a comparison, '> out.tsv' is a write" "$R" "[] ['acme/meta']"

echo "== round 3: Codex creation order, edits keep their own time =="
J="$(run "$EMPTY" "$TMP/r3-order" "$CFG")"
eq "parent modified AFTER its subagent: still one editing session" "$(get "['raw']['editing_sessions']")" "1"
eq "and the parent keeps its reply" "$(get "['raw']['human_replies']")" "1"
J="$(run "$EMPTY" "$TMP/r3-long" "$CFG")"
eq "an in-window edit in a turn that began before the window counts" "$(get "['raw']['meta_sessions']")" "1"

echo "== review: the call's workdir, and meta means only meta =="
J="$(run "$EMPTY" "$TMP/rv-workdir" "$CFG")"
eq "a relative patch path resolves against the call's workdir" "$(get "['raw']['product_sessions']")" "1"
J="$(run "$TMP/rv-mixed" "$EMPTY" '{"product_repos":["acme/x"],"meta_repos":["acme/meta"],"external_repos":["acme/prod"],"scratch_prefixes":[]}')"
eq "meta + external is not meta-only" "$(get "['raw']['meta_sessions']")" "0"
eq "it is external" "$(get "['raw']['external_sessions']")" "1"
eq "so m6 has no reading" "$(get "['metrics']['m6_meta_work_session_ratio']")" "None"
J="$(run "$TMP/rv-gone" "$EMPTY" "$CFG")"
eq "meta + an edit in a vanished clone is not meta-only" "$(get "['raw']['meta_sessions']")" "0"
eq "it is unclassified as <unresolved>" "$(get "['raw']['unclassified_repos'].get('<unresolved>')")" "1"
J="$(run "$TMP/rv-gone" "$EMPTY" "{\"product_repos\":[\"acme/prod\"],\"meta_repos\":[\"acme/meta\"],\"scratch_prefixes\":[\"$TMP/gone-clone\"]}")"
eq "the same edit under a scratch prefix is ignored -> meta" "$(get "['raw']['meta_sessions']")" "1"
J="$(run "$EMPTY" "$TMP/rv-nocwd" "$CFG")"
eq "a relative patch with no working directory is unresolved, not dropped" "$(get "['raw']['meta_sessions']")" "0"
eq "so the session is unclassified" "$(get "['raw']['unclassified_sessions']")" "1"

echo "== round 5: unresolved shell writes are kept, not dropped =="
# Place default-scratch test repo outside /tmp even when the repo checkout itself
# lives under /tmp (e.g. disposable review clones in /tmp).
NONTMP_ROOT="$(mktemp -d "${HOME}/.bstack-fleet-test.XXXXXX")"
trap 'rm -rf "$TMP" "$NONTMP_ROOT"' EXIT
NONTMP_META="$NONTMP_ROOT/meta"
git init -q "$NONTMP_META"
git -C "$NONTMP_META" remote add origin "https://github.com/acme/meta.git"
mkdir -p "$TMP/r5-shell/p" "$TMP/r5-shell-scratch/p"
python3 - "$TMP/r5-shell/p/s.jsonl" "$TMP/r5-shell-scratch/p/s.jsonl" "$TMP/meta" "$TMP/gone-product" "$NONTMP_META" <<'PY'
import json, sys, time
from datetime import datetime, timezone
p_gone, p_tmp, meta, gone, nontmp_meta = sys.argv[1:6]
t0 = datetime.fromtimestamp(time.time() - 600, timezone.utc).isoformat()
t1 = datetime.fromtimestamp(time.time() - 540, timezone.utc).isoformat()
def write_s(path, cwd, cmd):
    rows = [
        {"type": "assistant", "timestamp": t0, "cwd": cwd, "message": {"content": [
            {"type": "tool_use", "name": "Edit", "input": {"file_path": f"{cwd}/a.md"}},
        ]}},
        {"type": "assistant", "timestamp": t1, "cwd": cwd, "message": {"content": [
            {"type": "tool_use", "name": "Bash", "input": {"command": cmd}},
        ]}},
    ]
    with open(path, "w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
write_s(p_gone, meta, f"echo hi > {gone}/src/app.py")
write_s(p_tmp, nontmp_meta, "echo hi > /tmp/throwaway.log")
PY
J="$(run "$TMP/r5-shell" "$EMPTY" "$CFG")"
eq "meta Edit + shell redirect into vanished clone is not meta-only" "$(get "['raw']['meta_sessions']")" "0"
eq "it is unclassified" "$(get "['raw']['unclassified_sessions']")" "1"
eq "and counted under <unresolved>" "$(get "['raw']['unclassified_repos'].get('<unresolved>')")" "1"
eq "so m6 is None rather than 1.0" "$(get "['metrics']['m6_meta_work_session_ratio']")" "None"
J="$(run "$TMP/r5-shell" "$EMPTY" "{\"product_repos\":[\"acme/prod\"],\"meta_repos\":[\"acme/meta\"],\"scratch_prefixes\":[\"$TMP/gone-product\"]}")"
eq "the same shell write under scratch_prefixes is ignored -> meta" "$(get "['raw']['meta_sessions']")" "1"
CFG_DEF='{"product_repos":["acme/prod"],"meta_repos":["acme/meta"]}'
J="$(run "$TMP/r5-shell-scratch" "$EMPTY" "$CFG_DEF")"
eq "meta Edit + shell redirect to /tmp stays meta-only under default scratch_prefixes" "$(get "['raw']['meta_sessions']")" "1"
eq "and has zero unclassified sessions" "$(get "['raw']['unclassified_sessions']")" "0"
R="$(python3 -I - "$FLEET" <<'PY'
import importlib.util, sys
spec = importlib.util.spec_from_file_location("f", sys.argv[1]); f = importlib.util.module_from_spec(spec); spec.loader.exec_module(f)
print(sorted(f.shell_write_repos("echo hi > out.py", None)))
PY
)"
eq "relative shell write with no cwd is unresolved" "$R" "['?out.py']"

echo "== round 6 & 7: shell write variants, multi-target tee/sed, cd, var targets, [ab] literal pathspec, symlinks, malformed config =="
# Add tracked file `a` in $TMP/meta to test that missing `[ab]/x` does NOT glob-match `a`
echo "hi" > "$TMP/meta/a"
git -C "$TMP/meta" add a
R6_SHELL="$(python3 -I - "$FLEET" "$TMP/meta" "$TMP/prod" "$TMP/gone-product" <<'PY'
import importlib.util, os, sys
sp = importlib.util.spec_from_file_location("f", sys.argv[1]); m = importlib.util.module_from_spec(sp); sp.loader.exec_module(m)
meta, prod, gone = sys.argv[2:5]
c1 = sorted(m.shell_write_repos(f'echo hi 1> "{gone}/a.py"', meta))
c2 = sorted(m.shell_write_repos(f"echo hi | tee -- '{meta}/README' '{prod}/README'", meta))
c3 = sorted(m.shell_write_repos(f"sed -i '' 's/a/b/' \"{meta}/README\" \"{prod}/README\"", meta))
c4 = sorted(m.shell_write_repos("echo hi > README", None))
c5 = m.edit_repo(f"{meta}/vanished-nested-prod/src/app.py", meta)
c6 = sorted(m.shell_write_repos(f"echo hi>{prod}/README", meta))
c7 = sorted(m.shell_write_repos(f"cd {prod} && echo hi > README", meta))
c8 = sorted(m.shell_write_repos('echo hi > "my notes.md"', None))
c9 = sorted(m.shell_write_repos('echo hi > "$TARGET"', meta))
c10 = sorted(m.shell_write_repos("echo x | tee a b", "/not-existing"))
c11 = m.edit_repo(f"{meta}/[ab]/x.py", meta)
print(
    c1 == [f"?{os.path.normpath(gone + '/a.py')}"],
    c2 == ["acme/meta", "acme/prod"],
    c3 == ["acme/meta", "acme/prod"],
    c4 == ["?README"],
    c5.startswith("?"),
    c6 == ["acme/prod"],
    c7 == ["acme/prod"],
    c8 == ["?my notes.md"],
    c9 == ["?$TARGET"],
    c10 == ["?/not-existing/a", "?/not-existing/b"],
    c11.startswith("?"),
)
PY
)"
eq "all shell write variants (unspaced >, multi-target tee/sed, cd, quoted spaces, var target, literal [ab]) resolve accurately" "$R6_SHELL" "True True True True True True True True True True True"

mkdir -p "$TMP/r6-unreadable/p"
cp "$TMP/r5-shell-scratch/p/s.jsonl" "$TMP/r6-unreadable/p/meta.jsonl"
echo '{}' > "$TMP/r6-unreadable/p/secret.jsonl"
chmod 000 "$TMP/r6-unreadable/p/secret.jsonl"
J="$(run "$TMP/r6-unreadable" "$EMPTY" "$CFG_DEF")"
chmod 644 "$TMP/r6-unreadable/p/secret.jsonl"
eq "unreadable transcript fails closed on m6 -> None" "$(get "['metrics']['m6_meta_work_session_ratio']")" "None"
eq "unreadable transcript count recorded" "$(get "['raw']['unreadable_transcripts']['claude']")" "1"

# 7a) Symlinked unreadable Codex transcript into chmod 000 dir fails closed; readable symlinked dir is discovered
mkdir -p "$TMP/r7-codex-sym" "$TMP/r7-hidden-dir" "$TMP/r7-linked-dir"
cp "$XR/2026/11/19/rollout-a.jsonl" "$TMP/r7-linked-dir/linked.jsonl"
echo '{}' > "$TMP/r7-hidden-dir/secret.jsonl"
ln -s "$TMP/r7-linked-dir" "$TMP/r7-codex-sym/linked-sub"
J="$(run "$EMPTY" "$TMP/r7-codex-sym" "$CFG")"
eq "readable nested symlinked Codex dir is discovered" "$(get "['raw']['transcripts_read']['codex']")" "1"
chmod 000 "$TMP/r7-hidden-dir"
ln -s "$TMP/r7-hidden-dir/secret.jsonl" "$TMP/r7-codex-sym/hidden.jsonl"
J="$(run "$TMP/r5-shell-scratch" "$TMP/r7-codex-sym" "$CFG")"
chmod 755 "$TMP/r7-hidden-dir"
eq "symlinked unreadable Codex transcript fails closed -> m6 None" "$(get "['metrics']['m6_meta_work_session_ratio']")" "None"
eq "unreadable Codex symlink counted" "$(get "['raw']['unreadable_transcripts']['codex']")" "1"

# 7b) scratch_prefixes: [""] matches [] in both behavior and signature; malformed scratch_prefixes: 42 degrades cleanly
J_EMPTY_STR="$(run "$TMP/r5-shell-scratch" "$EMPTY" '{"product_repos":["acme/prod"],"meta_repos":["acme/meta"],"scratch_prefixes":[""]}')"
J_EMPTY_ARR="$(run "$TMP/r5-shell-scratch" "$EMPTY" '{"product_repos":["acme/prod"],"meta_repos":["acme/meta"],"scratch_prefixes":[]}')"
R7_SP="$(python3 -I - "$J_EMPTY_STR" "$J_EMPTY_ARR" <<'PY'
import json, sys
a, b = json.loads(sys.argv[1]), json.loads(sys.argv[2])
print(a["config"] == b["config"], a["raw"]["meta_sessions"] == b["raw"]["meta_sessions"], a["raw"]["unclassified_sessions"] == b["raw"]["unclassified_sessions"])
PY
)"
eq "scratch_prefixes:[''] behaves and signs identically to []" "$R7_SP" "True True True"
run "$EMPTY" "$EMPTY" '{"scratch_prefixes":42}' >/dev/null && ok "malformed scratch_prefixes: 42 degrades cleanly" || bad "scratch_prefixes: 42 crashed"

echo "== round 8: shell scope, redirections, per-row cwd, median, and dangling roots =="
R8="$(python3 -I - "$FLEET" "$TMP" <<'PY'
import importlib.util, json, os, shlex, sys, time
from datetime import datetime, timezone
from pathlib import Path

spec = importlib.util.spec_from_file_location("fleet_sensor", sys.argv[1])
sensor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sensor)
root = Path(sys.argv[2])
meta, prod = root / "meta", root / "prod"
quoted_meta = shlex.quote(str(meta))
cases = [
    (f"(cd {quoted_meta} && echo hi > README); echo hi > README", {"acme/meta", "acme/prod"}),
    (f"(cd {quoted_meta}); echo hi > README", {"acme/prod"}),
    (f"cd {quoted_meta} > README", {"acme/prod"}),
    (f"cd {quoted_meta} | tee README", {"acme/prod"}),
    ("echo hi >| README", {"acme/prod"}),
    ("echo hi 2>README 3>>other.txt", {"acme/prod"}),
    ("echo hi 2>&1", set()),
    (f"cat > {quoted_meta}/README <<'END'\necho > README\nEND", {"acme/meta"}),
    (f"cat <<-'END' > {quoted_meta}/README\n\techo > README\n\tEND\necho > README", {"acme/meta", "acme/prod"}),
]
for cmd, expected in cases:
    assert sensor.shell_write_repos(cmd, str(prod)) == expected, cmd

now = time.time()
project = root / "r8-claude" / "p"
project.mkdir(parents=True)
path = project / "session.jsonl"
def row(offset, kind, cwd, content):
    ts = datetime.fromtimestamp(now + offset, timezone.utc).isoformat()
    return {"timestamp": ts, "type": kind, "cwd": str(cwd), "message": {"content": content}}
def tool(name, **args):
    return {"type": "tool_use", "name": name, "input": args}
rows = [
    row(-660, "user", meta, "edit"),
    row(-650, "assistant", meta, [tool("Edit", file_path="README")]),
    row(-590, "user", meta, "continue"),
    row(-580, "assistant", prod, [tool("Bash", command="echo hi > README")]),
    row(-40, "user", prod, "finish"),
    row(-30, "assistant", prod, []),
]
path.write_text("".join(json.dumps(r) + "\n" for r in rows))
cfg = {"meta_repos": ["acme/meta"], "product_repos": ["acme/prod"], "scratch_prefixes": []}
result = sensor.measure(cfg, 7, str(project.parent), None, now)
assert result["raw"]["product_sessions"] == 1, result["raw"]
assert result["metrics"]["w2_reply_latency_p50_min"] == 5.0, result["metrics"]

broken = project.parent / "broken"
broken.symlink_to(root / "absent")
assert sensor._collect_jsonl(str(broken), False) == ([], 1)
assert sensor._collect_jsonl(str(broken), True) == ([], 1)
result = sensor.measure(cfg, 7, str(project.parent), None, now)
assert result["raw"]["unreadable_transcripts"]["claude"] == 1, result["raw"]
assert result["metrics"]["m6_meta_work_session_ratio"] is None, result["metrics"]
print("ok")
PY
)"
eq "shell writes, Claude row cwd, even median, and dangling discovery roots hold" "$R8" "ok"

echo "== state file: written atomically, no transcript content =="
# shellcheck source=scripts/lib/broomva-paths.sh
. "$REPO/scripts/lib/broomva-paths.sh"
export BROOMVA_HOME="$TMP/broomva-home"
python3 -I "$FLEET" --workspace "$TMP" --claude-root "$CR" --codex-root "$XR" --config-json "$CFG" >/dev/null
S="$(broomva_repo_path leverage_fleet_state "$TMP")"
[ -f "$S" ] && ok "state written" || bad "no state file"
tail -c1 "$S" | od -An -c | grep -q '\\n' && ok "trailing newline" || bad "no trailing newline"
! grep -q "SECRET-PROMPT-TEXT" "$S" && ok "no prompt text in state" || bad "prompt text leaked into state"
before="$(stat -c %Y "$S" 2>/dev/null || stat -f %m "$S")"; sleep 1
python3 -I "$FLEET" --workspace "$TMP" --claude-root "$CR" --codex-root "$XR" --config-json "$CFG" --throttle 3600 >/dev/null
after="$(stat -c %Y "$S" 2>/dev/null || stat -f %m "$S")"
eq "throttle skips a fresh state" "$after" "$before"
CFG2='{"product_repos":["acme/prod","acme/other"],"meta_repos":["acme/meta"],"scratch_prefixes":[]}'
sleep 1
python3 -I "$FLEET" --workspace "$TMP" --claude-root "$CR" --codex-root "$XR" --config-json "$CFG2" --throttle 3600 >/dev/null
after_cfg="$(stat -c %Y "$S" 2>/dev/null || stat -f %m "$S")"
[ "$after_cfg" != "$before" ] && ok "throttle recomputes when config changes" || bad "throttle kept stale config"
J="$(cat "$S")"
eq "updated state records the new product_repos config" "$(get "['config']['product_repos']")" "['acme/other', 'acme/prod']"

echo "fleet-sensor: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
