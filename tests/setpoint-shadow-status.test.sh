#!/usr/bin/env bash
# setpoint-shadow-status.test.sh — BRO-2168.
#
# .control/leverage-setpoints.yaml can stand a setpoint down while its replacement
# calibrates, by giving it `status: shadow-pending-<successor>`. Standing down means
# MEASURED, NOT GRADED: the value is still computed and still shown, but it must
# never rank, never become `worst`, and never emit its actuator.
#
# evaluate() read every setpoint field EXCEPT `status`, so m6 kept firing as an L3
# ALERT — "freeze NEW governance/primitive edits" — from 2026-08-16 onward, steering
# the agent on the authority of a reference the policy file had already retired. A
# declared property with no mechanism binding it is not a property.
#
# The invariant asserted here: a stood-down setpoint is measured and never steers,
# and a live one grades exactly as it did before. Reverting the `setpoint_grading()`
# guard in evaluate() must turn this test red — and specifically must flip case A
# from "shadow" to "alert", not merely crash it.
set -uo pipefail

BSTACK_REPO="$(cd "$(dirname "$0")/.." && pwd)"
SENSOR="$BSTACK_REPO/scripts/leverage-sensor.py"

PASS=0; FAIL=0
ok()  { echo "  ok   $1"; PASS=$((PASS+1)); }
bad() { echo "  FAIL $1"; FAIL=$((FAIL+1)); }

[ -f "$SENSOR" ] || { echo "FATAL: sensor not found at $SENSOR"; exit 1; }

# Drive the pure evaluate() directly. The module name carries a hyphen, so it is
# loaded by path rather than imported. Every case below feeds the SAME metric value
# (0.96, far past m6's real alert threshold of 0.75) and varies ONLY the setpoint
# `status`, so any difference in outcome is attributable to the guard and nothing else.
probe() {
    # probe <status-yaml-value-or-__ABSENT__> -> "<status>|<is_worst>|<has_actuator>|<has_note>|<value>"
    python3 - "$SENSOR" "$1" <<'PY'
import importlib.util, sys, json
sensor_path, status_arg = sys.argv[1], sys.argv[2]
spec = importlib.util.spec_from_file_location("levsensor", sensor_path)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

sp = {"id": "m6", "name": "meta_work_session_ratio", "level": "L3",
      "direction": "lower_is_better", "target": 0.50, "alert": 0.75,
      "actuator": "anti-windup: freeze NEW governance/primitive edits"}
if status_arg != "__ABSENT__":
    sp["status"] = status_arg

results, worst = mod.evaluate({"m6_meta_work_session_ratio": 0.96}, {"metrics": [sp]})
row = next(r for r in results if r["key"] == "m6_meta_work_session_ratio")
print("|".join([
    str(row.get("status")),
    "worst" if (worst and worst.get("key") == row["key"]) else "not-worst",
    "actuator" if row.get("actuator") else "no-actuator",
    "note" if row.get("status_note") else "no-note",
    str(row.get("value")),
]))
PY
}

# run_policy_docs <workdir> <label> — reads '%%'-separated YAML documents on stdin,
# writes each to <workdir>/.control/leverage-setpoints.yaml, and asserts the sensor
# neither crashes nor goes silent in each output mode.
#
# The previous version read with `while IFS= read -r doc`, which splits a MULTI-LINE
# document into separate one-line documents -- `metrics:` on its own is harmless, so
# every multi-line case passed vacuously. Found when a mutation that genuinely breaks
# `level: [L0]` left the section green.
run_policy_docs() {
    local wd="$1" label="$2" doc="" failed=0 n=0
    _check_doc() {
        [ -z "$1" ] && return 0
        n=$((n+1))
        if ! printf '%s\n' "$1" > "$wd/.control/leverage-setpoints.yaml"; then
            bad "$label: could not write policy for doc #$n (every check below would pass vacuously)"
            failed=1; return 0
        fi
        [ -s "$wd/.control/leverage-setpoints.yaml" ] || {
            bad "$label: wrote an EMPTY policy for doc #$n"; failed=1; return 0; }
        local mode out rc
        for mode in "--brief" ""; do
            out="$(python3 "$SENSOR" --workspace "$wd" $mode --no-store 2>/dev/null)"; rc=$?
            if [ $rc -ne 0 ]; then bad "$label: crash rc=$rc on doc #$n mode[$mode]"; failed=1
            elif [ -z "$out" ]; then bad "$label: empty output on doc #$n mode[$mode]"; failed=1; fi
        done
    }
    while IFS= read -r line; do
        if [ "$line" = "%%" ]; then _check_doc "$doc"; doc=""; else
            [ -z "$doc" ] && doc="$line" || doc="$doc
$line"
        fi
    done
    _check_doc "$doc"
    [ "$failed" = "0" ] && ok "$label: $n documents x 2 modes, no crash and no silence"
    return 0
}

echo "== A. a stood-down setpoint is measured, never graded, never steers =="
A="$(probe 'shadow-pending-m7')"
[ "${A%%|*}" = "shadow" ]           && ok "status is 'shadow' (was 'alert')"        || bad "status: $A"
case "$A" in *"|not-worst|"*) ok "never becomes \`worst\`";;      *) bad "became worst: $A";; esac
case "$A" in *"|no-actuator|"*) ok "carries no actuator (cannot steer)";; *) bad "carried an actuator: $A";; esac
case "$A" in *"|0.96") ok "value still measured and retained (0.96)";; *) bad "value lost: $A";; esac

# POLARITY ARM. Without this, case A proves nothing: it would pass identically if the
# fixture value were simply never alarming. The ONLY delta from A is the absent status.
echo "== B. polarity control — the SAME value with no status DOES alarm =="
B="$(probe '__ABSENT__')"
[ "${B%%|*}" = "alert" ]            && ok "absent status still grades → 'alert'"    || bad "regression, live path: $B"
case "$B" in *"|worst|"*) ok "still becomes \`worst\` (fixture is genuinely alarming)";; *) bad "not worst: $B";; esac
case "$B" in *"|actuator|"*) ok "still emits its actuator";;      *) bad "lost actuator: $B";; esac

