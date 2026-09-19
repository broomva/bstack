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
#   T7  no_worst_line() -- the SECOND renderer path -- makes the same distinction.
#   T8  doctor surfaces the derived transcript glob, and does NOT certify the
#       benign cause. `sessions_analyzed == 0` means "this glob matched nothing",
#       which is benign only if the glob is right; bstack 0.30.0 shipped a path
#       mangle that made `sde_vault` glob 0 files forever, and that bug was caught
#       precisely because zero files was loud. Reporting must stay falsifiable.
#   T9  under BSTACK_LOOP_STRICT the zero-session case is a hard gap and survives
#       --quiet. Info lines are QUIET-gated, so without this a CI lane on a
#       mis-derived glob turns FAIL into PASS.
#
# T7-T9 exist because P20 Stratum B found the first version of this fix had
# demoted the only automated detector of the path-derivation bug class.
#
# Mutation proof — each must turn this file red:
#   * `return "no_data" if ... else "blind"` -> `return "blind"`   kills T1,T5a,T5
#   * `... <= 0` -> `... < 0`                                      kills T1,T5a,T5
#   * sensor_is_live gaining `or sessions == 0`                         kills T4
#   * the `.get(..., "sensor dead")` default dropped to `.get(...)`     kills T6
#   * delete no_worst_line()'s no_data branch                           kills T7
#   * no_worst_line no_data wording back to "nothing to read"           kills T7b
#   * drop "transcript_glob" from the record                           kills T10
#   * doctor's no_data copy back to "not a defect"                      kills T8b
#   * doctor's LOOP_STRICT gap branch collapsed to the info line        kills T9
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

# The brief renderer is driven for real, so the assertion lands on the shipped
# string rather than on a copy of it.
def brief_for(closure):
    rec = {"sessions_analyzed": closure.get("sessions", 0), "window_days": 7,
           "closure": closure, "results": [], "raw": {}}
    try:
        return lev.render_brief(rec)
    except AttributeError:
        return None

# T5 derives the closure from closure_verdict() on a real no-data record rather
# than hand-writing blindness="no_data". Hand-writing it decouples this assertion
# from sensor_blindness(), and a mutant collapsing the split then leaves T5 green
# — measured: it did, until this was changed.
t5_closure = lev.closure_verdict(
    {"raw": NO_DATA, "sessions_analyzed": 0, "results": []},
    {"authored_by": "a-human"},
)
out["t5_blindness"] = t5_closure.get("blindness")
out["t5"] = brief_for(dict(t5_closure, sessions=0))
# no "blindness" key at all — the pre-existing-state-file case
out["t6"] = brief_for({"closed": False, "sensor_live": False,
                       "levels": {}, "sessions": 0, "reference_authored": True})

# T7 — no_worst_line() has its OWN no_data branch, on a second renderer path
# (render_human as well as the brief). P20 Stratum B found that deleting it left the
# whole suite green, because T5 only ever exercised render_brief's `why` mapping.
out["t7_no_data"] = lev.no_worst_line(
    {"results": [], "closure": {"sensor_live": False, "blindness": "no_data"}})
out["t7_blind"] = lev.no_worst_line(
    {"results": [], "closure": {"sensor_live": False, "blindness": "blind"}})
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
[ "$(jget t5_blindness)" = "no_data" ] \
  && ok "T5a closure_verdict() carries blindness through to the brief's input" \
  || bad "T5a closure_verdict() blindness was '$(jget t5_blindness)', expected 'no_data'"

if [ -z "$T5" ]; then
  bad "T5 render_brief() missing; assertion is vacuous, fix the test"
elif printf '%s' "$T5" | grep -q "no sessions read this window"; then
  ok "T5 brief names absence, not death (end-to-end: raw → closure_verdict → brief)"
else
  bad "T5 brief did not name absence: $(printf '%s' "$T5" | tr '\n' '|')"
fi

T6=$(jget t6)
if [ -z "$T6" ]; then
  bad "T6 render_brief() missing; assertion is vacuous, fix the test"
elif printf '%s' "$T6" | grep -q "(None)"; then
  bad "T6 a closure with no 'blindness' key rendered '(None)' — --cached reads exactly those"
elif printf '%s' "$T6" | grep -q "loop NOT closed (sensor dead)"; then
  ok "T6 pre-existing state file degrades to the old wording, not to a blank"
else
  bad "T6 unexpected fallback rendering: $(printf '%s' "$T6" | tr '\n' '|')"
fi

# --- T7: the SECOND renderer path (no_worst_line), which T5 does not reach --------
T7N=$(jget t7_no_data); T7B=$(jget t7_blind)
if printf '%s' "$T7N" | grep -q "no session file matched this window" \
   && [ "$T7N" != "$T7B" ]; then
  ok "T7 no_worst_line() distinguishes no_data from blind on its own path"
