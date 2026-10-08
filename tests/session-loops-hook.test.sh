#!/usr/bin/env bash
# tests/session-loops-hook.test.sh — BRO-2932 session-loop hook (loop layer P3).
#
# hooks/session-loops-hook.py appends a session's crons to
# <BROOMVA_HOME>/ledger/loops/session.jsonl. The payloads in
# tests/fixtures/session-loops/ were captured from a real interactive session on
# Claude Code 2.1.280 (CronCreate, one cron fire, CronDelete, /exit), with only
# the home directory renamed. Every case runs against a temp BROOMVA_HOME and a
# temp HOME; the operator's real ledger is never touched.
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HOOK="$REPO/hooks/session-loops-hook.py"
WRITER="$REPO/scripts/broomva_home.py"
FX="$REPO/tests/fixtures/session-loops"
PASS=0; FAIL=0
pass() { echo "  [ok] $1"; PASS=$((PASS + 1)); }
fail() { echo "  [FAIL] $1"; FAIL=$((FAIL + 1)); }
check() { if "${@:2}"; then pass "$1"; else fail "$1"; fi; }

echo "── session-loops-hook (BRO-2932) ───────────────────────────────────"
command -v python3 >/dev/null 2>&1 || { echo "  [FAIL] python3 not available"; exit 1; }
[ -f "$HOOK" ] || { echo "  [FAIL] missing $HOOK"; exit 1; }

T="$(mktemp -d)"; trap 'rm -rf "$T"' EXIT
T="$(cd "$T" && pwd -P)"
mkdir -p "$T/neutral" "$T/home"
# The ambient session must not leak its own ids or homes into a case.
unset PASEO_AGENT_ID CLAUDE_PID BROOMVA_HOME BROOMVA_WORKSPACE BSTACK_STATE_DIR CLAUDE_PLUGIN_ROOT