echo "== C. an explicitly live setpoint grades =="
C="$(probe 'live')"
[ "${C%%|*}" = "alert" ]            && ok "status: live → graded"                   || bad "live not graded: $C"

echo "== D. bare lifecycle words stand down (not just the -pending- form) =="
for s in shadow retired disabled deprecated; do
    D="$(probe "$s")"
    [ "${D%%|*}" = "shadow" ]       && ok "status: $s → ungraded"                   || bad "$s graded: $D"
done

echo "== E. an UNRECOGNIZED status keeps grading and reports itself =="
# Fail-safe direction: a noisy alarm is recoverable, a silently dropped shield is not.
E="$(probe 'wobble')"
[ "${E%%|*}" = "alert" ]            && ok "unknown status still graded (fail-safe)" || bad "unknown status silently dropped the shield: $E"
case "$E" in *"|note|"*) ok "unknown status surfaces a status_note";; *) bad "misconfiguration passed silently: $E";; esac

echo "== F. the stood-down setpoint stays VISIBLE in the brief =="
# An ungraded setpoint vanishing without trace is the inverse failure to the one
# BRO-2168 fixed, so its absence from grading must be stated explicitly.
BRIEF="$(python3 - "$SENSOR" <<'PY'
import importlib.util, sys
spec = importlib.util.spec_from_file_location("levsensor", sys.argv[1])
mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
sp = {"id": "m6", "name": "meta_work_session_ratio", "level": "L3",
      "direction": "lower_is_better", "target": 0.50, "alert": 0.75, "actuator": "freeze governance",
      "status": "shadow-pending-m7"}
results, worst = mod.evaluate({"m6_meta_work_session_ratio": 0.96}, {"metrics": [sp]})
print(mod.render_brief({"sessions_analyzed": 5, "window_days": 7, "results": results,
                        "worst": worst, "closure": {"closed": True, "sensor_live": True,
                                                    "reference_authored": True}}))
PY
)"
grep -q "NOT graded" <<<"$BRIEF"                 && ok "brief names it as not graded"        || bad "brief hid it: $BRIEF"
grep -q "meta_work_session_ratio" <<<"$BRIEF"    && ok "brief still names the setpoint"      || bad "setpoint absent from brief"
grep -q "0.96" <<<"$BRIEF"                       && ok "brief still shows the measured value" || bad "value absent from brief"
! grep -q "Corrective actuator" <<<"$BRIEF"      && ok "brief emits NO corrective actuator"  || bad "brief still steered: $BRIEF"

echo "== G. a qualified lifecycle word is only honored in the 'pending' form =="
# P20 round 1 BLOCKER: an earlier prefix match accepted ANY suffix, so `shadow-live`
# -- which reads like "shadow is live" -- silently dropped the shield with no note.
# These must GRADE (fail-safe) and REPORT, never stand down.
for s in shadow-live shadow-typo retired-nope disabled-not-really; do
    G="$(probe "$s")"
    [ "${G%%|*}" = "alert" ]        && ok "$s → still graded (shield kept)"          || bad "$s SILENTLY dropped the shield: $G"
    case "$G" in *"|note|"*) ok "$s → reported";; *) bad "$s dropped silently: $G";; esac
done
for s in shadow-pending-m7 shadow_pending_m7 retired-pending-m9; do
    G="$(probe "$s")"
    [ "${G%%|*}" = "shadow" ]       && ok "$s → stands down"                          || bad "$s did not stand down: $G"
done

echo "== H. only an ABSENT status is implicitly live; an empty one is malformed =="
for s in "" "   "; do
    H="$(probe "$s")"
    [ "${H%%|*}" = "alert" ]        && ok "empty status '$s' → graded (fail-safe)"    || bad "empty status ungraded: $H"
    case "$H" in *"|note|"*) ok "empty status '$s' → reported";; *) bad "empty status silent: $H";; esac
done
ABS="$(probe '__ABSENT__')"
case "$ABS" in *"|no-note|"*) ok "ABSENT status → graded with NO note (the common case)";; *) bad "absent status noisy: $ABS";; esac

echo "== I. a stood-down setpoint stays disclosed even when its value is null =="
# A blind window is exactly when a stand-down must not quietly vanish.
I="$(python3 - "$SENSOR" <<'PY'
import importlib.util, sys
spec = importlib.util.spec_from_file_location("s", sys.argv[1]); m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
sp = {"id": "m6", "name": "meta_work_session_ratio", "level": "L3", "direction": "lower_is_better",
      "target": 0.5, "alert": 0.75, "actuator": "freeze", "status": "shadow-pending-m7"}
r, _ = m.evaluate({"m6_meta_work_session_ratio": None}, {"metrics": [sp]})
print(r[0]["status"])
PY
)"
[ "$I" = "shadow" ]                 && ok "null value + shadow → still 'shadow'"      || bad "stand-down vanished on a blind read: $I"

echo "== J. a malformed status is reported even when the reference is unauthored =="
J="$(python3 - "$SENSOR" <<'PY'
import importlib.util, sys
spec = importlib.util.spec_from_file_location("s", sys.argv[1]); m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
sp = {"id": "m6", "name": "meta_work_session_ratio", "level": "L3", "status": "wobble"}  # no target/alert
r, _ = m.evaluate({"m6_meta_work_session_ratio": 0.96}, {"metrics": [sp]})
print(r[0]["status"], "note" if r[0].get("status_note") else "no-note")
PY
)"
[ "$J" = "unset_target note" ]      && ok "unset_target path still carries the note"  || bad "note lost on unset_target: $J"

echo "== K. render_human does not claim compliance on a blind read =="
K="$(python3 - "$SENSOR" <<'PY'
import importlib.util, sys
spec = importlib.util.spec_from_file_location("s", sys.argv[1]); m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
print(m.render_human({"sessions_analyzed": 3, "window_days": 7, "measured_at": "2026-08-17T00:00:00",
                      "results": [], "worst": None, "metrics": {},
                      "closure": {"closed": False, "sensor_live": False}}))
