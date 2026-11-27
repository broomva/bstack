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

# shellcheck source=scripts/lib/broomva-paths.sh
. "$REPO/scripts/lib/broomva-paths.sh"
export BROOMVA_HOME="$TMP/broomva-home"
WS="$TMP/ws"; CTRL="$WS/$BROOMVA_REPO_LEGACY_DIR"; mkdir -p "$CTRL"
SETPOINTS="$(broomva_repo_path leverage_setpoints "$WS")"
STATE_FILE="$(broomva_repo_path leverage_state "$WS")"
SHIP_STATE="$(broomva_repo_path leverage_ship_state "$WS")"
FLEET_STATE="$(broomva_repo_path leverage_fleet_state "$WS")"
cat > "$SETPOINTS" <<'Y'
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
cache(){ cat > "$STATE_FILE" <<J
{"measured_at":"$NOW","sessions_analyzed":2,"window_days":7,
 "metrics":{"m2_tool_error_rate":0.01,"m6_meta_work_session_ratio":1.0},
 "raw":{"meta_sessions":2,"product_sessions":0},
 "closure":{"closed":true,"sensor_live":true,"reference_authored":true}}
J
}
# ship MEASURED_AT M7 MERGED — as the sensor writes it: a 1.0 carries its newest
# artifact's time (here: the measurement time), a 0.0 none, plus outward_config.
DEF_RECEIPTS="$(python3 -c 'import os; print(os.path.expanduser("~/.config/broomva/door/receipts.jsonl"))')"
DEF_OUTWARD_CFG="{\"window_days\":7.0,\"product_repos\":[],\"author_allowlist\":[],\"door_receipts\":\"$DEF_RECEIPTS\"}"
DEF_FLEET_CFG='{"window_days":7.0,"product_repos":[],"meta_repos":[],"external_repos":[],"scratch_prefixes":["/private/tmp","/tmp"],"wait_cap_minutes":30.0}'
ship(){ local la=null; [ "$2" = "1.0" ] && la="\"$1\""
  printf '{"measured_at":"%s","outward_config":%s,"m6s_meta_work_ship_ratio":null,"raw":{},"m7_outward_artifact_floor":%s,"outward":{"merged_product_prs":%s,"window_days":7,"latest_artifact_at":%s,"complete_negative_coverage":true}}\n' "$1" "$DEF_OUTWARD_CFG" "$2" "$3" "$la" > "$SHIP_STATE"; }
# an unsigned legacy record (written before outward_config / latest_artifact_at existed)
ship_legacy(){ printf '{"measured_at":"%s","m6s_meta_work_ship_ratio":null,"raw":{},"m7_outward_artifact_floor":%s,"outward":{"merged_product_prs":0}}\n' "$1" "$2" > "$SHIP_STATE"; }
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
cache; rm -f "$SHIP_STATE"
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
cache_with_external(){ cat > "$STATE_FILE" <<J
{"measured_at":"$NOW","sessions_analyzed":2,"window_days":7,
 "metrics":{"m2_tool_error_rate":0.01,"m6_meta_work_session_ratio":0.2,"m7_outward_artifact_floor":1.0,"w1_agent_idle_share":0.3},
 "raw":{"meta_sessions":2,"product_sessions":0,"m6_scope":"fleet","m6_checkout":1.0},
 "closure":{"closed":true,"sensor_live":true,"reference_authored":true}}
J
}
for state in missing null stale; do
  cache_with_external
  case $state in
    missing) rm -f "$SHIP_STATE" ;;
    null)    ship "$NOW" null 0 ;;
    stale)   ship "$OLD" 1.0 5 ;;
  esac
  B="$(brief)"
  has "cached m7=1.0 with $state ship state -> UNMEASURED" "UNMEASURED" "$B"
  hasnt "cached m7=1.0 with $state ship state -> no compliance claim" "All graded setpoints within target." "$B"
done
cache_with_external; ship "$NOW" 1.0 2; rm -f "$FLEET_STATE"
J="$(python3 -I - "$SENSOR" "$WS" "$STATE_FILE" <<'PY'
import importlib.util, json, sys
spec = importlib.util.spec_from_file_location("s", sys.argv[1]); m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
st = json.load(open(sys.argv[3]))
m.clear_external(st["metrics"], st["raw"]); m.merge_fleet(st["metrics"], st["raw"], sys.argv[2])
print(st["metrics"].get("m6_meta_work_session_ratio"), st["raw"].get("m6_scope"), "w1_agent_idle_share" in st["metrics"])
PY
)"
[ "$J" = "1.0 None False" ] && ok "fleet state gone -> m6 back to checkout value, w1 dropped" || bad "fleet retention: $J"