else
  bad "T7 no_worst_line() collapsed the two: no_data='$T7N' blind='$T7B'"
fi
printf '%s' "$T7N" | grep -qi "nothing to read" \
  && bad "T7b no_worst_line() still certifies the benign cause ('nothing to read')" \
  || ok "T7b no_worst_line() names the observation, not the conclusion"

# --- T8/T9: doctor §23 must not certify, and must not go silent on a CI lane ------
# P20 Stratum B, F1: `no_data` as a QUIET-gated info line turned `--quiet --strict`
# from FAIL into PASS on a workspace whose transcript glob was mis-derived - the
# exact bug bstack 0.30.0 shipped (`sde_vault` globbed 0 files forever). These pin
# both halves: the glob is surfaced, and BSTACK_LOOP_STRICT makes it a hard gap.
DOCTOR="$BSTACK_REPO/scripts/doctor.sh"
if [ ! -f "$DOCTOR" ]; then
  bad "T8 doctor.sh not found; assertion is vacuous, fix the test"
else
  _W=$(mktemp -d); mkdir -p "$_W/.control"
  MARKER="/sentinel-glob-marker/*.jsonl"
  python3 -c "
import json,sys
json.dump({'closure':{'closed':False,'sensor_live':False,'blindness':'no_data',
                      'levels_closed':False,'reference_authored':True,
                      'levels':{'L0':{'live':False}}},
           'sessions_analyzed':0,'transcript_glob':sys.argv[2]},
          open(sys.argv[1]+'/.control/leverage-state.json','w'))
" "$_W" "$MARKER"

  D_SOFT=$(BROOMVA_WORKSPACE="$_W" bash "$DOCTOR" 2>&1)
  # -F, not a regex: the glob contains `*`, and as a BRE `/*` means "zero or more
  # slashes", which does NOT match the literal text. That mismatch is what made the
  # first version of T8 fail against a doctor that was printing the glob correctly.
  if printf '%s' "$D_SOFT" | grep -qF -- "$MARKER"; then
    ok "T8 doctor prints the derived glob, so 'no data' is falsifiable"
  else
    bad "T8 doctor did not surface the transcript glob — 'no data' is unverifiable"
  fi
  # T8b asserts an ABSENCE, so it needs a positive control: doctor producing no
  # output at all would otherwise satisfy it.
  if ! printf '%s' "$D_SOFT" | grep -qi "zero sessions"; then
    bad "T8b positive control failed — doctor never reached the no_data branch; the absence assertion below would be vacuous"
  elif printf '%s' "$D_SOFT" | grep -qi "not a defect"; then
    bad "T8b doctor certifies 'not a defect' for a cause it never checked"
  else
    ok "T8b doctor does not certify the benign cause (positive control held)"
  fi

  D_STRICT=$(BSTACK_LOOP_STRICT=1 BROOMVA_WORKSPACE="$_W" bash "$DOCTOR" --quiet 2>&1)
  if printf '%s' "$D_STRICT" | grep -qi "zero sessions"; then
    ok "T9 BSTACK_LOOP_STRICT + --quiet still reports zero sessions (CI lane cannot go silent)"
  else
    bad "T9 --quiet --strict suppressed the zero-session signal — FAIL silently becomes PASS"
  fi
  rm -rf "$_W"
fi

# --- T10: the SENSOR must actually emit transcript_glob ---------------------------
# T8 feeds doctor a SYNTHETIC state file, so it proves doctor can print the field,
# never that anything writes it. Measured: deleting `"transcript_glob": glob_pat`
# from the record left T8 green. This closes that gap by reading the real emitter.
_W2=$(mktemp -d)
_JSON=$(timeout 120 python3 "$SENSOR" --workspace "$_W2" \
          --transcripts "$_W2/none/*.jsonl" --window 7 --json --no-store 2>/dev/null)
rm -rf "$_W2"
if [ -z "$_JSON" ]; then
  bad "T10 sensor --json produced nothing; assertion is vacuous, fix the test"
else
  _HASGLOB=$(printf '%s' "$_JSON" | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
except Exception:
    print('PARSE_FAIL'); raise SystemExit
print('YES' if d.get('transcript_glob') else 'NO')" 2>/dev/null)
  case "$_HASGLOB" in
    YES) ok "T10 the sensor records transcript_glob (doctor has something real to print)" ;;
    NO)  bad "T10 sensor record carries no transcript_glob — doctor's glob line can only ever print a synthetic value" ;;
    *)   bad "T10 sensor --json was unparseable ($_HASGLOB); assertion is vacuous" ;;
  esac
fi

echo
echo "  ${PASS} passed, ${FAIL} failed"
[ "$FAIL" -eq 0 ]