PY
)"
! grep -q "within target" <<<"$K"   && ok "blind read makes no compliance claim"      || bad "blind read claimed compliance: $K"
grep -q "No setpoint graded" <<<"$K" && ok "blind read says nothing was measured"     || bad "blind read gave no reason: $K"

echo "== L. --cached re-grades against CURRENT setpoints (the SessionStart path) =="
# P20 round 1 BLOCKER: knowledge-wakeup-hook.sh runs `--brief --cached`. The cached
# record stores results/worst graded under the setpoints live at WRITE time, so
# standing a setpoint down left its retired actuator steering for up to 24h. The
# fixture below is a PRE-FIX cache: m6 stored as a graded L3 alert with its actuator.
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
mkdir -p "$TMP/.control"
cat > "$TMP/.control/leverage-setpoints.yaml" <<'YML'
window_days: 7
metrics:
  - id: m6
    name: meta_work_session_ratio
    level: L3
    direction: lower_is_better
    target: 0.50
    alert: 0.75
    actuator: "anti-windup: freeze NEW governance/primitive edits"
    status: shadow-pending-m7
YML
python3 - "$TMP" <<'PY'
import json, sys, datetime
ws = sys.argv[1]
rec = {"sessions_analyzed": 26, "window_days": 7,
       "measured_at": datetime.datetime.now().isoformat(),
       "metrics": {"m6_meta_work_session_ratio": 0.96},
       # stale grading, written before the stand-down was honored
       "results": [{"key": "m6_meta_work_session_ratio", "name": "meta_work_session_ratio",
                    "level": "L3", "value": 0.96, "target": 0.5, "alert": 0.75,
                    "direction": "lower_is_better", "status": "alert", "gap": 0.46,
                    "actuator": "anti-windup: freeze NEW governance/primitive edits"}],
       "worst": {"key": "m6_meta_work_session_ratio", "name": "meta_work_session_ratio",
                 "level": "L3", "value": 0.96, "target": 0.5, "gap": 0.46,
                 "direction": "lower_is_better", "status": "alert",
                 "actuator": "anti-windup: freeze NEW governance/primitive edits"},
       "closure": {"closed": True, "sensor_live": True, "reference_authored": True}}
json.dump(rec, open(f"{ws}/.control/leverage-state.json", "w"))
PY
CACHED="$(python3 "$SENSOR" --workspace "$TMP" --brief --cached --no-store 2>/dev/null)"
! grep -q "freeze NEW governance" <<<"$CACHED" && ok "cached brief no longer emits the retired actuator" || bad "STALE ACTUATOR STILL STEERING: $CACHED"
! grep -q "Worst gap" <<<"$CACHED"             && ok "cached brief no longer ranks it as worst"          || bad "still ranked worst: $CACHED"
grep -q "NOT graded" <<<"$CACHED"              && ok "cached brief discloses the stand-down"             || bad "stand-down undisclosed: $CACHED"

echo "== N. a '-pending' qualifier must NAME a successor (round-2 blocker) =="
# `shadow-pending` / `shadow-pending-` promise a successor and do not deliver one.
# Standing down on a half-written value is the silent shield-drop this rule prevents.
for s in shadow-pending shadow-pending- "shadow-pending- " retired-pending; do
    N="$(probe "$s")"
    [ "${N%%|*}" = "alert" ]        && ok "'$s' → graded (no successor named)"        || bad "'$s' stood down on a half-written value: $N"
    case "$N" in *"|note|"*) ok "'$s' → reported";; *) bad "'$s' dropped silently: $N";; esac
done
for s in shadow-pending-m7 retired-pending-x; do
    N="$(probe "$s")"
    [ "${N%%|*}" = "shadow" ]       && ok "'$s' → stands down (successor named)"      || bad "'$s' rejected: $N"
done

echo "== O. no compliance claim when NOTHING was graded (round-2 major) =="
# A cached m6 metric re-graded against setpoints that define only m7 leaves every row
# ungraded, so `worst` is None -- but the window was never certified. A STALE closure
# block carried over by the cached path must not certify it either.
O="$(python3 - "$SENSOR" <<'PY2'
import importlib.util, sys
spec = importlib.util.spec_from_file_location("s", sys.argv[1]); m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
sp = {"id": "m7", "name": "other", "level": "L3", "direction": "lower_is_better", "target": 1, "alert": 2}
results, worst = m.evaluate({"m6_meta_work_session_ratio": 0.96}, {"metrics": [sp]})
print(m.render_brief({"sessions_analyzed": 9, "window_days": 7, "results": results, "worst": worst,
                      "closure": {"closed": True, "sensor_live": True, "reference_authored": True}}))
PY2
)"
! grep -q "within target" <<<"$O"   && ok "no false compliance when nothing graded"    || bad "FALSE COMPLIANCE: $O"
grep -q "no metric matched" <<<"$O" && ok "states WHY nothing was graded"              || bad "no reason given: $O"

echo "== P. absent closure is not read as a dead sensor (round-2 regression) =="
P_="$(python3 - "$SENSOR" <<'PY2'
import importlib.util, sys
spec = importlib.util.spec_from_file_location("s", sys.argv[1]); m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
sp = {"id": "m6", "name": "meta_work_session_ratio", "level": "L3", "direction": "lower_is_better",
      "target": 0.5, "alert": 0.75, "actuator": "freeze"}
results, worst = m.evaluate({"m6_meta_work_session_ratio": 0.1}, {"metrics": [sp]})  # healthy
print(m.render_brief({"sessions_analyzed": 9, "window_days": 7, "results": results, "worst": worst}))
PY2
)"
grep -q "All graded setpoints within target" <<<"$P_" && ok "healthy + absent closure → compliance is stated" || bad "absent closure misread as dead sensor: $P_"


