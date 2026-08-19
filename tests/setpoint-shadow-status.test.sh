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


echo
echo "setpoint-shadow-status: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
