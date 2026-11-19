#!/usr/bin/env bash
# tests/leverage-l3-wiring.test.sh — BRO-3955: the L3 readings reach the agent.
#
# Drives leverage-sensor.py exactly as the hooks do (Stop: compute + store;
# SessionStart: `--brief --cached --no-store`) against a throwaway workspace whose
# ship/fleet state files are written by hand, and checks what the agent is told:
#   - an outward-empty window puts m7's actuator into the SessionStart brief;
#   - a declared setpoint with no reading is announced, never certified;
#   - a fresh fleet scan replaces the one-checkout m6 and keeps L3 closed.
set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/.." && pwd)"
SENSOR="$REPO/scripts/leverage-sensor.py"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

PASS=0; FAIL=0
ok(){ PASS=$((PASS+1)); echo "  ok   $1"; }
bad(){ FAIL=$((FAIL+1)); echo "  FAIL $1"; }
has(){ if grep -qF -- "$2" <<<"$3"; then ok "$1"; else bad "$1 — missing '$2' in: $3"; fi; }
hasnt(){ if grep -qF -- "$2" <<<"$3"; then bad "$1 — unexpected '$2' in: $3"; else ok "$1"; fi; }

if ! python3 -c 'import yaml' 2>/dev/null; then
  echo "  [skip] PyYAML not importable; the sensor reads YAML setpoints"; exit 0
fi

WS="$TMP/ws"; mkdir -p "$WS/.control"
cat > "$WS/.control/leverage-setpoints.yaml" <<'Y'
authored_by: test-owner
version: 1
window_days: 7
metrics:
  - {id: m2, name: tool_error_rate, level: L0, direction: lower_is_better, target: 0.05, alert: 0.12}
  - {id: m6, name: meta_work_session_ratio, level: L3, direction: lower_is_better, target: 0.5, alert: 0.75, status: shadow}
  - id: m7
    name: outward_artifact_floor
    level: L3
    direction: higher_is_better
    target: 1.0
    alert: 0.99
    actuator: "outward-empty week: ship ONE merged product PR or log ONE door receipt"
  - {id: w1, name: agent_idle_share, level: L1, status: shadow}
Y
NOW="$(python3 -c 'import datetime as d;print(d.datetime.now(d.timezone.utc).isoformat(timespec="seconds"))')"
OLD="$(python3 -c 'import datetime as d;print((d.datetime.now(d.timezone.utc)-d.timedelta(days=3)).isoformat(timespec="seconds"))')"
# The Stop hook's cache: one checkout, m2 healthy, m6 saturated at checkout scope.
cache(){ cat > "$WS/.control/leverage-state.json" <<J
{"measured_at":"$NOW","sessions_analyzed":2,"window_days":7,
 "metrics":{"m2_tool_error_rate":0.01,"m6_meta_work_session_ratio":1.0},
 "raw":{"meta_sessions":2,"product_sessions":0},
 "closure":{"closed":true,"sensor_live":true,"reference_authored":true}}
J
}
ship(){ printf '{"measured_at":"%s","m6s_meta_work_ship_ratio":null,"raw":{},"m7_outward_artifact_floor":%s,"outward":{"merged_product_prs":%s}}\n' "$1" "$2" "$3" > "$WS/.control/leverage-ship-state.json"; }
brief(){ python3 -I "$SENSOR" --workspace "$WS" --brief --cached --no-store 2>/dev/null; }

echo "== SessionStart: an outward-empty window steers the agent =="
cache; ship "$NOW" 0.0 0
B="$(brief)"
has "m7 is the worst gap" "Worst gap [ALERT] outward_artifact_floor (L3) = 0.0" "$B"
has "its actuator is injected" "Corrective actuator: outward-empty week" "$B"

echo "== SessionStart: shipping satisfies it =="
cache; ship "$NOW" 1.0 12
B="$(brief)"
has "within target once measured" "All graded setpoints within target." "$B"
hasnt "no unmeasured warning when m7 was read" "UNMEASURED" "$B"

echo "== a declared setpoint with no reading is announced, never certified =="
cache; rm -f "$WS/.control/leverage-ship-state.json"
B="$(brief)"
hasnt "no compliance claim" "All graded setpoints within target." "$B"
has "names the unread setpoint" "declared but UNMEASURED (no sensor reading this window): outward_artifact_floor (L3)" "$B"
cache; ship "$NOW" null 0
B="$(brief)"
has "a null m7 (gh outage) reads as unmeasured" "UNMEASURED" "$B"
cache; ship "$OLD" 0.0 0
B="$(brief)"
has "a stale (3-day) ship state is not used" "UNMEASURED" "$B"
hasnt "and cannot fire the actuator" "Corrective actuator" "$B"
hasnt "shadow setpoints are not reported as unmeasured" "agent_idle_share (L1)" "$B"

echo "== the cached path never keeps a reading its source no longer supports (round 1) =="
# A Stop-time cache that already carries m7 = 1.0 and fleet values.
cache_with_external(){ cat > "$WS/.control/leverage-state.json" <<J
{"measured_at":"$NOW","sessions_analyzed":2,"window_days":7,
 "metrics":{"m2_tool_error_rate":0.01,"m6_meta_work_session_ratio":0.2,"m7_outward_artifact_floor":1.0,"w1_agent_idle_share":0.3},
 "raw":{"meta_sessions":2,"product_sessions":0,"m6_scope":"fleet","m6_checkout":1.0},
 "closure":{"closed":true,"sensor_live":true,"reference_authored":true}}
J
}
for state in missing null stale; do
  cache_with_external
  case $state in
    missing) rm -f "$WS/.control/leverage-ship-state.json" ;;
    null)    ship "$NOW" null 0 ;;
    stale)   ship "$OLD" 1.0 5 ;;
  esac
  B="$(brief)"
  has "cached m7=1.0 with $state ship state -> UNMEASURED" "UNMEASURED" "$B"
  hasnt "cached m7=1.0 with $state ship state -> no compliance claim" "All graded setpoints within target." "$B"
