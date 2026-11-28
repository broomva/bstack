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
DEF_RECEIPTS="$(python3 -c 'import os; print(os.path.normpath(os.path.abspath(os.path.expanduser("~/.config/broomva/door/receipts.jsonl"))))')"
DEF_OUTWARD_CFG="{\"schema_version\":3,\"window_days\":7.0,\"product_repos\":[],\"author_allowlist\":[],\"door_receipts\":\"$DEF_RECEIPTS\"}"
DEF_FLEET_CFG='{"schema_version":3,"window_days":7.0,"product_repos":[],"meta_repos":[],"external_repos":[],"scratch_prefixes":["/private/tmp","/tmp"],"wait_cap_minutes":30.0}'
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
 "outward_config":{"schema_version":3,"window_days":7.0,"product_repos":["acme/app"],"author_allowlist":["alice"],"door_receipts":"$RECEIPTS_A"},
 "m6s_meta_work_ship_ratio":null,"raw":{},
 "m7_outward_artifact_floor":1.0,
 "outward":{"window_days":7,"merged_product_prs":1,"door_receipts":1,
   "latest_artifact_at":"$NOW","complete_negative_coverage":true}}
J
cat > "$FLEET_STATE" <<J
{"measured_at":"$NOW","window_days":7,
 "config":{"schema_version":3,"window_days":7.0,"product_repos":["acme/app"],"meta_repos":["acme/meta"],
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

# 6d) .broomva/-only migrated workspace works across hook guard + all 3 real producer CLIs
WS_MIG="$TMP/ws-mig"; mkdir -p "$WS_MIG/.broomva"
MIG_META="$TMP/mig-meta"; MIG_PROD="$TMP/mig-prod"
mkdir -p "$MIG_META" "$MIG_PROD"
git init -q "$MIG_META" && git -C "$MIG_META" remote add origin "https://github.com/acme/meta.git"
git init -q "$MIG_PROD" && git -C "$MIG_PROD" remote add origin "https://github.com/acme/app.git"
MIG_CR="$TMP/mig-claude"; mkdir -p "$MIG_CR/p"
python3 -I - "$MIG_CR/p/s.jsonl" "$MIG_PROD" <<'PY'
import json, sys, time
from datetime import datetime, timezone
p, prod = sys.argv[1], sys.argv[2]
t0 = datetime.fromtimestamp(time.time() - 300, timezone.utc).isoformat()
with open(p, "w") as f:
    f.write(json.dumps({"type": "assistant", "timestamp": t0, "cwd": prod, "message": {"content": [
        {"type": "tool_use", "name": "Edit", "input": {"file_path": f"{prod}/app.py"}}
    ]}}) + "\n")
PY
cat > "$WS_MIG/.broomva/leverage-setpoints.yaml" <<Y
window_days: 7
fleet:
  product_repos: ["acme/app"]
  meta_repos: ["acme/meta"]
  author_allowlist: ["alice"]
  door_receipts: "$RECEIPTS_A"
  scratch_prefixes: []
ship_signal:
  repos: []
metrics:
  - {id: m6, name: meta_work_session_ratio, level: L3, direction: lower_is_better, target: 0.5, alert: 0.8}
  - {id: m7, name: outward_artifact_floor, level: L3, direction: higher_is_better, target: 1.0, alert: 0.99}
Y
GUARD_OUT="$(BSTACK_WORKSPACE="$WS_MIG" bash "$REPO/hooks/bstack-hook-guard.sh" echo ok-migrated)"
[ "$GUARD_OUT" = "ok-migrated" ] && ok "bstack-hook-guard accepts .broomva/-only workspace" || bad "hook guard rejected .broomva/ workspace: $GUARD_OUT"
# Run real leverage-ship-sensor.py (with real outward-fixture JSON) and store its real output at broomva_paths location
MIG_FX="$TMP/mig-fx.json"; echo '{"acme/app":[]}' > "$MIG_FX"
MIG_SHIP_JSON="$(python3 -I "$SHIP_SENSOR" --workspace "$WS_MIG" --outward-fixture "$MIG_FX" --json --no-store)" \
  && ok "leverage-ship-sensor runs cleanly in .broomva/-only workspace" || bad "leverage-ship-sensor failed in .broomva/ workspace"
MIG_SHIP="$(broomva_repo_path leverage_ship_state "$WS_MIG")"
mkdir -p "$(dirname "$MIG_SHIP")"
printf '%s\n' "$MIG_SHIP_JSON" > "$MIG_SHIP"
# Run real leverage-fleet-sensor.py storing directly into the .broomva/-only workspace
python3 -I "$FLEET_SENSOR" --workspace "$WS_MIG" --claude-root "$MIG_CR" --codex-root "$TMP/none" --json >/dev/null \
  && ok "leverage-fleet-sensor runs and stores cleanly in .broomva/-only workspace" || bad "leverage-fleet-sensor failed in .broomva/ workspace"
MIG_FLEET="$(broomva_repo_path leverage_fleet_state "$WS_MIG")"
[ -f "$MIG_FLEET" ] && ok "leverage-fleet-sensor wrote state to broomva_paths target" || bad "missing $MIG_FLEET"
J="$(python3 -I "$SENSOR" --workspace "$WS_MIG" --transcripts "$EMPTY_GLOB" --json --no-store 2>/dev/null)"
[ "$(g "['metrics'].get('m7_outward_artifact_floor')")" = "1.0" ] && ok "leverage-sensor reads real ship state in .broomva/ workspace" || bad "migrated workspace m7 failed"
[ "$(g "['raw'].get('m6_scope')")" = "fleet" ] && [ "$(g "['metrics'].get('m6_meta_work_session_ratio')")" = "0.0" ] \
  && ok "leverage-sensor reads real fleet state in .broomva/ workspace" || bad "migrated workspace fleet failed"

echo "== round 7: symlink LayoutError, schema_version, closure L3, malformed inputs, relative receipts, numeric bounds, future ts, hook .broomva/ =="
# 7a) Symlinked repo state root must raise LayoutError and write ZERO files in all 3 sensors
SYM_OUT="$TMP/sym-outside"; SYM_WS="$TMP/sym-ws"
mkdir -p "$SYM_OUT" "$SYM_WS"
SYM_DIR="$(dirname "$(broomva_repo_path leverage_setpoints "$SYM_WS")")"
ln -s "$SYM_OUT" "$SYM_DIR"
R7_SYM="$(python3 -I - "$SENSOR" "$SHIP_SENSOR" "$FLEET_SENSOR" "$SYM_WS" "$EMPTY_GLOB" "$TMP/none" <<'PY'
import subprocess, sys
s, sh, fl, ws, eg, none = sys.argv[1:7]
rc1 = subprocess.run([sys.executable, "-I", s, "--workspace", ws, "--transcripts", eg, "--json"], capture_output=True).returncode
rc2 = subprocess.run([sys.executable, "-I", sh, "--workspace", ws, "--json"], capture_output=True).returncode
rc3 = subprocess.run([sys.executable, "-I", fl, "--workspace", ws, "--claude-root", none, "--codex-root", none, "--json"], capture_output=True).returncode
print(rc1 != 0, rc2 != 0, rc3 != 0)
PY
)"
SYM_FILES="$(ls -A "$SYM_OUT" | wc -l | tr -d ' ')"
[ "$R7_SYM" = "True True True" ] && [ "$SYM_FILES" = "0" ] \
  && ok "symlinked state root fails closed with LayoutError and writes zero files across all 3 sensors" \
  || bad "symlinked state root leaked: rc=$R7_SYM files=$SYM_FILES"

# 7b) Pre-hardening cache (no schema_version) is rejected by leverage-sensor and by producer --throttle
write_cfg_setpoints 7 "acme/app" "acme/meta" "alice" "$RECEIPTS_A"
cat > "$SHIP_STATE" <<J
{"measured_at":"$NOW","window_days":7,"outward_config":{"window_days":7.0,"product_repos":["acme/app"],"author_allowlist":["alice"],"door_receipts":"$RECEIPTS_A"},
 "m6s_meta_work_ship_ratio":null,"raw":{},"m7_outward_artifact_floor":0.0,
 "outward":{"window_days":7,"merged_product_prs":0,"door_receipts":0,"latest_artifact_at":null,"complete_negative_coverage":true}}
J
cat > "$FLEET_STATE" <<J
{"measured_at":"$NOW","window_days":7,"config":{"window_days":7.0,"product_repos":["acme/app"],"meta_repos":["acme/meta"],"external_repos":[],"scratch_prefixes":["/private/tmp","/tmp"],"wait_cap_minutes":30.0},
 "metrics":{"m6_meta_work_session_ratio":1.0},"raw":{"transcripts_read":{"claude":1,"codex":0}}}
J
J="$(python3 -I "$SENSOR" --workspace "$WS" --transcripts "$EMPTY_GLOB" --json --no-store 2>/dev/null)"
[ "$(g "['metrics'].get('m7_outward_artifact_floor')")" = "None" ] && [ "$(g "['metrics'].get('m6_meta_work_session_ratio')")" = "None" ] \
  && ok "unversioned caches are rejected" || bad "unversioned cache accepted"

# 7c) Closure verdict must NOT certify L3.live=True from checkout counts when m6_scope=fleet_unmeasured
rm -f "$SHIP_STATE" "$FLEET_STATE"
LOCAL_TR="$TMP/local-meta-tr"; mkdir -p "$LOCAL_TR"
python3 -I - "$LOCAL_TR/s.jsonl" "$WS" <<'PY'
import json, sys, time
from datetime import datetime, timezone
p, ws = sys.argv[1], sys.argv[2]
t0 = datetime.fromtimestamp(time.time() - 120, timezone.utc).isoformat()
rows = [
    {"type": "user", "timestamp": t0, "message": {"content": "edit scripts"}},
    {"type": "assistant", "timestamp": t0, "message": {"content": [
        {"type": "tool_use", "id": "t1", "name": "Edit", "input": {"file_path": f"{ws}/scripts/a.py"}}
    ]}},
    {"type": "user", "timestamp": t0, "message": {"content": [
        {"type": "tool_result", "tool_use_id": "t1", "content": "ok"}
    ]}},
]
with open(p, "w") as f:
    for r in rows: f.write(json.dumps(r) + "\n")
PY
J="$(python3 -I "$SENSOR" --workspace "$WS" --transcripts "$LOCAL_TR/*.jsonl" --json --no-store 2>/dev/null)"
[ "$(g "['raw'].get('m6_scope')")" = "fleet_unmeasured" ] \
  && [ "$(g "['closure']['levels']['L3']['live']")" = "False" ] \
  && [ "$(g "['closure']['closed']")" = "False" ] \
  && ok "closure_verdict refuses L3.live=True when fleet m6 is unmeasured despite local checkout sessions" \
  || bad "closure_verdict falsely certified L3: scope=$(g "['raw'].get('m6_scope')") L3=$(g "['closure']['levels']['L3']['live']")"

# 7d) Malformed outward fetch shapes, scalar YAML lists, non-dict author/tier_weights, explicit ungated=0.0, future-dated m7
R7_M7="$(python3 -I - "$SHIP_SENSOR" "$SENSOR" "$NOW" <<'PY'
import importlib.util, sys
from datetime import datetime, timedelta, timezone
def load(p):
    sp = importlib.util.spec_from_file_location("m", p); m = importlib.util.module_from_spec(sp); sp.loader.exec_module(m); return m
sh, s = load(sys.argv[1]), load(sys.argv[2])
now_iso = sys.argv[3]
# Dict response and list with non-dict item fail closed -> m7=None, complete_negative_coverage=False
r_dict = sh.compute_outward({"product_repos": ["acme/app"], "author_allowlist": ["alice"], "door_receipts": "/nonexistent-r7"}, 7,
    fetch=lambda r, si: {"error": "invalid shape"})
r_elem = sh.compute_outward({"product_repos": ["acme/app"], "author_allowlist": ["alice"], "door_receipts": "/nonexistent-r7"}, 7,
    fetch=lambda r, si: ["invalid element"])
r_auth_str = sh.compute_outward({"product_repos": ["acme/app"], "author_allowlist": ["alice"], "door_receipts": "/nonexistent-r7"}, 7,
    fetch=lambda r, si: [{"number": 1, "mergedAt": now_iso, "author": "alice"}])
# Scalar author_allowlist: "alice" must NOT iterate chars
r_scalar = sh.compute_outward({"product_repos": ["acme/app"], "author_allowlist": "alice", "door_receipts": "/nonexistent-r7"}, 7,
    fetch=lambda r, si: [])
# Malformed tier_weights: "bad" must not crash classify_prs; explicit ungated: 0.0 must score 0.0 (not 0.5)
r_bad_tw = sh.classify_prs([{"number": 1, "author": {"login": "a"}, "files": [{"path": "apps/x.ts"}], "statusCheckRollup": []}],
    {"tier_weights": "bad", "author_allowlist": ["a"]})
r_zero_ug = sh.classify_prs([{"number": 1, "author": {"login": "a"}, "files": [{"path": "apps/x.ts"}], "statusCheckRollup": []}],
    {"tier_weights": {"green": 1.0, "ungated": 0.0}, "author_allowlist": ["a"]})
# Future-dated cached artifact (>5m ahead) must fail closed in effective_m7
fut = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat(timespec="seconds")
m7_fut = s.effective_m7({"m7_outward_artifact_floor": 1.0, "outward": {"window_days": 7, "latest_artifact_at": fut, "complete_negative_coverage": True}}, 10)
print(r_dict["m7_outward_artifact_floor"], r_dict["outward"]["complete_negative_coverage"],
      r_elem["m7_outward_artifact_floor"], r_elem["outward"]["complete_negative_coverage"],
      r_auth_str["m7_outward_artifact_floor"], r_auth_str["outward"]["complete_negative_coverage"],
      r_scalar["m7_outward_artifact_floor"],
      r_bad_tw["raw"]["product_ship"], r_zero_ug["raw"]["product_ship"],
      m7_fut)
PY
)"
[ "$R7_M7" = "None False None False None False None 0.5 0.0 None" ] \
  && ok "malformed fetch/author/tier_weights, scalar allowlist, explicit ungated=0.0, and future-dated m7 all handled cleanly" \
  || bad "R7 outward/ship edge cases failed: $R7_M7"

# 7e) Relative door_receipts resolves against --workspace from two different cwds with identical signature & value
REL_WS="$TMP/rel-ws"; OTHER_CWD="$TMP/other-cwd"
mkdir -p "$REL_WS/.broomva" "$OTHER_CWD"
echo "{\"at\":\"$NOW\",\"kind\":\"deploy\"}" > "$REL_WS/door.jsonl"
cat > "$REL_WS/.broomva/leverage-setpoints.yaml" <<Y
window_days: 7
fleet:
  product_repos: ["acme/app"]
  author_allowlist: ["alice"]
  door_receipts: "door.jsonl"
Y
REL_FX="$TMP/rel-fx.json"; echo '{"acme/app":[]}' > "$REL_FX"
REL_A="$(cd "$REL_WS" && python3 -I "$SHIP_SENSOR" --workspace "$REL_WS" --outward-fixture "$REL_FX" --json --no-store)"
REL_B="$(cd "$OTHER_CWD" && python3 -I "$SHIP_SENSOR" --workspace "$REL_WS" --outward-fixture "$REL_FX" --json --no-store)"
R7_REL="$(python3 -I - "$REL_A" "$REL_B" <<'PY'
import json, sys
a, b = json.loads(sys.argv[1]), json.loads(sys.argv[2])
print(a["m7_outward_artifact_floor"] == 1.0, b["m7_outward_artifact_floor"] == 1.0,
      a["outward_config"] == b["outward_config"])
PY
)"
[ "$R7_REL" = "True True True" ] \
  && ok "relative door_receipts resolves against --workspace consistently across cwds" \
  || bad "relative door_receipts drifted across cwds: $R7_REL"

# 7f) Huge window_days (10^20 and 401-digit YAML integer) degrades cleanly without crashing or dropping metrics
HUGE_INT="1$(python3 -c 'print("0"*400)')"
cat > "$SETPOINTS" <<Y
window_days: $HUGE_INT
fleet:
  product_repos: []
  meta_repos: []
  author_allowlist: []
ship_signal:
  repos: []
metrics:
  - {id: m2, name: tool_error_rate, level: L1, direction: lower_is_better, target: 0.05, alert: 0.15}
Y
R7_HUGE="$(python3 -I "$SENSOR" --workspace "$WS" --transcripts "$EMPTY_GLOB" --json --no-store 2>/dev/null | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d["window_days"], any(r.get("key")=="m2_tool_error_rate" and r.get("target")==0.05 for r in d["results"]))')"
python3 -I "$SHIP_SENSOR" --workspace "$WS" --json --no-store >/dev/null && SHIP_HUGE=ok || SHIP_HUGE=fail
python3 -I "$FLEET_SENSOR" --workspace "$WS" --claude-root "$TMP/none" --codex-root "$TMP/none" --json --no-store >/dev/null && FLEET_HUGE=ok || FLEET_HUGE=fail
[ "$R7_HUGE" = "7 True" ] && [ "$SHIP_HUGE" = "ok" ] && [ "$FLEET_HUGE" = "ok" ] \
  && ok "401-digit window_days degrades to 7 without crashing or discarding policy metrics" \
  || bad "huge window_days failed: sensor=$R7_HUGE ship=$SHIP_HUGE fleet=$FLEET_HUGE"

# 7g) l3-stability-pretool-hook.sh warns on .broomva/leverage-setpoints.yaml and leverage-fleet-sensor.py in a .broomva/-only workspace
L3_HOOK="$REPO/scripts/l3-stability-pretool-hook.sh"
H_MIG_1="$(BROOMVA_WORKSPACE="$WS_MIG" bash "$L3_HOOK" <<< "{\"tool_name\":\"Edit\",\"tool_input\":{\"file_path\":\"$WS_MIG/.broomva/leverage-setpoints.yaml\"}}")"
H_MIG_2="$(BROOMVA_WORKSPACE="$WS_MIG" bash "$L3_HOOK" <<< "{\"tool_name\":\"Edit\",\"tool_input\":{\"file_path\":\"$WS_MIG/scripts/leverage-fleet-sensor.py\"}}")"
has "l3 hook warns on .broomva/leverage-setpoints.yaml in migrated workspace" "L3" "$H_MIG_1"
has "l3 hook warns on leverage-fleet-sensor.py in migrated workspace" "L3" "$H_MIG_2"

echo "== round 8: migrated audit parity and fail-closed outward evidence =="
BUDGET="$REPO/scripts/compute-budget-status.sh"
AUDIT="$(broomva_repo_path audit "$WS_MIG")"
BUDGET_RC=0
BUDGET_JSON="$(bash "$BUDGET" --workspace="$WS_MIG" --json)" || BUDGET_RC=$?
[ "$BUDGET_RC" -eq 1 ] && [ "$(wc -l < "$AUDIT/l3-edits.jsonl" | tr -d ' ')" = 2 ] &&
  jq -e '.layers[] | select(.id == "L3") | .observed.l3_edits_in_window == 2 and .verdict == "unstable"' \
    <<< "$BUDGET_JSON" >/dev/null \
  && ok "budget reader counts both L3 events at the registered audit path" || bad "migrated audit parity failed"

H_REJECT="$(BROOMVA_WORKSPACE="$SYM_WS" bash "$L3_HOOK" \
  <<< "{\"tool_name\":\"Edit\",\"tool_input\":{\"file_path\":\"$SYM_DIR/leverage-setpoints.yaml\"}}" 2>&1)"
BUDGET_RC=0
B_REJECT="$(bash "$BUDGET" --workspace="$SYM_WS" --trend 2>&1)" || BUDGET_RC=$?
[ "$BUDGET_RC" -eq 2 ] && [ -z "$(ls -A "$SYM_OUT")" ] \
  && ok "hook and budget report refuse a symlinked workspace layout without writing" \
  || bad "symlinked audit layout accepted: $H_REJECT; $B_REJECT"

R8_M7="$(python3 -I - "$SHIP_SENSOR" "$SENSOR" "$TMP" <<'PY'
import importlib.util, json, sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

ship, reader = load("ship", sys.argv[1]), load("reader", sys.argv[2])
now = datetime.now(timezone.utc)
recent = (now - timedelta(minutes=1)).isoformat()
future = (now + timedelta(hours=1)).isoformat()
receipt = Path(sys.argv[3]) / "future-receipts.jsonl"
cfg = {"product_repos": ["acme/app"], "author_allowlist": ["alice"], "door_receipts": str(receipt)}
for row in [
    {"mergedAt": recent},
    {"mergedAt": recent, "author": {"login": ["alice"]}},
    {"mergedAt": future, "author": {"login": "alice"}},
]:
    result = ship.compute_outward(cfg, 7, now=now, fetch=lambda *_: [row])
    assert result["m7_outward_artifact_floor"] is None, result
    assert not result["outward"]["complete_negative_coverage"], result

receipt.write_text(json.dumps({"ts": future}) + "\n")
result = ship.compute_outward(cfg, 7, now=now, fetch=lambda *_: [])
assert result["m7_outward_artifact_floor"] is None, result
sig = ship.outward_config_signature(cfg, 7)
assert reader._config_matches_outward({"outward_config": sig}, {"fleet": cfg}, 7)
assert not reader._config_matches_outward({"outward_config": dict(sig, schema_version=2)}, {"fleet": cfg}, 7)
assert reader.effective_m7({"m7_outward_artifact_floor": 1.0, "outward": {}}, 10) is None
print("ok")
PY
)"
[ "$R8_M7" = ok ] && ok "malformed or future evidence and old positive caches fail closed" \
  || bad "invalid outward evidence was accepted"

echo "leverage-l3-wiring: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
