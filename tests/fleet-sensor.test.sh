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

echo "== state file: written atomically, no transcript content =="
python3 -I "$FLEET" --workspace "$TMP" --claude-root "$CR" --codex-root "$XR" --config-json "$CFG" >/dev/null
S="$TMP/.control/leverage-fleet-state.json"
[ -f "$S" ] && ok "state written" || bad "no state file"
tail -c1 "$S" | od -An -c | grep -q '\\n' && ok "trailing newline" || bad "no trailing newline"
! grep -q "SECRET-PROMPT-TEXT" "$S" && ok "no prompt text in state" || bad "prompt text leaked into state"
before="$(stat -c %Y "$S" 2>/dev/null || stat -f %m "$S")"; sleep 1
python3 -I "$FLEET" --workspace "$TMP" --claude-root "$CR" --codex-root "$XR" --config-json "$CFG" --throttle 3600 >/dev/null
after="$(stat -c %Y "$S" 2>/dev/null || stat -f %m "$S")"
eq "throttle skips a fresh state" "$after" "$before"

echo "fleet-sensor: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
