#!/usr/bin/env bash
# loop-stall-hooks.test.sh — BRO-1700 loop-stall rejection. Verifies the arc-file
# state helper (atomic/flock writes, staleness), the arc-continuation Stop-hook
# predicate (drain-retry for the flush race, tight no-op match, auto-release,
# consecutive-stall cap), the posture UserPromptSubmit hook (self-bootstrap +
# set-if-absent + re-stamp), and the settings.json.snippet wiring.
set -uo pipefail

BSTACK_REPO="$(cd "$(dirname "$0")/.." && pwd)"
S="$BSTACK_REPO/scripts"
PASS=0; FAIL=0
ok()  { echo "  ok   $1"; PASS=$((PASS+1)); }
bad() { echo "  FAIL $1"; FAIL=$((FAIL+1)); }

TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
export BROOMVA_AUTONOMOUS_HOME="$TMP/arcs"
SID="unit-sid-1"
ARC="$S/autonomous-arc.sh"
CONT="$S/arc-continuation-hook.sh"
POS="$S/autonomous-posture-hook.sh"

# fixture <name> <has_tool_use:0|1> <text> → a one-line assistant turn with a FRESH
# timestamp (so the continuation hook's drain-retry settles immediately) at a unique path.
fixture() {
  local name="$1" tu="$2" text="$3" now
  local f="$TMP/$name.jsonl"
  now="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  if [ "$tu" = "1" ]; then
    printf '{"type":"assistant","timestamp":"%s","message":{"role":"assistant","content":[{"type":"tool_use","name":"Bash","input":{}}]}}\n' "$now" > "$f"
  else
    printf '{"type":"assistant","timestamp":"%s","message":{"role":"assistant","content":[{"type":"text","text":"%s"}]}}\n' "$now" "$text" > "$f"
  fi
  printf '%s' "$f"
}
cont() { echo "{\"session_id\":\"$1\",\"transcript_path\":\"$2\"}" | bash "$CONT"; }

