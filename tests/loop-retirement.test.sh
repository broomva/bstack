#!/usr/bin/env bash
# loop-retirement.test.sh — verifies the fake l0/l1 sensor is retired to safe no-op
# stubs, doctor §16/§17 now source L0/L1 from the leverage-sensor (not the retired
# logs), and the auth pre-flight hook warns-without-blocking.
set -uo pipefail

BSTACK_REPO="$(cd "$(dirname "$0")/.." && pwd)"
PASS=0; FAIL=0
ok()  { echo "  ok   $1"; PASS=$((PASS+1)); }
bad() { echo "  FAIL $1"; FAIL=$((FAIL+1)); }

TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT

echo "== l0/l1 hooks retired to no-op stubs =="
for h in l0-tool-audit-hook l1-reflex-audit-hook; do
    S="$BSTACK_REPO/scripts/$h.sh"
    # feeds the CC hook stdin payload; stub must drain it and exit 0, writing no audit log
    out=$(cd "$TMP" && echo '{"tool_name":"Bash","tool_result":{"is_error":false}}' | bash "$S" 2>/dev/null); rc=$?
    [ "$rc" = "0" ] && ok "$h exits 0 (never blocks)" || bad "$h exit $rc"
    [ -z "$out" ] && ok "$h emits no stdout (silent no-op)" || bad "$h emitted: $out"
    grep -q 'DEPRECATED' "$S" && ok "$h carries the DEPRECATED marker" || bad "$h missing deprecation marker"
done
# no audit jsonl should have been created by the stubs
[ ! -e "$TMP/.control/audit/l0-tools.jsonl" ] && [ ! -e "$TMP/.control/audit/l1-reflexes.jsonl" ] \
    && ok "stubs wrote no fake audit rows" || bad "a stub wrote an audit log"

echo "== doctor §16/§17 no longer read the retired logs =="
# check the DEPENDENCY signature (the read-assignment), not explanatory comments that
# name the retired files — those legitimately document what was superseded.
if grep -qE '^[[:space:]]*L0_LOG=|^[[:space:]]*L1_LOG=' "$BSTACK_REPO/scripts/doctor.sh"; then
    bad "doctor.sh still assigns L0_LOG/L1_LOG (reads the retired audit logs)"
else
    ok "doctor.sh §16/§17 dropped the retired-log dependency (no L0_LOG/L1_LOG read)"
fi
# ...and must source from leverage-state instead
grep -q 'LEV_STATE=.*leverage-state.json' "$BSTACK_REPO/scripts/doctor.sh" \
    && ok "doctor §16/§17 source L0/L1 from leverage-state.json" || bad "doctor not re-sourced from leverage-state"

echo "== auth pre-flight: warns, never blocks =="
A="$BSTACK_REPO/scripts/auth-preflight-hook.sh"
[ -x "$A" ] && ok "auth-preflight-hook.sh present + executable" || bad "auth-preflight-hook.sh missing/not executable"
echo '{}' | bash "$A" >/dev/null 2>&1; [ "$?" = "0" ] && ok "auth pre-flight exits 0 regardless of gh state" || bad "auth pre-flight non-zero exit"
grep -q '"auth-preflight"' "$BSTACK_REPO/assets/templates/settings.json.snippet" \
    && ok "auth-preflight wired at SessionStart in the snippet" || bad "auth-preflight not wired in snippet"

echo ""
echo "loop-retirement: $PASS passed, $FAIL failed"
[ "$FAIL" = "0" ]
