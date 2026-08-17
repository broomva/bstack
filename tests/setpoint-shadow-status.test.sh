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

echo
echo "setpoint-shadow-status: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
