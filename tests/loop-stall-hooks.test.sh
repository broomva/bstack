#!/usr/bin/env bash
# loop-stall-hooks.test.sh — BRO-1700 loop-stall rejection. Verifies the arc-file
# state helper, the arc-continuation Stop-hook predicate (no-op terminal → block,
# with the complete-sentinel + reconcile_count<2 false-positive guards), the
# posture UserPromptSubmit hook (self-bootstrap on /autonomous + re-stamp), the
# limit-stall log-only calibration probe, and the settings.json.snippet wiring.
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
LIM="$S/limit-stall-resume-hook.sh"

# helper: write a one-line assistant-turn transcript to a UNIQUELY-named file
# (each fixture needs its own path — a shared filename would let later writes
# clobber earlier ones and every predicate would read the last content written).
fixture() { local f="$TMP/$1.jsonl"; printf '%s\n' "$2" > "$f"; printf '%s' "$f"; }
# helper: run the continuation hook with a given sid + transcript
cont() { echo "{\"session_id\":\"$1\",\"transcript_path\":\"$2\"}" | bash "$CONT"; }

echo "== arc-state helper lifecycle =="
[ -x "$ARC" ] && ok "autonomous-arc.sh executable" || bad "autonomous-arc.sh missing/not executable"
[ "$("$ARC" set "$SID" demo 'slice one' 'slice two')" = "active demo" ] && ok "set → active" || bad "set output wrong"
[ "$("$ARC" status "$SID")" = "active demo" ] && ok "status → active demo" || bad "status wrong"
"$ARC" active "$SID" && ok "active → exit 0 while active" || bad "active exit non-0 while active"
[ "$("$ARC" next "$SID")" = "slice one" ] && ok "next → first undone milestone" || bad "next wrong"
[ "$("$ARC" bump "$SID" reconcile_count)" = "1" ] && ok "bump → 1" || bad "bump wrong"
[ "$("$ARC" get "$SID" reconcile_count)" = "1" ] && ok "get reconcile_count → 1" || bad "get wrong"
"$ARC" complete "$SID" >/dev/null
"$ARC" active "$SID" && bad "active exit 0 after complete" || ok "active → exit 1 after complete (the sentinel)"

echo "== arc-continuation predicate =="
"$ARC" set "$SID" demo >/dev/null
NOOP=$(fixture noop '{"type":"assistant","message":{"role":"assistant","content":[{"type":"text","text":"No response requested."}]}}')
TOOL=$(fixture tool '{"type":"assistant","message":{"role":"assistant","content":[{"type":"tool_use","name":"Bash","input":{}}]}}')
SUBST=$(fixture subst '{"type":"assistant","message":{"role":"assistant","content":[{"type":"text","text":"Here is the full analysis with a recommendation and next steps."}]}}')
DONE=$(fixture alldone '{"type":"assistant","message":{"role":"assistant","content":[{"type":"text","text":"All milestones shipped."}]}}')

echo "$(cont "$SID" "$NOOP")" | grep -q '"decision": "block"' && ok "no-op terminal → block decision" || bad "no-op did NOT block"
"$ARC" set "$SID" demo >/dev/null  # reset counter
[ -z "$(cont "$SID" "$TOOL")" ] && ok "tool-use turn → silent (productive)" || bad "tool-use turn blocked"
[ -z "$(cont "$SID" "$SUBST")" ] && ok "substantive text → silent" || bad "substantive text blocked"
[ -z "$(cont "$SID" "$DONE")" ] && ok "explicit completion phrase → silent" || bad "completion phrase blocked"
"$ARC" complete "$SID" >/dev/null
[ -z "$(cont "$SID" "$NOOP")" ] && ok "inactive arc → silent even on no-op (sentinel guard)" || bad "inactive arc blocked"

echo "== runaway guard (reconcile_count cap = 2) =="
"$ARC" set "$SID" demo >/dev/null
b1=$(cont "$SID" "$NOOP"); b2=$(cont "$SID" "$NOOP"); b3=$(cont "$SID" "$NOOP")
{ [ -n "$b1" ] && [ -n "$b2" ] && [ -z "$b3" ]; } && ok "blocks twice then gives up (no infinite loop)" || bad "runaway guard wrong (b1='$b1' b2='$b2' b3='$b3')"