echo "== a graded setpoint present with a null value is unmeasured, not certified (round 1) =="
cat > "$STATE_FILE" <<J
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
# ship2 MEASURED_AT M7 LATEST_ARTIFACT_AT [COMPLETE_NEGATIVE_COVERAGE=true]
ship2(){ printf '{"measured_at":"%s","outward_config":%s,"m6s_meta_work_ship_ratio":null,"raw":{},"m7_outward_artifact_floor":%s,"outward":{"window_days":7,"latest_artifact_at":%s,"complete_negative_coverage":%s}}\n' "$1" "$DEF_OUTWARD_CFG" "$2" "$3" "${4:-true}" > "$SHIP_STATE"; }
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

echo "== proving nothing shipped needs every source read (round 3) =="
cache; ship2 "$(agoiso 1)" 1.0 "\"$(agoiso 192)\"" false
B="$(brief)"
has "an aged-out 1.0 from a PARTIAL scan is unmeasured, not an alarm" "UNMEASURED" "$B"
hasnt "and fires no actuator" "Corrective actuator" "$B"
cache; ship2 "$(agoiso 1)" 0.0 null false
B="$(brief)"
has "a 0.0 from a partial scan is unmeasured" "UNMEASURED" "$B"
cache; ship2 "$(agoiso 1)" 1.0 '"not-a-time"'
B="$(brief)"
has "an unparseable artifact time is unmeasured" "UNMEASURED" "$B"

echo "== review: unsigned legacy records fail closed, and cached closure recomputes =="
cache; ship_legacy "$NOW" 0.0
B="$(brief)"
has "an unsigned legacy 0.0 is rejected -> unmeasured" "UNMEASURED" "$B"
hasnt "and fires no actuator" "Corrective actuator" "$B"
cache; ship_legacy "$NOW" 1.0
B="$(brief)"
has "an unsigned legacy 1.0 is rejected -> unmeasured (fail closed)" "UNMEASURED" "$B"
hasnt "unsigned legacy 1.0 never certifies compliance" "All graded setpoints within target." "$B"
# Stop wrote a closure with L3 NOT live; the fresh ship state now carries m7.
cat > "$STATE_FILE" <<J
{"measured_at":"$NOW","sessions_analyzed":2,"window_days":7,
 "metrics":{"m2_tool_error_rate":0.01,"m6_meta_work_session_ratio":null},
 "raw":{"meta_sessions":0,"product_sessions":0,"tool_results":5},
 "closure":{"closed":false,"sensor_live":true,"reference_authored":true,
   "levels":{"L0":{"live":true},"L1":{"live":true},"L2":{"live":true},"L3":{"live":false}}}}
J
ship "$NOW" 1.0 4
B="$(brief)"
hasnt "the cached closure is recomputed after the merge (L3 now live)" "levels not all live" "$B"