echo "== arc-state helper lifecycle =="
[ -x "$ARC" ] && ok "autonomous-arc.sh executable" || bad "autonomous-arc.sh missing/not executable"
[ "$("$ARC" set "$SID" demo 'slice one' 'slice two')" = "active demo" ] && ok "set → active" || bad "set output wrong"
[ "$("$ARC" status "$SID")" = "active demo" ] && ok "status → active demo" || bad "status wrong"
"$ARC" active "$SID" && ok "active → exit 0 while active" || bad "active exit non-0 while active"
[ "$("$ARC" next "$SID")" = "slice one" ] && ok "next → first undone milestone" || bad "next wrong"
[ "$("$ARC" bump "$SID" reconcile_count)" = "1" ] && ok "bump → 1" || bad "bump wrong"
[ "$("$ARC" reset "$SID" reconcile_count)" = "0" ] && ok "reset → 0" || bad "reset wrong"
"$ARC" complete "$SID" >/dev/null
"$ARC" active "$SID" && bad "active exit 0 after complete" || ok "active → exit 1 after complete (the sentinel)"
if ls "$BROOMVA_AUTONOMOUS_HOME"/*.tmp >/dev/null 2>&1; then bad "leftover .tmp from atomic writes"; else ok "no leftover .tmp from atomic writes"; fi

echo "== arc-continuation predicate (tight no-op match) =="
"$ARC" set "$SID" demo >/dev/null
cont "$SID" "$(fixture noop 0 'No response requested.')" | grep -q '"decision": "block"' && ok "'No response requested.' → block" || bad "sentinel did NOT block"
"$ARC" set "$SID" demo >/dev/null
cont "$SID" "$(fixture wrap 0 'No response requested. Stopping here.')" | grep -q '"decision": "block"' && ok "wrapped sentinel → block (search, not anchored)" || bad "wrapped sentinel missed"
"$ARC" set "$SID" demo >/dev/null
cont "$SID" "$(fixture empty 0 '')" | grep -q '"decision": "block"' && ok "empty terminal → block" || bad "empty terminal missed"
"$ARC" set "$SID" demo >/dev/null
cont "$SID" "$(fixture ack 0 'Acknowledged.')" | grep -q '"decision": "block"' && ok "'Acknowledged.' → block" || bad "acknowledged missed"
# these must NOT block — conversational replies / completions, NOT no-ops
"$ARC" set "$SID" demo >/dev/null
[ -z "$(cont "$SID" "$(fixture ok 0 'Okay.')")" ] && ok "'Okay.' → silent (legit user-pause reply, P20 fix)" || bad "'Okay.' wrongly blocked"
"$ARC" set "$SID" demo >/dev/null
[ -z "$(cont "$SID" "$(fixture gotit 0 'Got it.')")" ] && ok "'Got it.' → silent (P20 fix)" || bad "'Got it.' wrongly blocked"
"$ARC" set "$SID" demo >/dev/null
[ -z "$(cont "$SID" "$(fixture tool 1 '')")" ] && ok "tool-use turn → silent (productive)" || bad "tool-use blocked"
"$ARC" set "$SID" demo >/dev/null
[ -z "$(cont "$SID" "$(fixture subst 0 'Here is the full analysis with a recommendation.')")" ] && ok "substantive text → silent" || bad "substantive text blocked"
"$ARC" complete "$SID" >/dev/null
[ -z "$(cont "$SID" "$(fixture inact 0 'No response requested.')")" ] && ok "inactive arc → silent (sentinel guard)" || bad "inactive arc blocked"

echo "== auto-release on completion phrase (P20 fix) =="
"$ARC" set "$SID" demo >/dev/null
out=$(cont "$SID" "$(fixture cmp 0 'All milestones shipped.')")
if [ -z "$out" ] && ! "$ARC" active "$SID" >/dev/null 2>&1; then ok "'All milestones shipped.' auto-released the arc + no block"; else bad "completion not auto-released (out='$out')"; fi
"$ARC" set "$SID" demo >/dev/null
[ -z "$(cont "$SID" "$(fixture doneword 0 'Done.')")" ] && ok "bare 'Done.' → silent (not force-continued; P20 fix)" || bad "'Done.' wrongly blocked"

echo "== reconcile_count bounds CONSECUTIVE stalls, resets on productive (P20 fix) =="
"$ARC" set "$SID" demo >/dev/null
cont "$SID" "$(fixture s1 0 'No response requested.')" >/dev/null           # block rc=1
cont "$SID" "$(fixture prog 1 '')" >/dev/null                               # productive → reset
[ "$("$ARC" get "$SID" reconcile_count)" = "0" ] && ok "productive turn reset reconcile_count to 0" || bad "counter not reset on productive"
b1=$(cont "$SID" "$(fixture c1 0 'No response requested.')")                # block rc=1
b2=$(cont "$SID" "$(fixture c2 0 'No response requested.')")                # block rc=2
b3=$(cont "$SID" "$(fixture c3 0 'No response requested.')")                # capped
{ [ -n "$b1" ] && [ -n "$b2" ] && [ -z "$b3" ]; } && ok "2 consecutive blocks then gives up (no infinite loop)" || bad "consecutive cap wrong (b1='$b1' b2='$b2' b3='$b3')"

echo "== drain-retry catches a late-flushed final turn (BRO-1616 race, P20 HIGH) =="
"$ARC" set "$SID" demo >/dev/null
RT="$TMP/race.jsonl"
# on-disk at hook start: only a STALE productive turn (2020) — a single read would SKIP
printf '{"type":"assistant","timestamp":"2020-01-01T00:00:00Z","message":{"role":"assistant","content":[{"type":"tool_use","name":"Bash","input":{}}]}}\n' > "$RT"
( sleep 0.3; printf '{"type":"assistant","timestamp":"%s","message":{"role":"assistant","content":[{"type":"text","text":"No response requested."}]}}\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" >> "$RT" ) &
ap=$!
out="$(echo "{\"session_id\":\"$SID\",\"transcript_path\":\"$RT\"}" | ARC_DRAIN_MS=3000 bash "$CONT")"
wait $ap 2>/dev/null
echo "$out" | grep -q '"decision": "block"' && ok "drain-retry waited for + classified the late fresh no-op turn" || bad "drain-retry missed the late turn (out='$out')"

echo "== staleness auto-expiry (P20 fix — cannot fight the user forever) =="
SS=stale-sid
"$ARC" set "$SS" demo >/dev/null
python3 -c "import json; p='$BROOMVA_AUTONOMOUS_HOME/$SS.arc'; d=json.load(open(p)); d['invoked_at']='2020-01-01T00:00:00+00:00'; json.dump(d,open(p,'w'))"
"$ARC" active "$SS" && bad "stale arc still active" || ok "arc past staleness window reads inactive"
"$ARC" status "$SS" | grep -q inactive && ok "status reports inactive for a stale arc" || bad "status wrong for stale arc"

echo "== concurrent bumps are serialized (flock, no lost updates — P20 fix) =="
"$ARC" set "$SID" demo >/dev/null
for _ in 1 2 3 4 5 6 7 8; do "$ARC" bump "$SID" reconcile_count >/dev/null & done
wait
got="$("$ARC" get "$SID" reconcile_count)"
[ "$got" = "8" ] && ok "8 concurrent bumps → count 8 (no lost updates)" || bad "lost update: count=$got (expected 8)"
python3 -c "import json; json.load(open('$BROOMVA_AUTONOMOUS_HOME/$SID.arc'))" >/dev/null 2>&1 && ok "arc file valid JSON after concurrent writes" || bad "arc corrupted by concurrent writes"

echo "== hooks exit 0 (Stop signals via JSON, never exit 2) =="
"$ARC" set "$SID" demo >/dev/null
cont "$SID" "$(fixture x 0 'No response requested.')" >/dev/null 2>&1; [ "$?" = "0" ] && ok "continuation exits 0 even when blocking" || bad "continuation non-zero exit"
echo '{}' | bash "$CONT" >/dev/null 2>&1; [ "$?" = "0" ] && ok "continuation exits 0 on empty stdin" || bad "continuation non-zero on empty stdin"
echo '{}' | bash "$POS" >/dev/null 2>&1; [ "$?" = "0" ] && ok "posture exits 0 on empty stdin" || bad "posture non-zero on empty stdin"

echo "== posture hook: self-bootstrap + set-if-absent + re-stamp =="
NS="posture-sid-2"
echo "{\"session_id\":\"$NS\",\"prompt\":\"/autonomous ship it\"}" | bash "$POS" | grep -q 'sticky posture' && ok "/autonomous → posture line emitted" || bad "no posture line on /autonomous"
[ "$("$ARC" status "$NS")" = "active autonomous" ] && ok "/autonomous self-bootstrapped an active arc" || bad "arc not created on /autonomous"
"$ARC" bump "$NS" reconcile_count >/dev/null   # rc=1
echo "{\"session_id\":\"$NS\",\"prompt\":\"/autonomous keep going\"}" | bash "$POS" >/dev/null
[ "$("$ARC" get "$NS" reconcile_count)" = "1" ] && ok "re-typing /autonomous did NOT reset the counter (set-if-absent, P20 fix)" || bad "re-typing /autonomous reset the counter"
echo "{\"session_id\":\"$NS\",\"prompt\":\"next\"}" | bash "$POS" | grep -q 'sticky posture' && ok "re-stamps posture on a later turn" || bad "posture not re-stamped"
[ -z "$(echo '{"session_id":"quiet-sid","prompt":"hello"}' | bash "$POS")" ] && ok "silent when no arc active" || bad "posture leaked with no active arc"

echo "== settings.json.snippet wiring =="
SNIP="$BSTACK_REPO/assets/templates/settings.json.snippet"
python3 -c "import json,sys; json.load(open('$SNIP'))" 2>/dev/null && ok "snippet is valid JSON" || bad "snippet invalid JSON"
grep -q 'autonomous-posture-hook.sh' "$SNIP" && ok "posture hook wired (UserPromptSubmit)" || bad "posture hook not wired"
grep -q 'arc-continuation-hook.sh' "$SNIP" && ok "continuation hook wired (Stop)" || bad "continuation hook not wired"
! grep -q 'limit-stall-resume-hook.sh' "$SNIP" && ok "dropped limit-stall hook is NOT wired (deferred)" || bad "limit-stall still wired"

echo ""
echo "loop-stall-hooks: $PASS passed, $FAIL failed"
[ "$FAIL" = "0" ]
