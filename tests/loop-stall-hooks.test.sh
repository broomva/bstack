#!/usr/bin/env bash
# loop-stall-hooks.test.sh — BRO-1700 loop-stall rejection. Verifies the arc-file
# state helper (atomic/flock writes, consecutive + lifetime caps, staleness), the
# arc-continuation Stop-hook predicate (identity-based drain-retry, bias-to-safety
# no-op match, auto-release, cap bounds), the posture UserPromptSubmit hook
# (self-bootstrap + set-if-absent + re-stamp), and the settings.json.snippet wiring.
set -uo pipefail

BSTACK_REPO="$(cd "$(dirname "$0")/.." && pwd)"
S="$BSTACK_REPO/scripts"
PASS=0; FAIL=0
ok()  { echo "  ok   $1"; PASS=$((PASS+1)); }
bad() { echo "  FAIL $1"; FAIL=$((FAIL+1)); }

TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
export BROOMVA_AUTONOMOUS_HOME="$TMP/arcs"
export ARC_DRAIN_MS=150          # static fixtures never change → short drain budget for speed
SID="unit-sid-1"
ARC="$S/autonomous-arc.sh"
CONT="$S/arc-continuation-hook.sh"
POS="$S/autonomous-posture-hook.sh"

# fixture <name> <has_tool_use:0|1> <text> → one assistant turn (unique uuid) at a unique path
fixture() {
  local name="$1" tu="$2" text="$3"
  local f="$TMP/$name.jsonl"
  if [ "$tu" = "1" ]; then
    printf '{"type":"assistant","uuid":"%s","message":{"role":"assistant","content":[{"type":"tool_use","name":"Bash","input":{}}]}}\n' "$name" > "$f"
  else
    printf '{"type":"assistant","uuid":"%s","message":{"role":"assistant","content":[{"type":"text","text":"%s"}]}}\n' "$name" "$text" > "$f"
  fi
  printf '%s' "$f"
}
# cont <sid> <transcript> [stop_hook_active:true|false]
cont() {
  local sa="${3:-false}"
  echo "{\"session_id\":\"$1\",\"transcript_path\":\"$2\",\"stop_hook_active\":$sa}" | bash "$CONT"
}