echo "== Stop: a fresh fleet scan replaces the one-checkout m6 =="
ship "$NOW" 1.0 3
cat > "$FLEET_STATE" <<J
{"measured_at":"$NOW","config":$DEF_FLEET_CFG,"metrics":{"m6_meta_work_session_ratio":0.2,"w1_agent_idle_share":0.35,
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
rm -f "$FLEET_STATE"
J="$(python3 -I "$SENSOR" --workspace "$WS" --transcripts "$EMPTY_GLOB" --json --no-store 2>/dev/null)"
[ "$(g "['raw'].get('m6_scope')")" = "None" ] && ok "without fleet state m6 stays at checkout scope" || bad "scope claimed without fleet state"

echo "== round 5: cached external readings must match current configuration =="
SHIP_SENSOR="$REPO/scripts/leverage-ship-sensor.py"
RECEIPTS_A="$TMP/receipts-a.jsonl"
RECEIPTS_B="$TMP/receipts-b.jsonl"
echo "{\"at\":\"$NOW\",\"kind\":\"deploy\",\"ref\":\"prod\"}" > "$RECEIPTS_A"
: > "$RECEIPTS_B"
write_cfg_setpoints() {
  local win="$1" prod="$2" meta="$3" author="$4" receipts="$5"
  cat > "$SETPOINTS" <<Y
window_days: $win
fleet:
  product_repos: [$prod]
  meta_repos: [$meta]
  author_allowlist: [$author]
  door_receipts: "$receipts"
metrics:
  - id: m2
    name: tool_error_rate
    direction: lower_is_better
    target: 0.05
    alert: 0.15
    level: L1
    actuator: "fix tools"
  - id: m6
    name: meta_work_session_ratio
    direction: lower_is_better
    target: 0.5
    alert: 0.8
    level: L3
    actuator: "stop meta"
  - id: m7
    name: outward_artifact_floor
    direction: higher_is_better
    target: 1.0
    alert: 0.0
    level: L3
    actuator: "ship outward"
Y
}
write_cfg_setpoints 7 "acme/app" "acme/meta" "alice" "$RECEIPTS_A"
cat > "$SHIP_STATE" <<J
{"measured_at":"$NOW","window_days":7,"shadow":true,"gh_ok":true,"repos":[],
 "outward_config":{"window_days":7.0,"product_repos":["acme/app"],"author_allowlist":["alice"],"door_receipts":"$RECEIPTS_A"},
 "m6s_meta_work_ship_ratio":null,"raw":{},
 "m7_outward_artifact_floor":1.0,
 "outward":{"window_days":7,"merged_product_prs":1,"door_receipts":1,
   "latest_artifact_at":"$NOW","complete_negative_coverage":true}}
J
cat > "$FLEET_STATE" <<J
{"measured_at":"$NOW","window_days":7,
 "config":{"window_days":7.0,"product_repos":["acme/app"],"meta_repos":["acme/meta"],
   "external_repos":[],"scratch_prefixes":["/private/tmp","/tmp"],"wait_cap_minutes":30.0},
 "metrics":{"m6_meta_work_session_ratio":0.25,"w1_agent_idle_share":0.2},
 "raw":{"transcripts_read":{"claude":2,"codex":0},"unclassified_sessions":0,"unclassified_repos":{}}}
J
cache
J="$(python3 -I "$SENSOR" --workspace "$WS" --transcripts "$EMPTY_GLOB" --json --no-store 2>/dev/null)"
[ "$(g "['metrics'].get('m7_outward_artifact_floor')")" = "1.0" ] && ok "matching outward_config merges m7" || bad "m7 did not merge on match"
[ "$(g "['raw'].get('m6_scope')")" = "fleet" ] && ok "matching fleet config merges m6" || bad "fleet did not merge on match"

# 1) Window change rejects both cached m7 and cached fleet m6 (and nulls checkout m6 fallback)
write_cfg_setpoints 14 "acme/app" "acme/meta" "alice" "$RECEIPTS_A"
J="$(python3 -I "$SENSOR" --workspace "$WS" --transcripts "$EMPTY_GLOB" --json --no-store 2>/dev/null)"
[ "$(g "['metrics'].get('m7_outward_artifact_floor')")" = "None" ] && ok "window change rejects cached m7" || bad "stale-window m7 leaked"
[ "$(g "['raw'].get('m6_scope')")" = "fleet_unmeasured" ] && [ "$(g "['metrics'].get('m6_meta_work_session_ratio')")" = "None" ] && ok "window change rejects cached fleet m6 and nulls checkout fallback" || bad "stale-window fleet leaked"
B="$(brief)"
has "cached brief drops stale-window m7 -> UNMEASURED" "UNMEASURED" "$B"

# 2) product_repos change rejects both cached m7 and cached fleet m6 (and nulls checkout m6 fallback)
write_cfg_setpoints 7 "acme/other-app" "acme/meta" "alice" "$RECEIPTS_A"
J="$(python3 -I "$SENSOR" --workspace "$WS" --transcripts "$EMPTY_GLOB" --json --no-store 2>/dev/null)"
[ "$(g "['metrics'].get('m7_outward_artifact_floor')")" = "None" ] && ok "product_repos change rejects cached m7" || bad "stale-repo m7 leaked"
[ "$(g "['raw'].get('m6_scope')")" = "fleet_unmeasured" ] && [ "$(g "['metrics'].get('m6_meta_work_session_ratio')")" = "None" ] && ok "product_repos change rejects cached fleet m6 and nulls checkout fallback" || bad "stale-repo fleet leaked"

# 3) author_allowlist change rejects cached m7 (fleet m6 does not depend on author_allowlist)
write_cfg_setpoints 7 "acme/app" "acme/meta" "bob" "$RECEIPTS_A"
J="$(python3 -I "$SENSOR" --workspace "$WS" --transcripts "$EMPTY_GLOB" --json --no-store 2>/dev/null)"
[ "$(g "['metrics'].get('m7_outward_artifact_floor')")" = "None" ] && ok "author_allowlist change rejects cached m7" || bad "stale-author m7 leaked"

# 4) door_receipts change rejects cached m7
write_cfg_setpoints 7 "acme/app" "acme/meta" "alice" "$RECEIPTS_B"
J="$(python3 -I "$SENSOR" --workspace "$WS" --transcripts "$EMPTY_GLOB" --json --no-store 2>/dev/null)"
[ "$(g "['metrics'].get('m7_outward_artifact_floor')")" = "None" ] && ok "door_receipts change rejects cached m7" || bad "stale-receipts m7 leaked"

# 5) leverage-ship-sensor.py --throttle skips when config matches, recomputes when config changes
cat > "$SETPOINTS" <<Y
window_days: 7
fleet:
  product_repos: []
  author_allowlist: []
  door_receipts: "$RECEIPTS_A"
ship_signal:
  repos: []
Y
python3 -I "$SHIP_SENSOR" --workspace "$WS" >/dev/null
t_before="$(stat -c %Y "$SHIP_STATE" 2>/dev/null || stat -f %m "$SHIP_STATE")"; sleep 1
python3 -I "$SHIP_SENSOR" --workspace "$WS" --throttle 3600 >/dev/null
t_same="$(stat -c %Y "$SHIP_STATE" 2>/dev/null || stat -f %m "$SHIP_STATE")"
[ "$t_same" = "$t_before" ] && ok "ship-sensor throttle skips unchanged config" || bad "ship-sensor did not throttle unchanged config"
cat > "$SETPOINTS" <<Y
window_days: 7
fleet:
  product_repos: []
  author_allowlist: []
  door_receipts: "$RECEIPTS_B"
ship_signal:
  repos: []
Y
sleep 1
python3 -I "$SHIP_SENSOR" --workspace "$WS" --throttle 3600 >/dev/null
t_changed="$(stat -c %Y "$SHIP_STATE" 2>/dev/null || stat -f %m "$SHIP_STATE")"
[ "$t_changed" != "$t_before" ] && ok "ship-sensor throttle recomputes when door_receipts changes" || bad "ship-sensor throttle kept stale config"

echo "== round 6: default door_receipts parity, window_days null, whitespace, .broomva/, partial coverage =="
FLEET_SENSOR="$REPO/scripts/leverage-fleet-sensor.py"
# 6a) Default door_receipts matches between leverage-ship-sensor.py and leverage-sensor.py
R6_OUT="$(python3 -I - "$SENSOR" "$SHIP_SENSOR" <<'PY'
import importlib.util, sys
def load(p):
    sp = importlib.util.spec_from_file_location("m", p); m = importlib.util.module_from_spec(sp); sp.loader.exec_module(m); return m
s, sh = load(sys.argv[1]), load(sys.argv[2])
print(s._DEFAULT_DOOR_RECEIPTS == sh.DEFAULT_DOOR_RECEIPTS, s._outward_config_sig({}, 7) == sh.outward_config_signature({}, 7))
PY
)"
[ "$R6_OUT" = "True True" ] && ok "default door_receipts and outward_config signature match across sensors" || bad "receipt path mismatch: $R6_OUT"

# 6b) window_days: null does not crash any of the 3 CLI scripts
cat > "$SETPOINTS" <<Y
window_days: null
fleet:
  product_repos: []
  meta_repos: []
  author_allowlist: []
ship_signal:
  repos: []
metrics: []
Y
python3 -I "$SENSOR" --workspace "$WS" --transcripts "$EMPTY_GLOB" --json --no-store >/dev/null && ok "leverage-sensor handles window_days: null" || bad "leverage-sensor crashed on window_days: null"
python3 -I "$SHIP_SENSOR" --workspace "$WS" --json --no-store >/dev/null && ok "leverage-ship-sensor handles window_days: null" || bad "leverage-ship-sensor crashed on window_days: null"
python3 -I "$FLEET_SENSOR" --workspace "$WS" --claude-root "$TMP/none" --codex-root "$TMP/none" --json --no-store >/dev/null && ok "leverage-fleet-sensor handles window_days: null" || bad "leverage-fleet-sensor crashed on window_days: null"

# 6c) Whitespace in author_allowlist matches in compute_outward + invalid mergedAt fails closed + chmod 000 parent dir is unreadable
NOSEARCH="$TMP/nosearch"; mkdir -p "$NOSEARCH"; echo "{\"at\":\"$NOW\"}" > "$NOSEARCH/r.jsonl"; chmod 000 "$NOSEARCH"
R6_M7="$(python3 -I - "$SHIP_SENSOR" "$NOW" "$NOSEARCH/r.jsonl" <<'PY'
import importlib.util, sys
sp = importlib.util.spec_from_file_location("sh", sys.argv[1]); sh = importlib.util.module_from_spec(sp); sp.loader.exec_module(sh)
now_iso, bad_path = sys.argv[2], sys.argv[3]
# Whitespace author matches
res_ws = sh.compute_outward({"product_repos": [" acme/app "], "author_allowlist": [" alice "]}, 7,
    fetch=lambda r, s: [{"number": 1, "mergedAt": now_iso, "author": {"login": "Alice", "is_bot": False}}])
# Invalid mergedAt on qualifying PR fails closed -> m7=None, complete_negative_coverage=False
res_bad_ts = sh.compute_outward({"product_repos": ["acme/app"], "author_allowlist": ["alice"], "door_receipts": "/nonexistent-r6-file"}, 7,
    fetch=lambda r, s: [{"number": 2, "mergedAt": "not-a-timestamp", "author": {"login": "alice", "is_bot": False}}])
# Unsearchable receipt parent dir -> door_receipts_status=='unreadable', m7=None
res_perm = sh.compute_outward({"product_repos": ["acme/app"], "author_allowlist": ["alice"], "door_receipts": bad_path}, 7,
    fetch=lambda r, s: [])
print(res_ws["m7_outward_artifact_floor"],
      res_bad_ts["m7_outward_artifact_floor"], res_bad_ts["outward"]["unparsed_merges"], res_bad_ts["outward"]["complete_negative_coverage"],
      res_perm["m7_outward_artifact_floor"], res_perm["outward"]["door_receipts_status"], res_perm["outward"]["complete_negative_coverage"])
PY
)"
chmod 755 "$NOSEARCH"
[ "$R6_M7" = "1.0 None 1 False None unreadable False" ] && ok "whitespace allowlist, invalid mergedAt, and unsearchable receipt path all fail closed" || bad "R6 m7 checks failed: $R6_M7"