done
cache_with_external; ship "$NOW" 1.0 2; rm -f "$WS/.control/leverage-fleet-state.json"
J="$(python3 -I - "$SENSOR" "$WS" <<'PY'
import importlib.util, json, sys
spec = importlib.util.spec_from_file_location("s", sys.argv[1]); m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
st = json.load(open(sys.argv[2] + "/.control/leverage-state.json"))
m.clear_external(st["metrics"], st["raw"]); m.merge_fleet(st["metrics"], st["raw"], sys.argv[2])
print(st["metrics"].get("m6_meta_work_session_ratio"), st["raw"].get("m6_scope"), "w1_agent_idle_share" in st["metrics"])
PY
)"
[ "$J" = "1.0 None False" ] && ok "fleet state gone -> m6 back to checkout value, w1 dropped" || bad "fleet retention: $J"

echo "== a graded setpoint present with a null value is unmeasured, not certified (round 1) =="
cat > "$WS/.control/leverage-state.json" <<J
{"measured_at":"$NOW","sessions_analyzed":2,"window_days":7,
 "metrics":{"m2_tool_error_rate":null,"m6_meta_work_session_ratio":1.0},
 "raw":{},"closure":{"closed":true,"sensor_live":true,"reference_authored":true}}
J
ship "$NOW" 1.0 3
B="$(brief)"
hasnt "null graded m2 blocks the compliance claim" "All graded setpoints within target." "$B"
has "and names it" "tool_error_rate (L0)" "$B"

echo "== an alarm is only acted on while its measurement is recent (round 2) =="
agoiso(){ python3 -c "import datetime as d,sys;print((d.datetime.now(d.timezone.utc)-d.timedelta(hours=float(sys.argv[1]))).isoformat(timespec='seconds'))" "$1"; }
ship2(){ printf '{"measured_at":"%s","m6s_meta_work_ship_ratio":null,"raw":{},"m7_outward_artifact_floor":%s,"outward":{"window_days":7,"latest_artifact_at":%s}}\n' "$1" "$2" "$3" > "$WS/.control/leverage-ship-state.json"; }
cache; ship2 "$(agoiso 7)" 0.0 null
B="$(brief)"
has "a 7 h old outward-empty reading is not acted on" "UNMEASURED" "$B"
hasnt "and fires no actuator" "Corrective actuator" "$B"
cache; ship2 "$(agoiso 1)" 0.0 null
B="$(brief)"
has "a 1 h old outward-empty reading fires" "Worst gap [ALERT] outward_artifact_floor" "$B"
has "and says how old it is" "measured 1.0 h ago" "$B"
cache; ship2 "$(agoiso 1)" 1.0 "\"$(agoiso 192)\""
B="$(brief)"
has "a 1.0 whose newest artifact aged out of the window becomes 0.0" "Worst gap [ALERT] outward_artifact_floor" "$B"
cache; ship2 "$(agoiso 40)" 1.0 "\"$(agoiso 48)\""
B="$(brief)"
has "a 40 h old 1.0 whose artifact is 2 days old still holds" "All graded setpoints within target." "$B"

echo "== Stop: a fresh fleet scan replaces the one-checkout m6 =="
ship "$NOW" 1.0 3
cat > "$WS/.control/leverage-fleet-state.json" <<J
{"measured_at":"$NOW","metrics":{"m6_meta_work_session_ratio":0.2,"w1_agent_idle_share":0.35,
 "w2_reply_latency_p50_min":5.5,"w3_waiting_sessions_mean":1.1},
 "raw":{"transcripts_read":{"claude":10,"codex":5},"m6_tool_edits_only":0.25,"unclassified_sessions":0,"unclassified_repos":{}}}
J
EMPTY_GLOB="$TMP/none/*.jsonl"
J="$(python3 -I "$SENSOR" --workspace "$WS" --transcripts "$EMPTY_GLOB" --json --no-store 2>/dev/null)"
g(){ python3 -c "import json,sys;d=json.load(sys.stdin);print(d$1)" <<<"$J"; }
[ "$(g "['metrics']['m6_meta_work_session_ratio']")" = "0.2" ] && ok "m6 carries the fleet value" || bad "m6 = $(g "['metrics']['m6_meta_work_session_ratio']")"
[ "$(g "['raw']['m6_scope']")" = "fleet" ] && ok "raw says the scope" || bad "no m6_scope"
[ "$(g "['metrics']['w1_agent_idle_share']")" = "0.35" ] && ok "wait time merged" || bad "w1 missing"
[ "$(g "['metrics']['m7_outward_artifact_floor']")" = "1.0" ] && ok "m7 merged at Stop" || bad "m7 missing at Stop"
[ "$(g "['closure']['levels']['L3']['live']")" = "True" ] && ok "L3 live from fleet/m7 evidence with zero local sessions" || bad "L3 not live"
H="$(python3 -I "$SENSOR" --workspace "$WS" --transcripts "$EMPTY_GLOB" --no-store 2>/dev/null)"
has "human view states the m6 scope" "m6 scope: fleet" "$H"
rm -f "$WS/.control/leverage-fleet-state.json"
J="$(python3 -I "$SENSOR" --workspace "$WS" --transcripts "$EMPTY_GLOB" --json --no-store 2>/dev/null)"
[ "$(g "['raw'].get('m6_scope')")" = "None" ] && ok "without fleet state m6 stays at checkout scope" || bad "scope claimed without fleet state"

echo "leverage-l3-wiring: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