echo "== M. unreadable setpoints must not pass off cached grading as current =="
# Fallback hole found in round 2 self-check: when the policy file cannot be read the
# cached path keeps the stored grading, which can name an actuator policy has since
# retired -- steering on authority nobody can verify. It must be labelled.
echo ":::not yaml:::" > "$TMP/.control/leverage-setpoints.yaml"
STALE="$(python3 "$SENSOR" --workspace "$TMP" --brief --cached --no-store 2>/dev/null)"
grep -q "self-improvement loop" <<<"$STALE"        && ok "brief was actually produced"                 || bad "no output; absence checks would pass vacuously"
grep -q "unreadable, empty or malformed" <<<"$STALE"       && ok "unreadable setpoints are disclosed"          || bad "stale grading passed off as current: $STALE"
# Round-2 blocker: labelling is not enough — a caveat printed beside a live
# "-> Corrective actuator:" line still steers, so the steering itself must go.
! grep -q "Corrective actuator" <<<"$STALE"     && ok "NO actuator emitted without readable policy" || bad "STILL STEERING on unverifiable policy: $STALE"
! grep -q "Worst gap" <<<"$STALE"               && ok "nothing ranked as worst"                     || bad "still ranked: $STALE"
# Round-3 blocker (self-inflicted): dropping `worst` while KEEPING cached alert rows
# made no_worst_line() certify "All graded setpoints within target" over an alert.
! grep -q "within target" <<<"$STALE"           && ok "no compliance claim without readable policy" || bad "FALSE COMPLIANCE over a cached alert: $STALE"

echo "== Q. a '-pending' successor must NAME something, not be a placeholder =="
# $'...' so a REAL tab / NBSP reaches the probe; a literal backslash-t would carry
# an alphanumeric 't' and legitimately stand down.
for s in shadow-pending-. shadow-pending-- $'shadow-pending-\t' $'shadow-pending-\u00a0'; do
    Q="$(probe "$s")"
    [ "${Q%%|*}" = "alert" ]        && ok "'$s' → graded (placeholder successor)"     || bad "'$s' stood down on a placeholder: $Q"
done
for s in shadow-pending-m7 shadow-pending-0 shadow-pending--m7; do
    Q="$(probe "$s")"
    [ "${Q%%|*}" = "shadow" ]       && ok "'$s' → stands down (successor named)"      || bad "'$s' rejected: $Q"
done

echo "== R. a malformed result set cannot certify anything =="
R="$(python3 - "$SENSOR" <<'PY2'
import importlib.util, sys
spec = importlib.util.spec_from_file_location("s", sys.argv[1]); m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
for rec in ({"results": None}, {"results": "nope"}, {"results": ["junk", 3]},
            {"results": [{"status": "ok", "value": None}]}):
    line = m.no_worst_line(dict(rec))
    assert "within target" not in line, f"CERTIFIED {rec}: {line}"
print("ok")
PY2
)"
[ "$R" = "ok" ]                     && ok "null/str/junk/valueless rows never certify"  || bad "malformed results certified or crashed: $R"


echo "== S. round-4: an inconsistent record is never certified =="
S_="$(python3 - "$SENSOR" <<'PY2'
import importlib.util, sys
spec = importlib.util.spec_from_file_location("s", sys.argv[1]); m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
# alert row present but 'worst' unset: the two disagree; certifying picks the
# reassuring side of a contradiction.
print(m.no_worst_line({"results": [{"key": "m6", "name": "m6", "status": "alert", "value": 1}]}))
PY2
)"
! grep -q "within target" <<<"$S_"  && ok "alert row + no worst → NOT certified"       || bad "FALSE COMPLIANCE over an alert row: $S_"
grep -q "Inconsistent" <<<"$S_"     && ok "the contradiction is named"                 || bad "contradiction hidden: $S_"

echo "== T. round-4: an empty cached metric set cannot retain a stale actuator =="
T2="$(mktemp -d)"; mkdir -p "$T2/.control"
cat > "$T2/.control/leverage-setpoints.yaml" <<'YML'
window_days: 7
metrics:
  - {id: m6, name: meta_work_session_ratio, level: L3, direction: lower_is_better, target: 0.5, alert: 0.75, actuator: "REAL POLICY"}
YML
python3 - "$T2" <<'PY2'
import json,sys,datetime
json.dump({"sessions_analyzed":4,"window_days":7,"measured_at":datetime.datetime.now().isoformat(),
 "metrics":{},   # EMPTY dict -- falsy, so the old guard skipped the regrade entirely
 "results":[{"key":"m6_x","name":"m6","level":"L3","value":9,"status":"alert","gap":9,"target":0.5,
             "alert":0.75,"direction":"lower_is_better","actuator":"STALE ACTUATOR FROM CACHE"}],
 "worst":{"key":"m6_x","name":"m6","level":"L3","value":9,"target":0.5,"gap":9,
          "direction":"lower_is_better","status":"alert","actuator":"STALE ACTUATOR FROM CACHE"},
 "closure":{"closed":True,"sensor_live":True,"reference_authored":True}},
 open(f"{sys.argv[1]}/.control/leverage-state.json","w"))
PY2
EMPTY="$(python3 "$SENSOR" --workspace "$T2" --brief --cached --no-store 2>/dev/null)"
# POSITIVE assertions FIRST. An absence-only check passes vacuously on empty output --
# caught when a mutation that killed the process produced no stdout and this section
# still went green. Every "must not appear" needs a "must appear" beside it.
grep -q "self-improvement loop" <<<"$EMPTY"       && ok "brief was actually produced"              || bad "no output at all (absence checks below would pass vacuously): $EMPTY"
grep -q "No setpoint graded" <<<"$EMPTY"          && ok "empty metric set is reported as ungraded" || bad "wrong line: $EMPTY"
! grep -q "STALE ACTUATOR FROM CACHE" <<<"$EMPTY" && ok "empty metric set drops the stale actuator" || bad "STALE ACTUATOR LEAKED: $EMPTY"

echo "== U. round-4: structurally malformed (but valid) YAML policy must not crash =="
printf -- "- id: m6\n" > "$T2/.control/leverage-setpoints.yaml"   # valid YAML, a LIST not a mapping
BADY="$(python3 "$SENSOR" --workspace "$T2" --brief --cached --no-store 2>/dev/null)"; rc=$?
[ $rc -eq 0 ] && ok "non-mapping policy exits 0 (no crash)"                             || bad "crashed rc=$rc"
grep -q "unreadable, empty or malformed" <<<"$BADY" && ok "non-mapping policy reports the unified state" || bad "wrong output: $BADY"
rm -rf "$T2"