# 6d) .broomva/-only migrated workspace works across hook guard + all 3 sensors
WS_MIG="$TMP/ws-mig"; mkdir -p "$WS_MIG/.broomva"
cat > "$WS_MIG/.broomva/leverage-setpoints.yaml" <<Y
window_days: 7
fleet:
  product_repos: ["acme/app"]
  meta_repos: ["acme/meta"]
  author_allowlist: ["alice"]
  door_receipts: "$RECEIPTS_A"
ship_signal:
  repos: []
metrics:
  - {id: m7, name: outward_artifact_floor, level: L3, direction: higher_is_better, target: 1.0, alert: 0.99}
Y
GUARD_OUT="$(BSTACK_WORKSPACE="$WS_MIG" bash "$REPO/hooks/bstack-hook-guard.sh" echo ok-migrated)"
[ "$GUARD_OUT" = "ok-migrated" ] && ok "bstack-hook-guard accepts .broomva/-only workspace" || bad "hook guard rejected .broomva/ workspace: $GUARD_OUT"
python3 -I "$SHIP_SENSOR" --workspace "$WS_MIG" --outward-fixture /dev/null --json --no-store 2>/dev/null || true
# Write ship state via broomva_paths and read it via leverage-sensor.py on the .broomva/-only workspace
MIG_SHIP="$(broomva_repo_path leverage_ship_state "$WS_MIG")"
mkdir -p "$(dirname "$MIG_SHIP")"
cat > "$MIG_SHIP" <<J
{"measured_at":"$NOW","window_days":7,"outward_config":{"window_days":7.0,"product_repos":["acme/app"],"author_allowlist":["alice"],"door_receipts":"$RECEIPTS_A"},"m6s_meta_work_ship_ratio":null,"raw":{},"m7_outward_artifact_floor":1.0,"outward":{"window_days":7,"merged_product_prs":0,"door_receipts":1,"latest_artifact_at":"$NOW","complete_negative_coverage":true}}
J
J="$(python3 -I "$SENSOR" --workspace "$WS_MIG" --transcripts "$EMPTY_GLOB" --json --no-store 2>/dev/null)"
[ "$(g "['metrics'].get('m7_outward_artifact_floor')")" = "1.0" ] && ok "leverage-sensor reads .broomva/leverage-setpoints.yaml + broomva_paths ship state" || bad "migrated workspace m7 failed"

echo "leverage-l3-wiring: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