echo "== every hook exits 0 (Stop hooks signal via JSON, never exit 2) =="
"$ARC" set "$SID" demo >/dev/null
cont "$SID" "$NOOP" >/dev/null 2>&1; [ "$?" = "0" ] && ok "continuation hook exits 0 even when blocking" || bad "continuation hook non-zero exit"
echo '{}' | bash "$CONT" >/dev/null 2>&1; [ "$?" = "0" ] && ok "continuation hook exits 0 on empty stdin" || bad "continuation non-zero on empty stdin"
echo '{}' | bash "$POS" >/dev/null 2>&1; [ "$?" = "0" ] && ok "posture hook exits 0 on empty stdin" || bad "posture non-zero on empty stdin"
echo '{}' | bash "$LIM" >/dev/null 2>&1; [ "$?" = "0" ] && ok "limit-stall hook exits 0 on empty stdin" || bad "limit-stall non-zero on empty stdin"

echo "== posture hook: self-bootstrap on /autonomous + re-stamp =="
NS="posture-sid-2"
out="$(echo "{\"session_id\":\"$NS\",\"prompt\":\"/autonomous ship it\"}" | bash "$POS")"
echo "$out" | grep -q 'sticky posture' && ok "/autonomous → posture line emitted" || bad "no posture line on /autonomous"
[ "$("$ARC" status "$NS")" = "active autonomous" ] && ok "/autonomous self-bootstrapped an active arc" || bad "arc not created on /autonomous"
out2="$(echo "{\"session_id\":\"$NS\",\"prompt\":\"keep going\"}" | bash "$POS")"
echo "$out2" | grep -q 'sticky posture' && ok "re-stamps posture on a later turn" || bad "posture not re-stamped"
# a non-/autonomous prompt with no active arc must stay silent
[ -z "$(echo '{"session_id":"quiet-sid","prompt":"hello there"}' | bash "$POS")" ] && ok "silent when no arc active" || bad "posture leaked with no active arc"

echo "== limit-stall: log-only calibration probe =="
"$ARC" set "$SID" demo >/dev/null
LIMIT_TR=$(fixture limit '{"type":"assistant","message":{"role":"assistant","content":[{"type":"text","text":"usage limit reached · resets in 3h"}]}}')
out="$(echo "{\"session_id\":\"$SID\",\"transcript_path\":\"$LIMIT_TR\"}" | bash "$LIM")"
[ -z "$out" ] && ok "limit-stall emits no decision (log-only, never acts)" || bad "limit-stall emitted output: $out"
[ -s "$BROOMVA_AUTONOMOUS_HOME/resume-dryrun.jsonl" ] && ok "limit-stall appended a calibration candidate" || bad "no calibration candidate logged"
grep -q 'dry-run-only (never scheduled)' "$BROOMVA_AUTONOMOUS_HOME/resume-dryrun.jsonl" && ok "candidate marks itself dry-run-only" || bad "candidate missing dry-run marker"
# a clean transcript (no limit tokens) logs nothing new
before=$(wc -l < "$BROOMVA_AUTONOMOUS_HOME/resume-dryrun.jsonl")
echo "{\"session_id\":\"$SID\",\"transcript_path\":\"$SUBST\"}" | bash "$LIM" >/dev/null
after=$(wc -l < "$BROOMVA_AUTONOMOUS_HOME/resume-dryrun.jsonl")
[ "$before" = "$after" ] && ok "no candidate logged on a clean transcript" || bad "false-positive limit candidate logged"

echo "== settings.json.snippet wiring =="
SNIP="$BSTACK_REPO/assets/templates/settings.json.snippet"
python3 -c "import json,sys; json.load(open('$SNIP'))" 2>/dev/null && ok "snippet is valid JSON" || bad "snippet is invalid JSON"
grep -q 'autonomous-posture-hook.sh' "$SNIP" && ok "posture hook wired (UserPromptSubmit)" || bad "posture hook not wired"
grep -q 'arc-continuation-hook.sh' "$SNIP" && ok "continuation hook wired (Stop)" || bad "continuation hook not wired"
grep -q 'limit-stall-resume-hook.sh' "$SNIP" && ok "limit-stall hook wired (Stop)" || bad "limit-stall hook not wired"

echo ""
echo "loop-stall-hooks: $PASS passed, $FAIL failed"
[ "$FAIL" = "0" ]