echo "== V. round-5: a BREACH CLAIM counts even without a value =="
# Requiring value-is-not-None on the breach filter is how
# [{status:ok,value:0},{status:alert,value:None}] certified: the alert row was
# filtered out for lacking a number and the ok row carried the claim.
V="$(python3 - "$SENSOR" <<'PY2'
import importlib.util, sys
spec = importlib.util.spec_from_file_location("s", sys.argv[1]); m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
print(m.no_worst_line({"results": [{"status": "ok", "value": 0}, {"status": "alert", "value": None}]}))
PY2
)"
! grep -q "within target" <<<"$V"   && ok "valueless alert row blocks certification"   || bad "FALSE COMPLIANCE: $V"
grep -q "Inconsistent" <<<"$V"      && ok "the contradiction is named"                 || bad "not named: $V"

echo "== W. round-5: only a real finite number may be graded =="
# bool is an int in Python, so False grades ok against every lower-is-better target;
# NaN does the same because every comparison with NaN is False. Both certify a
# window nobody measured.
W="$(python3 - "$SENSOR" <<'PY2'
import importlib.util, sys
spec = importlib.util.spec_from_file_location("s", sys.argv[1]); m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
sp = {"id":"m6","name":"m6","level":"L3","direction":"lower_is_better","target":0.5,"alert":0.75,"actuator":"A"}
bad = []
for v in (False, True, float("nan"), float("inf"), float("-inf"), "0.4", None, [], {}):
    r,_ = m.evaluate({"m6_x": v}, {"metrics":[sp]})
    if r[0]["status"] != "no_setpoint": bad.append((v, r[0]["status"]))
# polarity: a real number MUST still grade, or this passes by refusing everything
r,_ = m.evaluate({"m6_x": 0.9}, {"metrics":[sp]})
if r[0]["status"] != "alert": bad.append((0.9, r[0]["status"]))
r,_ = m.evaluate({"m6_x": 0.4}, {"metrics":[sp]})
if r[0]["status"] != "ok": bad.append((0.4, r[0]["status"]))
print("ok" if not bad else f"MISGRADED {bad}")
PY2
)"
[ "$W" = "ok" ]                     && ok "bool/NaN/inf/str/None ungraded; real numbers still graded" || bad "$W"

echo "== X. round-5: renderers never raise on a malformed record =="
X="$(python3 - "$SENSOR" <<'PY2'
import importlib.util, sys
spec = importlib.util.spec_from_file_location("s", sys.argv[1]); m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
bad = [{"closure":"bad"}, {"results":None}, {"results":"str"}, {"results":[None,3,"x"]},
       {"worst":{"status":"alert"}}, {"worst":"nope"}, {}, {"closure":{"levels":"junk"}},
       {"metrics":"junk"}, {"closure":{"closed":False,"levels":{"L0":"nd"}}}]
fails = []
for rec in bad:
    for fn in ("render_brief","render_human","no_worst_line","shadow_notes"):
        try: getattr(m, fn)(dict(rec))
        except Exception as e: fails.append((fn, rec, type(e).__name__))
# polarity: a WELL-FORMED record must still render its actuator, or "never raises"
# could be satisfied by a function that returns nothing useful.
sp = {"id":"m6","name":"m6","level":"L3","direction":"lower_is_better","target":0.5,"alert":0.75,"actuator":"REAL ACTUATOR"}
res, worst = m.evaluate({"m6_x": 0.9}, {"metrics":[sp]})
brief = m.render_brief({"sessions_analyzed":3,"window_days":7,"results":res,"worst":worst,
                        "closure":{"closed":True,"sensor_live":True,"reference_authored":True}})
if "REAL ACTUATOR" not in brief: fails.append(("render_brief", "well-formed", "actuator missing"))
print("ok" if not fails else f"FAILURES {fails[:3]}")
PY2
)"
[ "$X" = "ok" ]                     && ok "40 hostile inputs render without raising; well-formed still steers" || bad "$X"


echo "== Y. round-6: a non-finite THRESHOLD must fail closed, not certify =="
# PyYAML accepts `target: .nan`. Every comparison with NaN is False, so the metric
# grades ok and the window certifies -- the same falsehood as a NaN metric, entering
# from the policy side. Validating the measurement was only half of it.
Y="$(python3 - "$SENSOR" <<'PY2'
import importlib.util, sys
spec = importlib.util.spec_from_file_location("s", sys.argv[1]); m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
bad = []
for tgt, alr in ((float("nan"), float("nan")), (float("nan"), 0.75), (0.5, float("nan")),
                 (float("inf"), 0.75), ("0.5", 0.75), (True, 0.75)):
    r, w = m.evaluate({"m6_x": 0.9}, {"metrics": [{"id":"m6","name":"m6","level":"L3",
        "direction":"lower_is_better","target":tgt,"alert":alr,"actuator":"A"}]})
    line = m.render_brief({"sessions_analyzed":1,"window_days":7,"results":r,"worst":w,
        "closure":{"closed":True,"sensor_live":True,"reference_authored":True}})
    if r[0]["status"] != "unset_target" or w is not None or "within target" in line:
        bad.append((tgt, alr, r[0]["status"], w is not None))
# polarity: real thresholds must STILL grade and still steer
r, w = m.evaluate({"m6_x": 0.9}, {"metrics": [{"id":"m6","name":"m6","level":"L3",
    "direction":"lower_is_better","target":0.5,"alert":0.75,"actuator":"REAL"}]})
if r[0]["status"] != "alert" or not w: bad.append(("real", "real", r[0]["status"], bool(w)))
print("ok" if not bad else f"BAD {bad}")
PY2
)"
[ "$Y" = "ok" ]                     && ok "non-finite thresholds fail closed; real ones still grade" || bad "$Y"