echo "== arc-state helper lifecycle =="
[ -x "$ARC" ] && ok "autonomous-arc.sh executable" || bad "autonomous-arc.sh missing/not executable"
[ "$("$ARC" set "$SID" demo 'slice one')" = "active demo" ] && ok "set → active" || bad "set output wrong"
"$ARC" active "$SID" && ok "active → exit 0 while active" || bad "active exit non-0 while active"
[ "$("$ARC" next "$SID")" = "slice one" ] && ok "next → first undone milestone" || bad "next wrong"
[ "$("$ARC" try-block "$SID" 2 5)" = "BLOCK" ] && ok "try-block → BLOCK under caps" || bad "try-block wrong"
[ "$("$ARC" get "$SID" total_blocks)" = "1" ] && ok "try-block bumped total_blocks" || bad "total_blocks not bumped"
[ "$("$ARC" reset "$SID" reconcile_count)" = "0" ] && ok "reset → 0" || bad "reset wrong"
"$ARC" complete "$SID" >/dev/null
"$ARC" active "$SID" && bad "active exit 0 after complete" || ok "active → exit 1 after complete (the sentinel)"
if ls "$BROOMVA_AUTONOMOUS_HOME"/*.tmp >/dev/null 2>&1; then bad "leftover .tmp from atomic writes"; else ok "no leftover .tmp"; fi

echo "== no-op predicate (bias-to-safety: block ONLY unambiguous no-ops) =="
"$ARC" set "$SID" demo >/dev/null
cont "$SID" "$(fixture noop 0 'No response requested.')" | grep -q '"decision": "block"' && ok "exact 'No response requested.' → block" || bad "sentinel did NOT block"
"$ARC" set "$SID" demo >/dev/null
cont "$SID" "$(fixture empty 0 '')" | grep -q '"decision": "block"' && ok "empty terminal → block" || bad "empty terminal missed"
# these must NOT block — accepted false negatives + substantive turns (P20 r2 fixes)
for t in "wrap|No response requested. Stopping here and awaiting input." \
         "mention|Here is my review of the No response requested sentinel handling." \
         "ack|Acknowledged." "ok|Okay." "gotit|Got it." "doneword|Done." \
         "subst|Here is the full analysis with a recommendation."; do
  name="${t%%|*}"; txt="${t#*|}"
  "$ARC" set "$SID" demo >/dev/null
  [ -z "$(cont "$SID" "$(fixture "$name" 0 "$txt")")" ] && ok "'$txt' → silent (not force-continued)" || bad "'$txt' wrongly blocked"
done
"$ARC" set "$SID" demo >/dev/null
[ -z "$(cont "$SID" "$(fixture tool 1 '')")" ] && ok "tool-use turn → silent (productive)" || bad "tool-use blocked"
"$ARC" complete "$SID" >/dev/null
[ -z "$(cont "$SID" "$(fixture inact 0 'No response requested.')")" ] && ok "inactive arc → silent" || bad "inactive arc blocked"

echo "== thinking-only entries are NEVER force-continued (P20 r3 HIGH) =="
"$ARC" set "$SID" demo >/dev/null
TH="$TMP/think.jsonl"
printf '{"type":"assistant","uuid":"th1","message":{"role":"assistant","content":[{"type":"thinking","thinking":"pondering the next step"}]}}\n' > "$TH"
[ -z "$(cont "$SID" "$TH")" ] && ok "thinking-only final entry → silent (not force-continued)" || bad "thinking-only entry force-continued"
"$ARC" set "$SID" demo >/dev/null
printf '{"type":"assistant","uuid":"th2","message":{"role":"assistant","content":[{"type":"thinking","thinking":"x"},{"type":"text","text":"Here is the plan and next steps."}]}}\n' > "$TH"
[ -z "$(cont "$SID" "$TH")" ] && ok "thinking + substantive text → silent" || bad "thinking+text blocked"
# drain must SKIP a thinking-only intermediate entry and wait for the real text yield
"$ARC" set "$SID" demo >/dev/null
DR="$TMP/drain-think.jsonl"
printf '{"type":"assistant","uuid":"prior2","message":{"role":"assistant","content":[{"type":"tool_use","name":"Bash","input":{}}]}}\n' > "$DR"
( sleep 0.2; printf '{"type":"assistant","uuid":"thk","message":{"role":"assistant","content":[{"type":"thinking","thinking":"y"}]}}\n' >> "$DR"; \
  sleep 0.3; printf '{"type":"assistant","uuid":"txt","message":{"role":"assistant","content":[{"type":"text","text":"Here is my substantive answer."}]}}\n' >> "$DR" ) &
ap=$!
out="$(echo "{\"session_id\":\"$SID\",\"transcript_path\":\"$DR\",\"stop_hook_active\":false}" | ARC_DRAIN_MS=3000 bash "$CONT")"
wait $ap 2>/dev/null
[ -z "$out" ] && ok "drain skipped the thinking intermediate, classified the text yield → silent" || bad "drain force-continued via a thinking entry (out='$out')"

echo "== auto-release: completion-dominant releases; mid-arc mention does not =="
for c in "All milestones shipped." "All done!" "Everything's shipped and merged to main." "The arc is complete."; do
  "$ARC" set "$SID" demo >/dev/null
  cont "$SID" "$(fixture rel 0 "$c")" >/dev/null
  "$ARC" active "$SID" >/dev/null 2>&1 && bad "completion '$c' did NOT auto-release" || ok "'$c' → auto-released the arc"
done
"$ARC" set "$SID" demo >/dev/null
cont "$SID" "$(fixture midc 0 'The first task is complete, moving on to the next slice now.')" >/dev/null
"$ARC" active "$SID" >/dev/null 2>&1 && ok "mid-arc 'first task is complete' did NOT auto-release (anchored)" || bad "premature auto-release"

echo "== consecutive cap resets on productive OUT of chain =="
"$ARC" set "$SID" demo >/dev/null
cont "$SID" "$(fixture o1 0 'No response requested.')" false >/dev/null    # block rc=1
cont "$SID" "$(fixture o2 1 '')" false >/dev/null                          # productive out-of-chain → reset
[ "$("$ARC" get "$SID" reconcile_count)" = "0" ] && ok "productive (out of chain) resets reconcile_count" || bad "out-of-chain reset failed"

echo "== in-chain trivial tool does NOT reset the cap (stop_hook_active honored, P20 r2 HIGH) =="
"$ARC" set "$SID" demo >/dev/null
b1=$(cont "$SID" "$(fixture t1 0 'No response requested.')" true)          # block rc=1
cont "$SID" "$(fixture t2 1 '')" true >/dev/null                          # trivial tool IN CHAIN → no reset
b2=$(cont "$SID" "$(fixture t3 0 'No response requested.')" true)          # block rc=2
cont "$SID" "$(fixture t4 1 '')" true >/dev/null                          # trivial tool IN CHAIN → no reset
b3=$(cont "$SID" "$(fixture t5 0 'No response requested.')" true)          # rc=2 → capped
{ [ -n "$b1" ] && [ -n "$b2" ] && [ -z "$b3" ]; } && ok "interleaved-tool loop is bounded (cap not reset in chain)" || bad "trivial-tool loop unbounded (b1='$b1' b2='$b2' b3='$b3')"

echo "== lifetime cap: at most 5 total blocks even with resets (P20 r2 HIGH) =="
"$ARC" set "$SID" demo >/dev/null
n=0
for i in 1 2 3 4 5 6 7; do
  out=$(cont "$SID" "$(fixture "life$i" 0 'No response requested.')" false)
  [ -n "$out" ] && n=$((n+1))
  "$ARC" reset "$SID" reconcile_count >/dev/null    # simulate a productive reset between each stall
done
[ "$n" = "5" ] && ok "lifetime cap engaged: 5 blocks then gives up despite resets" || bad "lifetime cap wrong (got $n blocks, expected 5)"

echo "== identity drain-retry catches a late-flushed final turn (fast-loop safe, P20 HIGH/MED) =="
"$ARC" set "$SID" demo >/dev/null
RT="$TMP/race.jsonl"
# on-disk at hook start: only a prior productive turn (distinct uuid) — a single read would SKIP
printf '{"type":"assistant","uuid":"prior","message":{"role":"assistant","content":[{"type":"tool_use","name":"Bash","input":{}}]}}\n' > "$RT"
( sleep 0.3; printf '{"type":"assistant","uuid":"fresh","message":{"role":"assistant","content":[{"type":"text","text":"No response requested."}]}}\n' >> "$RT" ) &
ap=$!
out="$(echo "{\"session_id\":\"$SID\",\"transcript_path\":\"$RT\",\"stop_hook_active\":false}" | ARC_DRAIN_MS=3000 bash "$CONT")"
wait $ap 2>/dev/null
echo "$out" | grep -q '"decision": "block"' && ok "drain waited for the new-identity fresh no-op turn + blocked" || bad "drain missed the late turn (out='$out')"

echo "== staleness auto-expiry =="
SS=stale-sid
"$ARC" set "$SS" demo >/dev/null
python3 -c "import json; p='$BROOMVA_AUTONOMOUS_HOME/$SS.arc'; d=json.load(open(p)); d['invoked_at']='2020-01-01T00:00:00+00:00'; json.dump(d,open(p,'w'))"
"$ARC" active "$SS" && bad "stale arc still active" || ok "arc past staleness window reads inactive"

echo "== concurrent bumps serialized (flock) =="
"$ARC" set "$SID" demo >/dev/null
for _ in 1 2 3 4 5 6 7 8; do "$ARC" bump "$SID" reconcile_count >/dev/null & done
wait
[ "$("$ARC" get "$SID" reconcile_count)" = "8" ] && ok "8 concurrent bumps → 8 (no lost updates)" || bad "lost update: $("$ARC" get "$SID" reconcile_count)"
python3 -c "import json; json.load(open('$BROOMVA_AUTONOMOUS_HOME/$SID.arc'))" >/dev/null 2>&1 && ok "arc valid JSON after concurrent writes" || bad "arc corrupted"

echo "== hooks exit 0 =="
"$ARC" set "$SID" demo >/dev/null
cont "$SID" "$(fixture z 0 'No response requested.')" >/dev/null 2>&1; [ "$?" = "0" ] && ok "continuation exits 0 when blocking" || bad "continuation non-zero"
echo '{}' | bash "$CONT" >/dev/null 2>&1; [ "$?" = "0" ] && ok "continuation exits 0 on empty stdin" || bad "continuation non-zero empty stdin"
echo '{}' | bash "$POS" >/dev/null 2>&1; [ "$?" = "0" ] && ok "posture exits 0 on empty stdin" || bad "posture non-zero empty stdin"

echo "== posture hook: self-bootstrap + set-if-absent + re-stamp =="
NS="posture-sid-2"
echo "{\"session_id\":\"$NS\",\"prompt\":\"/autonomous ship it\"}" | bash "$POS" | grep -q 'sticky posture' && ok "/autonomous → posture line" || bad "no posture line"
[ "$("$ARC" status "$NS")" = "active autonomous" ] && ok "/autonomous self-bootstrapped an arc" || bad "arc not created"
"$ARC" bump "$NS" reconcile_count >/dev/null
echo "{\"session_id\":\"$NS\",\"prompt\":\"/autonomous keep going\"}" | bash "$POS" >/dev/null
[ "$("$ARC" get "$NS" reconcile_count)" = "1" ] && ok "re-typing /autonomous did NOT reset the counter (set-if-absent)" || bad "re-typing /autonomous reset counter"
[ -z "$(echo '{"session_id":"quiet-sid","prompt":"hello"}' | bash "$POS")" ] && ok "silent when no arc active" || bad "posture leaked"

echo "== settings.json.snippet wiring =="
SNIP="$BSTACK_REPO/assets/templates/settings.json.snippet"
python3 -c "import json; json.load(open('$SNIP'))" 2>/dev/null && ok "snippet valid JSON" || bad "snippet invalid JSON"
grep -q 'autonomous-posture-hook.sh' "$SNIP" && ok "posture hook wired" || bad "posture not wired"
grep -q 'arc-continuation-hook.sh' "$SNIP" && ok "continuation hook wired" || bad "continuation not wired"
! grep -q 'limit-stall-resume-hook.sh' "$SNIP" && ok "dropped limit-stall not wired" || bad "limit-stall still wired"

echo ""
echo "loop-stall-hooks: $PASS passed, $FAIL failed"
[ "$FAIL" = "0" ]
