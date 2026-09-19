#!/usr/bin/env bash
# sensor-absence-is-not-death.test.sh
#
# leverage-sensor.py reported "sensor dead" for TWO different states:
#
#   blind    N>0 session files were read and yielded zero structural events.
#            The wholesale-misread failure doctor §23 exists to catch.
#   no_data  zero session files matched the window. Nothing was read.
#
# Only the first is a defect. Claude Code keys transcripts on the project
# directory, so a git worktree carries its own near-empty history: measured
# 2026-09-18, the same sensor over the same 7-day window read 51 sessions from
# the main checkout and 0 from a fresh worktree, and the SessionStart brief
# announced "loop NOT closed (sensor dead) — run `bstack doctor` §23" while
# §23, reading the main checkout's state file, said the sensor was live. Two
# readings of one sensor, disagreeing, with the false one pointing at a parser
# that was fine.
#
# Absence is the resting state, so it cannot also be the error signal.
#
# Invariants asserted here:
#   T1  no_data   -> sensor_blindness() == "no_data"        (nothing read)
#   T2  blind     -> sensor_blindness() == "blind"          (read, no structure)
#   T3  live      -> sensor_blindness() is None
#   T4  sensor_is_live() stays False for BOTH not-live causes. This is the
#       regression guard on the fix itself: making absence "live" would un-blind
#       the metrics, and a metric computed from an empty numerator is 0.0, which
#       beats every target. The reporting split must not become a grading split.
#   T5  the brief says "no sessions read this window", not "sensor dead"
#   T6  a closure block with NO "blindness" key (a state file written before this
#       existed; --cached reads exactly those) still renders a reason, never
#       "(None)" and never a blank.
#
# Mutation proof — each must turn this file red:
#   * `return "no_data" if ... else "blind"` -> `return "blind"`        kills T1, T5
#   * `... <= 0` -> `... < 0`                                           kills T1, T5
#   * sensor_is_live gaining `or sessions == 0`                         kills T4
#   * the `.get(..., "sensor dead")` default dropped to `.get(...)`     kills T6
set -uo pipefail

BSTACK_REPO="$(cd "$(dirname "$0")/.." && pwd)"
SENSOR="$BSTACK_REPO/scripts/leverage-sensor.py"

PASS=0; FAIL=0
ok()  { echo "  ok   $1"; PASS=$((PASS+1)); }
bad() { echo "  FAIL $1"; FAIL=$((FAIL+1)); }

[ -f "$SENSOR" ] || { echo "FATAL: $SENSOR not found"; exit 1; }

# Drive the real functions out of the real module — an inlined reimplementation
# would pass while the shipped code stayed broken.
RESULT=$(python3 - "$SENSOR" <<'PY'
import importlib.util, sys, json

spec = importlib.util.spec_from_file_location("lev", sys.argv[1])
lev = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lev)

NO_DATA = {"sessions_analyzed": 0, "tool_results": 0, "edits": 0}
BLIND   = {"sessions_analyzed": 12, "tool_results": 0, "edits": 0}
LIVE    = {"sessions_analyzed": 12, "tool_results": 340, "edits": 9}

out = {
    "t1": lev.sensor_blindness(NO_DATA),
    "t2": lev.sensor_blindness(BLIND),
    "t3": lev.sensor_blindness(LIVE),
    "t4_no_data_live": lev.sensor_is_live(NO_DATA),
    "t4_blind_live":   lev.sensor_is_live(BLIND),
    "t4_live_live":    lev.sensor_is_live(LIVE),
}

# T5/T6 exercise the brief renderer on a synthetic record, so the assertion is on
# the shipped string rather than on a copy of it.
def brief_for(closure):
    rec = {"sessions_analyzed": closure.get("sessions", 0), "window_days": 7,
           "closure": closure, "results": [], "raw": {}}
    try:
        return lev.render_brief(rec)
    except AttributeError:
        return None

out["t5"] = brief_for({"closed": False, "sensor_live": False,
                       "blindness": "no_data", "levels": {}, "sessions": 0,
                       "reference_authored": True})
# no "blindness" key at all — the pre-existing-state-file case
out["t6"] = brief_for({"closed": False, "sensor_live": False,
                       "levels": {}, "sessions": 0, "reference_authored": True})
print(json.dumps(out))
PY
)

if [ -z "$RESULT" ]; then
  echo "  FAIL  sensor module did not load / sensor_blindness missing"
  exit 1
fi

jget() { printf '%s' "$RESULT" | python3 -c "import sys,json;v=json.load(sys.stdin)[sys.argv[1]];print('' if v is None else v)" "$1"; }

# --- T1/T2/T3: the three-way split -------------------------------------------
[ "$(jget t1)" = "no_data" ] \
  && ok "T1 zero sessions read → 'no_data' (absence, not a defect)" \
  || bad "T1 zero sessions → expected 'no_data', got '$(jget t1)'"

[ "$(jget t2)" = "blind" ] \
  && ok "T2 sessions read, zero structural events → 'blind' (the real failure)" \
  || bad "T2 blind read → expected 'blind', got '$(jget t2)'"

[ -z "$(jget t3)" ] \
  && ok "T3 live sensor → blindness is None" \
  || bad "T3 live sensor → expected None, got '$(jget t3)'"

# --- T4: the reporting split must NOT become a grading split -----------------
if [ "$(jget t4_no_data_live)" = "False" ] && [ "$(jget t4_blind_live)" = "False" ]; then
  ok "T4 sensor_is_live() stays False for BOTH causes (metrics remain blinded)"
else
  bad "T4 sensor_is_live() leaked True — no_data=$(jget t4_no_data_live) blind=$(jget t4_blind_live); ungraded-as-perfect regression"
fi
[ "$(jget t4_live_live)" = "True" ] \
  && ok "T4b sensor_is_live() still True on a genuinely live read (positive control)" \
  || bad "T4b live read reported not-live — the gate now rejects everything"

# --- T5/T6: what the operator actually reads ---------------------------------
T5=$(jget t5)
if [ -z "$T5" ]; then
  bad "T5 brief_lines() not found — render_brief() missing; assertion is vacuous, fix the test"
elif printf '%s' "$T5" | grep -q "no sessions read this window"; then
  ok "T5 brief names absence, not death"
else
  bad "T5 brief did not name absence: $(printf '%s' "$T5" | tr '\n' '|')"
fi

T6=$(jget t6)
if [ -z "$T6" ]; then
  bad "T6 brief_lines() not found — render_brief() missing; assertion is vacuous, fix the test"
elif printf '%s' "$T6" | grep -q "(None)"; then
  bad "T6 a closure with no 'blindness' key rendered '(None)' — --cached reads exactly those"
elif printf '%s' "$T6" | grep -q "loop NOT closed (sensor dead)"; then
  ok "T6 pre-existing state file degrades to the old wording, not to a blank"
else
  bad "T6 unexpected fallback rendering: $(printf '%s' "$T6" | tr '\n' '|')"
fi

echo
echo "  ${PASS} passed, ${FAIL} failed"
[ "$FAIL" -eq 0 ]