echo "== Z. round-6: is_gradeable converts rather than type-matches =="
Z="$(python3 - "$SENSOR" <<'PY2'
import importlib.util, sys, decimal
spec = importlib.util.spec_from_file_location("s", sys.argv[1]); m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
want_true  = [0.4, 1, 0, -0.0, 10**30, decimal.Decimal("0.4")]
want_false = [True, False, float("nan"), float("inf"), float("-inf"), "0.4", b"0.4",
              None, [], {}, 10**10000]   # 10**10000 must be REFUSED, not raise
bad  = [(v, "expected gradeable")     for v in want_true  if not m.is_gradeable(v)]
bad += [(repr(v)[:12], "expected refused") for v in want_false if m.is_gradeable(v)]
print("ok" if not bad else f"BAD {bad}")
PY2
)"
[ "$Z" = "ok" ]                     && ok "Decimal/int/float ok; bool/str/NaN/huge refused without raising" || bad "$Z"


echo "== AA. round-7: an unrecognized DIRECTION must not invert the comparison =="
# `if direction == "lower_is_better": ... else: <higher branch>` made every
# unrecognized value select the OPPOSITE polarity, so a breach read as healthy.
# Same defect as the status: field this change exists to fix.
AA="$(python3 - "$SENSOR" <<'PY2'
import importlib.util, sys
spec = importlib.util.spec_from_file_location("s", sys.argv[1]); m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
def probe(d):
    sp={"id":"m6","name":"m6","level":"L3","target":0.5,"alert":0.75,"actuator":"A"}
    if d is not False: sp["direction"]=d
    r,w=m.evaluate({"m6_x":0.9},{"metrics":[sp]}); return r[0]["status"], w is not None, r[0].get("status_note")
bad=[]
# separator/case variants of lower_is_better must all ALERT on 0.9
for d in ("lower_is_better","lower-is-better","LOWER IS BETTER","Lower_Is_Better",None,False):
    st,w,_ = probe(d)
    if st!="alert" or not w: bad.append((d,st,w))
# genuinely unknown must fail CLOSED, never silently flip to the higher branch
for d in ("nonsense","lowerish","","higher-is-worse",0,[]):
    st,w,n = probe(d)
    if st!="unset_target" or w or not n: bad.append((d,st,w,n))
# polarity: a real higher_is_better setpoint must still grade the OTHER way
sp={"id":"m5","name":"m5","level":"L2","direction":"higher_is_better","target":0.4,"alert":0.1,"actuator":"A"}
r,w=m.evaluate({"m5_x":0.05},{"metrics":[sp]})
if r[0]["status"]!="alert" or not w: bad.append(("higher_is_better polarity",r[0]["status"],bool(w)))
print("ok" if not bad else f"BAD {bad}")
PY2
)"
[ "$AA" = "ok" ]                    && ok "direction variants normalize; unknown fails closed with a reason" || bad "$AA"

echo "== AB. round-7: an accepted numeric type must be COMPARABLE, not just accepted =="
# Declaring Decimal gradeable without normalizing operands made evaluate() raise in
# round(val - target, 4) -- a claim the code did not honour.
AB="$(python3 - "$SENSOR" <<'PY2'
import importlib.util, sys, decimal, fractions
spec = importlib.util.spec_from_file_location("s", sys.argv[1]); m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
sp={"id":"m6","name":"m6","level":"L3","direction":"lower_is_better","target":0.5,"alert":0.75,"actuator":"A"}
bad=[]
for v,want in ((decimal.Decimal("0.9"),"alert"), (decimal.Decimal("0.4"),"ok"),
               (fractions.Fraction(9,10),"alert"), (1,"alert"), (0.4,"ok")):
    if not m.is_gradeable(v): bad.append((v,"declared ungradeable")); continue
    try:
        r,_=m.evaluate({"m6_x":v},{"metrics":[sp]})
        if r[0]["status"]!=want: bad.append((v,r[0]["status"],"wanted",want))
    except Exception as e: bad.append((v,f"RAISED {type(e).__name__}"))
# thresholds may be Decimal too
try:
    r,_=m.evaluate({"m6_x":0.9},{"metrics":[dict(sp,target=decimal.Decimal("0.5"),alert=decimal.Decimal("0.75"))]})
    if r[0]["status"]!="alert": bad.append(("decimal thresholds",r[0]["status"]))
except Exception as e: bad.append(("decimal thresholds",f"RAISED {type(e).__name__}"))
print("ok" if not bad else f"BAD {bad}")
PY2
)"
[ "$AB" = "ok" ]                    && ok "every gradeable type actually compares, values and thresholds" || bad "$AB"

echo "== AC. round-7: a malformed threshold names ITSELF, not 'nothing matched' =="
AC="$(python3 - "$SENSOR" <<'PY2'
import importlib.util, sys
spec = importlib.util.spec_from_file_location("s", sys.argv[1]); m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
sp={"id":"m6","name":"m6","level":"L3","direction":"lower_is_better","target":float("nan"),"alert":0.75,"actuator":"A"}
r,_=m.evaluate({"m6_x":0.9},{"metrics":[sp]})
n=r[0].get("status_note") or ""
print("ok" if "not a finite number" in n else f"BAD note={n!r}")
PY2
)"
[ "$AC" = "ok" ]                    && ok "malformed threshold reports why it was dropped"  || bad "$AC"


echo "== AD. sibling-field audit: no row may vanish from the human render =="
# Found by asking the question that had already produced two blockers -- which OTHER
# field is an enum where an unrecognized value silently picks an outcome? `level` was
# one: render_human iterated a fixed L0..L3 list, so a row at L4, a lowercase l3, or
# the "L?" that DEFAULT_LEVELS assigns an unknown metric id rendered NOWHERE. Alerts
# included. Disappearance instead of inversion, same shape.
AD="$(python3 - "$SENSOR" <<'PY2'
import importlib.util, sys
spec = importlib.util.spec_from_file_location("s", sys.argv[1]); m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
rows = [{"key":"a","name":"KNOWN_L3","level":"L3","value":0.9,"status":"alert","target":0.5,"alert":0.75,"gap":0.4,"direction":"lower_is_better","actuator":"A"},
        {"key":"b","name":"GHOST_L4","level":"L4","value":9,"status":"alert","target":1,"alert":2,"gap":8,"direction":"lower_is_better","actuator":"B"},
        {"key":"c","name":"GHOST_lower","level":"l3","value":9,"status":"alert","target":1,"alert":2,"gap":8,"direction":"lower_is_better","actuator":"C"},
        {"key":"d","name":"GHOST_unknown","level":"L?","value":9,"status":"alert","target":1,"alert":2,"gap":8,"direction":"lower_is_better","actuator":"D"},
        {"key":"e","name":"GHOST_none","value":9,"status":"alert","target":1,"alert":2,"gap":8,"direction":"lower_is_better","actuator":"E"}]