# hook HOMEDIR FIXTURE-OR-JSON [VAR=value ...] — one event; prints the exit code
hook() {
    local bh="$1" input="$2"; shift 2
    [ -f "$input" ] && input="$(cat "$input")"
    ( cd "$T/neutral" && printf '%s' "$input" | env HOME="$T/home" BROOMVA_HOME="$bh" "$@" \
        python3 -I "$HOOK" 2>> "$bh.stderr"; echo "$?" )
}
stream() { echo "$1/ledger/loops/session.jsonl"; }
lines() { if [ -f "$(stream "$1")" ]; then wc -l < "$(stream "$1")" | tr -d ' '; else echo 0; fi; }
types() { python3 -I -c 'import json,sys; print(" ".join(json.loads(l)["type"].split(".", 1)[1] for l in open(sys.argv[1])))' "$(stream "$1")"; }
verify() { ( cd "$T/neutral" && BROOMVA_HOME="$1" python3 -I "$WRITER" verify loops/session >/dev/null 2>&1 ); }
# jq-free field read: field PATH-INSIDE-EVENT over line N (1-based)
field() { python3 -I -c '
import json, sys
e = json.loads(open(sys.argv[1]).read().splitlines()[int(sys.argv[2]) - 1])
for k in sys.argv[3].split("."):
    e = e.get(k) if isinstance(e, dict) else None
print(json.dumps(e))' "$(stream "$1")" "$2" "$3"; }
with() {  # with FIXTURE KEY=JSON ... — the fixture with top-level keys replaced
    python3 -I -c '
import json, sys
d = json.load(open(sys.argv[1]))
for kv in sys.argv[2:]:
    k, v = kv.split("=", 1)
    d[k] = json.loads(v)
print(json.dumps(d))' "$@"
}

# ── 1. the captured session, replayed in order ───────────────────────────────
H1="$T/h1"
rcs=""
for f in "$FX"/*.json; do rcs="$rcs$(hook "$H1" "$f" PASEO_AGENT_ID=agent-1 CLAUDE_PID=4242)"; done
check "every captured event exits 0" [ "$rcs" = "000000000" ]
check "the stream reads: created, snapshot, run, deleted, snapshot, ended" \
    [ "$(types "$H1")" = "session.cron_created session.snapshot run.started session.cron_deleted session.snapshot session.ended" ]
check "broomva_home.py verify prints ok over the stream" verify "$H1"
check "cron_created comes from the PostToolUse (via CronCreate)" [ "$(field "$H1" 1 data.via)" = '"CronCreate"' ]
check "the subject is loop:cc/<session>/<cron id>" \
    [ "$(field "$H1" 1 subject)" = '"loop:cc/53305082-37ff-47ed-b9aa-1b33ec806891/25b1e2a5"' ]
check "refs name the session and the agent" \
    [ "$(field "$H1" 1 refs)" = '{"agent": "agent:agent-1", "session": "session:53305082-37ff-47ed-b9aa-1b33ec806891"}' ]
check "the actor is the Paseo agent" [ "$(field "$H1" 1 actor)" = '"agent:agent-1"' ]
check "data carries the pid" [ "$(field "$H1" 1 data.pid)" = 4242 ]
check "data carries the cron and recurring" \
    [ "$(field "$H1" 1 data.cron)$(field "$H1" 1 data.recurring)" = '"* * * * *"true' ]
check "the cron fire is loop.run.started on the cron's subject" \
    [ "$(field "$H1" 3 subject)" = "$(field "$H1" 1 subject)" ]
check "the run names its cron_created as cause" [ "$(field "$H1" 3 cause)" = "$(field "$H1" 1 id)" ]
check "cron_deleted comes from the PostToolUse (via CronDelete)" [ "$(field "$H1" 4 data.via)" = '"CronDelete"' ]
check "the empty snapshot says count 0" [ "$(field "$H1" 5 data.count)" = 0 ]
check "SessionEnd records its reason" [ "$(field "$H1" 6 data.reason)" = '"prompt_input_exit"' ]
check "SessionEnd removes the session's cache" [ ! -e "$H1/cache/session-loops/53305082-37ff-47ed-b9aa-1b33ec806891.json" ]
check "nothing went to stderr" [ ! -s "$H1.stderr" ]

# ── 2. a session that never had a cron writes nothing, keeps no cache ────────
H2="$T/h2"
for f in 01-session-start 02-prompt-plain 08-stop-no-crons 09-session-end; do hook "$H2" "$FX/$f.json" >/dev/null; done
check "no cron, no stream" [ ! -e "$(stream "$H2")" ]
check "no cron, no cache file" [ ! -e "$H2/cache" ]

# ── 3. Stop alone (no PostToolUse seen): the snapshot diff finds the cron ────
H3="$T/h3"
hook "$H3" "$FX/04-stop-one-cron.json" >/dev/null
hook "$H3" "$FX/04-stop-one-cron.json" >/dev/null
hook "$H3" "$FX/04-stop-one-cron.json" >/dev/null
check "three identical Stops write once: created + snapshot" [ "$(types "$H3")" = "session.cron_created session.snapshot" ]
check "found by the diff: via snapshot" [ "$(field "$H3" 1 data.via)" = '"snapshot"' ]
check "first sight of the session: createdAt is a lower bound" [ "$(field "$H3" 1 data.created_at_lower_bound)" = true ]
check "no agent id: the actor is the hook" [ "$(field "$H3" 1 actor)" = '"hook:session-loops"' ]
check "no agent id: refs carry the session only" [ "$(field "$H3" 1 refs)" = '{"session": "session:53305082-37ff-47ed-b9aa-1b33ec806891"}' ]
hook "$H3" "$FX/02-prompt-plain.json" >/dev/null
check "a prompt that is not the cron's records no run" [ "$(lines "$H3")" = 2 ]
hook "$H3" "$FX/05-prompt-cron-fire.json" >/dev/null
check "the cron's prompt records a run" [ "$(types "$H3" | awk '{print $NF}')" = "run.started" ]
hook "$H3" "$FX/08-stop-no-crons.json" >/dev/null
check "a cron gone from the snapshot is deleted via snapshot" \
    [ "$(field "$H3" 4 type)$(field "$H3" 4 data.via)" = '"loop.session.cron_deleted""snapshot"' ]
hook "$H3" "$FX/08-stop-no-crons.json" >/dev/null
check "a second empty Stop writes nothing" [ "$(lines "$H3")" = 5 ]
check "verify ok" verify "$H3"

# A cron created mid-session (the cache already exists) is not a lower bound.
hook "$H3" "$(with "$FX/04-stop-one-cron.json" 'session_crons=[{"id":"abc12345","schedule":"*/5 * * * *","recurring":true,"prompt":"second"}]')" >/dev/null
check "a cron first seen mid-session is not marked a lower bound" \
    [ "$(field "$H3" 6 type)$(field "$H3" 6 data.created_at_lower_bound)" = '"loop.session.cron_created"null' ]

# ── 4. secrets: only a digest and a scrubbed 120-char head ever land ─────────
H4="$T/h4"
SECRET="ghp_$(printf 'a%.0s' $(seq 1 36))"
LONG="tick: $(printf 'x%.0s' $(seq 1 300))"
hook "$H4" "$(with "$FX/04-stop-one-cron.json" "session_crons=[{\"id\":\"s1\",\"schedule\":\"* * * * *\",\"recurring\":true,\"prompt\":\"deploy with $SECRET now\"},{\"id\":\"s2\",\"schedule\":\"* * * * *\",\"recurring\":true,\"prompt\":\"$LONG\"}]")" >/dev/null
check "a secret in a prompt never reaches the stream" sh -c "! grep -q '$SECRET' '$(stream "$H4")'"
check "the prompt head holding it is redacted whole" [ "$(field "$H4" 1 data.prompt_head)" = '"[redacted]"' ]
check "a long prompt is cut to 120 characters" [ "$(field "$H4" 2 data.prompt_head | tr -d '"' | wc -c | tr -d ' ')" = 121 ]
CUT="$(printf 'y%.0s' $(seq 1 100))"
hook "$H4" "$(with "$FX/04-stop-one-cron.json" "session_crons=[{\"id\":\"s3\",\"schedule\":\"* * * * *\",\"recurring\":true,\"prompt\":\"$CUT $SECRET\"}]")" >/dev/null
check "a secret cut by the 120-character head is still redacted" \
    [ "$(field "$H4" 4 data.via)$(field "$H4" 4 data.prompt_head)" = '"snapshot""[redacted]"' ]
check "no fragment of it lands either" sh -c "! grep -q 'ghp_aaaa' '$(stream "$H4")'"
check "the cache keeps no prompt text" sh -c "! grep -q 'tick: xxx' '$H4/cache/session-loops/'*.json"

# ── 5. Stimulus / SRI: scope sri, no prompt head ─────────────────────────────
H5="$T/h5"
mkdir -p "$T/home/conductor/workspaces/sri/w1"
hook "$H5" "$(with "$FX/04-stop-one-cron.json" "cwd=\"$T/home/conductor/workspaces/sri/w1\"")" >/dev/null
check "under an SRI root: scope sri" [ "$(field "$H5" 1 data.scope)" = '"sri"' ]
check "under an SRI root: no prompt_head" [ "$(field "$H5" 1 data.prompt_head)" = null ]
check "under an SRI root: the snapshot omits it too" sh -c "! grep -q prompt_head '$(stream "$H5")'"
# A worktree of a Stimulus repo checked out elsewhere resolves through git.
mkdir -p "$T/ws/work/stimulus" && git -C "$T/ws/work/stimulus" init -q sri && \
    git -C "$T/ws/work/stimulus/sri" -c core.hooksPath=/dev/null -c user.email=t@t -c user.name=t commit -q --allow-empty -m init && \
    git -C "$T/ws/work/stimulus/sri" worktree add -q "$T/elsewhere-wt" 2>/dev/null
H5b="$T/h5b"
hook "$H5b" "$(with "$FX/04-stop-one-cron.json" "cwd=\"$T/elsewhere-wt\"")" BROOMVA_WORKSPACE="$T/ws" >/dev/null
check "a worktree of <workspace>/work/stimulus anywhere: scope sri" [ "$(field "$H5b" 1 data.scope)" = '"sri"' ]

# ── 6. never blocks, never writes on a bad input ─────────────────────────────
H6="$T/h6"
check "malformed JSON exits 0" [ "$(hook "$H6" 'not json')" = 0 ]
check "a session id with a path in it is ignored" \
    [ "$(hook "$H6" "$(with "$FX/04-stop-one-cron.json" 'session_id="../../etc/x"')")$(lines "$H6")" = 00 ]
check "a malformed cron entry is skipped, not fatal" \
    [ "$(hook "$H6" "$(with "$FX/04-stop-one-cron.json" 'session_crons=[{"id":"../x"},7,null]')")" = 0 ]
: > "$T/h6file"   # BROOMVA_HOME is a file: every write fails
check "an unwritable home exits 0" [ "$(hook "$T/h6file" "$FX/04-stop-one-cron.json")" = 0 ]
check "and says why on stderr" grep -q 'session-loops-hook:' "$T/h6file.stderr"
mkdir -p "$T/nowriter/hooks" && cp "$HOOK" "$T/nowriter/hooks/"
check "a plugin root without the writer exits 0 and writes nothing" \
    [ "$( (cd "$T/neutral" && BROOMVA_HOME="$T/h6w" HOME="$T/home" python3 -I "$T/nowriter/hooks/session-loops-hook.py" < "$FX/04-stop-one-cron.json"; echo $?) )$(lines "$T/h6w")" = 00 ]

# ── 7. P20 round 1 (BRO-2932): the cases a reviewer found the suite could miss ─
# A one-shot (ScheduleWakeup's, every turn of a self-paced /loop) changes the
# snapshot but writes no created/deleted pair of its own; its fire still counts.
H7="$T/h7"
ONESHOT='session_crons=[{"id":"w1","schedule":"5 14 8 10 *","recurring":false,"prompt":"wake up"}]'
hook "$H7" "$(with "$FX/04-stop-one-cron.json" "$ONESHOT")" >/dev/null
hook "$H7" "$(with "$FX/05-prompt-cron-fire.json" 'prompt="wake up"')" >/dev/null
hook "$H7" "$FX/08-stop-no-crons.json" >/dev/null
check "a one-shot writes snapshot, its fire, snapshot: no created/deleted pair" \
    [ "$(types "$H7")" = "session.snapshot run.started session.snapshot" ]
# Two crons sharing a prompt: the fire names both as candidates.
H7b="$T/h7b"
hook "$H7b" "$(with "$FX/04-stop-one-cron.json" 'session_crons=[{"id":"a1","schedule":"* * * * *","recurring":true,"prompt":"same"},{"id":"b2","schedule":"*/2 * * * *","recurring":true,"prompt":"same"}]')" >/dev/null
hook "$H7b" "$(with "$FX/05-prompt-cron-fire.json" 'prompt="same"')" >/dev/null
check "two crons with one prompt: the run lists both as candidates" [ "$(field "$H7b" 4 data.candidates)" = '["a1", "b2"]' ]
# A PostToolUse in the first turn the hook sees creates the cache before any
# snapshot: a cron that predates the hook is still only a lower bound.
H7c="$T/h7c"
hook "$H7c" "$(with "$FX/07-posttool-crondelete.json" 'tool_input={"id":"old00001"}' 'tool_response={"id":"old00001"}')" >/dev/null
hook "$H7c" "$FX/04-stop-one-cron.json" >/dev/null
check "no snapshot recorded yet: a cron found is a lower bound even with a cache" \
    [ "$(field "$H7c" 2 type)$(field "$H7c" 2 data.created_at_lower_bound)" = '"loop.session.cron_created"true' ]
# A corrupt cache: the cron is re-recorded as a lower bound (the adapter takes
# the earliest cron_created per subject), and SessionEnd still records the end.
H7d="$T/h7d"
hook "$H7d" "$FX/03-posttool-croncreate.json" >/dev/null
hook "$H7d" "$FX/04-stop-one-cron.json" >/dev/null
printf 'not json' > "$H7d/cache/session-loops/53305082-37ff-47ed-b9aa-1b33ec806891.json"
hook "$H7d" "$FX/04-stop-one-cron.json" >/dev/null
check "a corrupt cache re-records the cron, marked a lower bound" \
    [ "$(field "$H7d" 3 data.via)$(field "$H7d" 3 data.created_at_lower_bound)" = '"snapshot"true' ]
printf 'not json' > "$H7d/cache/session-loops/53305082-37ff-47ed-b9aa-1b33ec806891.json"
hook "$H7d" "$FX/09-session-end.json" >/dev/null
check "SessionEnd over a corrupt cache records the end, crons unknown" \
    [ "$(field "$H7d" 5 type)$(field "$H7d" 5 data.crons)" = '"loop.session.ended"null' ]
check "verify ok" verify "$H7d"
# A relative BROOMVA_HOME (the writer refuses it) must not land a cache in the repo.
rc="$( (cd "$T/neutral" && env HOME="$T/home" BROOMVA_HOME=relhome python3 -I "$HOOK" < "$FX/04-stop-one-cron.json" 2>/dev/null; echo $?) )"
check "a relative BROOMVA_HOME writes nothing into the session's cwd" [ "$rc$(ls -A "$T/neutral")" = 0 ]
# git missing: SRI cannot be ruled out, so no prompt head, and the answer is not cached.
H7e="$T/h7e"
mkdir -p "$T/nogit" "$T/plain-cwd" && ln -sf "$(command -v python3)" "$T/nogit/python3"
( cd "$T/neutral" && env PATH="$T/nogit" HOME="$T/home" BROOMVA_HOME="$H7e" \
    "$T/nogit/python3" -I "$HOOK" < <(with "$FX/04-stop-one-cron.json" "cwd=\"$T/plain-cwd\"") )
check "git unavailable: the cron is recorded as sri, without a head" \
    [ "$(field "$H7e" 1 data.scope)$(field "$H7e" 1 data.prompt_head)" = '"sri"null' ]
check "git unavailable: that answer is not cached" sh -c "! grep -q '\"sri\"' '$H7e/cache/session-loops/'*.json"
# A writer stuck holding the stream lock: the hook gives up on its own deadline,
# well inside hooks.json's 5 s timeout, and still exits 0.
H7f="$T/h7f"
mkdir -p "$H7f/locks"
python3 -I -c '
import fcntl, os, sys, time
fd = os.open(sys.argv[1], os.O_RDWR | os.O_CREAT, 0o600)
fcntl.flock(fd, fcntl.LOCK_EX)
open(sys.argv[2], "w").close()
time.sleep(20)' "$H7f/locks/loops__session.lock" "$T/locked" &
HOLDER=$!
for _ in $(seq 1 50); do [ -f "$T/locked" ] && break; sleep 0.1; done
t0=$(python3 -I -c 'import time; print(time.time())')
rc="$(hook "$H7f" "$FX/04-stop-one-cron.json")"
took=$(python3 -I -c "import time; print(int(time.time() - $t0))")
kill "$HOLDER" 2>/dev/null; wait "$HOLDER" 2>/dev/null
check "a held stream lock: exit 0 within the 3 s deadline (took ${took}s)" [ "$rc$((took < 5))" = 01 ]
check "and says it gave up" grep -q 'gave up after 3 s' "$H7f.stderr"

# ── 7. the wiring and the vendored writer ────────────────────────────────────
check "hooks.json runs it on Stop, UserPromptSubmit, SessionEnd and PostToolUse Cron*" python3 -I -c '
import json, sys
h = json.load(open(sys.argv[1]))["hooks"]
want = {"Stop": None, "UserPromptSubmit": None, "SessionEnd": None, "PostToolUse": "CronCreate|CronDelete"}
for event, matcher in want.items():
    hits = [b for b in h.get(event, []) for c in b["hooks"] if "session-loops-hook.py" in c["command"]]
    assert len(hits) == 1, (event, hits)
    assert hits[0].get("matcher") == matcher, (event, hits[0].get("matcher"))
    cmd = [c["command"] for c in hits[0]["hooks"] if "session-loops-hook.py" in c["command"]][0]
    assert cmd.startswith("python3 -I ") and cmd.endswith("|| true"), cmd
' "$REPO/hooks/hooks.json"
# scripts/broomva_home.py is a byte-identical copy of broomva/workspace's writer.
# Resync: copy it from the new source commit and update both pins together.
VENDORED_FROM="broomva/workspace@71d3e647e67169ce4c694042cec546715d5c3e13"
VENDORED_SHA256="7079519cdcb09a7eed23d40d2401b721aa5b99e89fe663666851606c0fb8c4c2"
got="$(python3 -I -c 'import hashlib,sys; print(hashlib.sha256(open(sys.argv[1],"rb").read()).hexdigest())' "$WRITER")"
check "scripts/broomva_home.py is $VENDORED_FROM, unedited" [ "$got" = "$VENDORED_SHA256" ]

echo ""
echo "session-loops-hook: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