# worst=None so no "Focus:" line, which would legitimately name a row a second time
# and make the duplicate check fire on its own fixture.
out = m.render_human({"sessions_analyzed":3,"window_days":7,"measured_at":"x","results":rows,
                      "worst":None,"metrics":{},"closure":{"closed":True,"sensor_live":True,"reference_authored":True}})
# Count only the TABLE lines. The summary legitimately names rows again -- the
# "Focus:" line names 'worst', and the round-4 "Inconsistent record" line names every
# alert row when nothing ranked. Counting the whole output made this fixture flag its
# own correct behaviour as a duplicate.
table = [ln for ln in out.splitlines() if ln.startswith(("  ALRT", "  WARN", "  ok  ",
                                                         "  ----", "  r0? ", "  shdw"))]
missing = [r["name"] for r in rows if not any(r["name"] in ln for ln in table)]
dupes = [r["name"] for r in rows
         if sum(1 for ln in table if r["name"] in ln) > 1]
print("ok" if not missing and not dupes else f"missing={missing} dupes={dupes}")
PY2
)"
[ "$AD" = "ok" ]                    && ok "every row renders exactly once, whatever its level" || bad "$AD"


echo "== AE. round-8: structurally invalid policy must not crash the sensor =="
# All of these are VALID YAML from a hand-edited file, and each reached
# {m["id"]: m for m in setpoints["metrics"]} and raised. The SessionStart hook
# swallows failure with || true, so a crash means no brief AND no error. Round 4
# refused a non-mapping policy on the CACHED path only -- a one-site fix for a
# two-site defect; the ordinary CLI still crashed.
AE_T="$(mktemp -d)"; mkdir -p "$AE_T/.control"
run_policy_docs "$AE_T" "malformed policy" <<'DOCS'
- id: m6
%%
metrics: [oops]
%%
metrics:
  - name: no_id
    target: 0.5
%%
just a string
%%
metrics: notalist
%%
metrics:
  - id: m6
    target: 0.5
    alert: 0.75
  - id: m6
    target: 9
DOCS
# A DROPPED setpoint is a disarmed shield, so the drop must be reported, not silent.
# Without this, the id-drop guard is indistinguishable from evaluate()'s own defensive
# filter -- reverting it left the suite green, which is how the gap was found.
printf 'metrics:\n  - name: no_id\n    target: 0.5\n  - id: m6\n    target: 0.5\n    alert: 0.75\n' > "$AE_T/.control/leverage-setpoints.yaml"
AE_ERR="$(python3 "$SENSOR" --workspace "$AE_T" --brief --no-store 2>&1 >/dev/null)"
grep -q "no usable" <<<"$AE_ERR"      && ok "a setpoint dropped for a missing id says so on stderr" || bad "silently disarmed: $AE_ERR"
printf 'metrics: [oops]\n' > "$AE_T/.control/leverage-setpoints.yaml"
AE_ERR2="$(python3 "$SENSOR" --workspace "$AE_T" --brief --no-store 2>&1 >/dev/null)"
grep -q "expected a mapping" <<<"$AE_ERR2" && ok "a non-mapping metric entry says so on stderr" || bad "silently dropped: $AE_ERR2"
# Round-9 MINOR: AE did not bind the duplicate-id guard -- deleting it left AE green.
printf 'metrics:\n  - id: m6\n    target: 0.5\n    alert: 0.75\n  - id: m6\n    target: 9\n' > "$AE_T/.control/leverage-setpoints.yaml"
AE_DUP="$(python3 "$SENSOR" --workspace "$AE_T" --brief --no-store 2>&1 >/dev/null)"
grep -q "duplicates id" <<<"$AE_DUP" && ok "a duplicate setpoint id is reported"   || bad "duplicate id silently accepted: $AE_DUP"

echo "== AE2. evaluate() is tolerant on its OWN, not only behind the loader =="
# evaluate() is called directly (these tests do it, and any future consumer may).
# Its defensive filter was unbound: removing it left the suite green because
# coerce_setpoints() also catches the same input on the CLI path. Redundancy is fine;
# UNTESTED redundancy is indistinguishable from dead code.
AE2="$(python3 - "$SENSOR" <<'PY2'
import importlib.util, sys
spec = importlib.util.spec_from_file_location("s", sys.argv[1]); m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
raw = [None, "str", 42, {"metrics": "notalist"}, {"metrics": ["oops", 3, None]},
       {"metrics": [{"name": "no_id"}]}, {"metrics": [{"id": 7}]}, {}]
bad = []
for sp in raw:
    try:
        res, worst = m.evaluate({"m6_x": 0.9}, sp)
        if worst is not None: bad.append((sp, "ranked something from junk policy"))
    except Exception as e:
        bad.append((str(sp)[:28], f"RAISED {type(e).__name__}"))
# metrics arg may be junk too
for met in (None, "str", 42, []):
    try: m.evaluate(met, {"metrics": []})
    except Exception as e: bad.append((str(met)[:12], f"metrics arg RAISED {type(e).__name__}"))
# POLARITY: a well-formed call must still rank, or tolerance could mean "never grades"
res, worst = m.evaluate({"m6_x": 0.9}, {"metrics": [{"id": "m6", "name": "m6", "level": "L3",
    "direction": "lower_is_better", "target": 0.5, "alert": 0.75, "actuator": "A"}]})
if not worst: bad.append(("well-formed", "did not rank"))
print("ok" if not bad else f"BAD {bad[:3]}")
PY2
)"
[ "$AE2" = "ok" ]                   && ok "evaluate() tolerates junk policy and junk metrics unaided" || bad "$AE2"

echo "== AF. round-9: mistyped top-level and per-entry fields must not crash =="
# These reach arithmetic (window_days), re.compile (knowledge_paths) and a dict key
# (level) far from the file that caused them.
AF_T="$(mktemp -d)"; mkdir -p "$AF_T/.control"
run_policy_docs "$AF_T" "mistyped fields" <<'DOCS'
window_days: []
metrics: []
%%
window_days: abc
metrics: []
%%
window_days: -5
metrics: []
%%
window_days: .nan
metrics: []
%%
knowledge_paths: []
metrics: []
%%
metrics:
  - id: m6
    level: [L0]
    target: 0.5
    alert: 0.75
%%
metrics:
  - id: m6
    name: [oops]
    target: 0.5
    alert: 0.75
DOCS
# POLARITY: a valid policy with all these fields set correctly must still steer.
cat > "$AF_T/.control/leverage-setpoints.yaml" <<'YML'
window_days: 7
knowledge_paths: "research/entities"
metrics:
  - {id: m4, name: permission_bypass_per_session, level: L0, direction: lower_is_better, target: 0.5, alert: 0.75, actuator: "AF ACTUATOR"}
YML
python3 - "$AF_T" <<'PY2'
import json,sys,datetime
json.dump({"sessions_analyzed":5,"window_days":7,"measured_at":datetime.datetime.now().isoformat(),
 "metrics":{"m4_permission_bypass_per_session":4.6},"results":[],"worst":None,
 "closure":{"closed":True,"sensor_live":True,"reference_authored":True}},
 open(f"{sys.argv[1]}/.control/leverage-state.json","w"))
PY2
AF_OK="$(python3 "$SENSOR" --workspace "$AF_T" --brief --cached --no-store 2>/dev/null)"
grep -q "AF ACTUATOR" <<<"$AF_OK" && ok "well-typed policy still grades and steers (polarity)" || bad "stopped steering: $AF_OK"
# Round-10: AF asserted only non-crash for these, so ACCEPTING them would still pass —
# a test that cannot fail. `-5` never crashed; it silently moved the cutoff into the
# future so no transcripts matched. Assert the REFUSAL, not merely the survival.
for wd in "-5" ".nan" "[]" "abc" "0"; do
    printf 'window_days: %s\nmetrics: []\n' "$wd" > "$AF_T/.control/leverage-setpoints.yaml"
    ERR="$(python3 "$SENSOR" --workspace "$AF_T" --brief --no-store 2>&1 >/dev/null)"
    grep -q "window_days" <<<"$ERR" && ok "window_days: $wd is refused and reported" || bad "window_days: $wd silently accepted: $ERR"
done
# POLARITY: a VALID window must NOT be reported
printf 'window_days: 7\nmetrics: []\n' > "$AF_T/.control/leverage-setpoints.yaml"
ERR_OK="$(python3 "$SENSOR" --workspace "$AF_T" --brief --no-store 2>&1 >/dev/null)"
! grep -q "window_days" <<<"$ERR_OK" && ok "a valid window_days is NOT reported (polarity)" || bad "valid window flagged: $ERR_OK"

echo "== AG. round-10: an invalid REGEX, and the never-silent floor =="
# A syntactically fine string can still be a broken regex; only compiling reveals it.
printf 'knowledge_paths: "["\nmetrics: []\n' > "$AF_T/.control/leverage-setpoints.yaml"
RX="$(python3 "$SENSOR" --workspace "$AF_T" --brief --no-store 2>/dev/null)"; rc=$?
[ $rc -eq 0 ] && [ -n "$RX" ]        && ok "invalid knowledge_paths regex does not crash"  || bad "rc=$rc out=[$RX]"
RXE="$(python3 "$SENSOR" --workspace "$AF_T" --brief --no-store 2>&1 >/dev/null)"
grep -q "not a valid regex" <<<"$RXE" && ok "invalid regex is reported, not swallowed"      || bad "silent fallback: $RXE"
# The floor: ANY unexpected failure must still emit a stated failure and exit 0, or the
# hook's `|| true` turns it into no brief AND no error.
FLOOR="$(python3 - "$SENSOR" <<'PY2'
import importlib.util, sys
spec = importlib.util.spec_from_file_location("s", sys.argv[1]); m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
m.main = lambda: (_ for _ in ()).throw(RuntimeError("synthetic boom"))
try: m._main_guarded()
except SystemExit as e: print(f"EXIT={e.code}", file=sys.stderr)
PY2
)"
grep -q "sensor failed" <<<"$FLOOR"   && ok "an unexpected failure still emits a stated failure" || bad "silent crash: [$FLOOR]"
grep -q "no actuator emitted" <<<"$FLOOR" && ok "and explicitly emits no actuator"                || bad "no disclaimer: [$FLOOR]"
rm -rf "$AF_T"
# POLARITY: a VALID policy must still grade and still steer through the same path.
cat > "$AE_T/.control/leverage-setpoints.yaml" <<'YML'
window_days: 7
metrics:
  - {id: m4, name: permission_bypass_per_session, level: L0, direction: lower_is_better, target: 0.5, alert: 0.75, actuator: "REAL ACTUATOR"}
YML
python3 - "$AE_T" <<'PY2'
import json,sys,datetime
json.dump({"sessions_analyzed":5,"window_days":7,"measured_at":datetime.datetime.now().isoformat(),
 "metrics":{"m4_permission_bypass_per_session":4.6},"results":[],"worst":None,
 "closure":{"closed":True,"sensor_live":True,"reference_authored":True}},
 open(f"{sys.argv[1]}/.control/leverage-state.json","w"))
PY2
VALID="$(python3 "$SENSOR" --workspace "$AE_T" --brief --cached --no-store 2>/dev/null)"
grep -q "REAL ACTUATOR" <<<"$VALID" && ok "a VALID policy still grades and steers (polarity)" || bad "valid policy stopped steering: $VALID"
rm -rf "$AE_T"


echo
echo "setpoint-shadow-status: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
