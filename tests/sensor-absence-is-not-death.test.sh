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
#   T10 the sensor DERIVES the transcript glob (asserted differentially, across
#       two workspaces — a single-workspace shape check passes for any constant). `sessions_analyzed == 0`
#       means "this glob matched nothing", which is benign only if the glob is
#       right — bstack 0.30.0 shipped a path mangle that made `sde_vault` glob 0
#       files forever. Nothing previously emitted the pattern on any channel, so
#       "no data" was an unfalsifiable claim. This scopes to REPORTING only:
#       doctor.sh is untouched by this branch, so no detector changes severity.
#
# Mutation proof — each must turn this file red:
#   * `return "no_data" if ... else "blind"` -> `return "blind"`   kills T1,T5a,T5
#   * `... <= 0` -> `... < 0`                                      kills T1,T5a,T5
#   * sensor_is_live gaining `or sessions == 0`                         kills T4
#   * the `.get(..., "sensor dead")` default dropped to `.get(...)`     kills T6
#   * delete no_worst_line()'s no_data branch                           kills T7
#   * no_worst_line no_data wording back to "nothing to read"           kills T7b
#   * drop "transcript_glob" from the record                           kills T10
#   * transcript_glob := the workspace path                            kills T10
#   * glob_pat := a hardcoded $HOME/.claude/projects/X/*.jsonl          kills T10
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
# whole suite green, because T5 only ever exercised render_brief's 'why' mapping.
# (No backticks in this heredoc: bash32-parse-safety.test.sh flags a literal
# backtick inside a $()-nested quoted heredoc as a bash-3.2 parse hazard.)
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

# --- T10: the SENSOR must actually DERIVE the transcript glob --------------------
# Two workspaces, not one. A single-workspace check can only assert the glob "looks
# like a transcript path", which any hardcoded constant of that shape satisfies —
# P20 round 3 proved it with two survivors: a hardcoded
# $HOME/.claude/projects/HARDCODED/*.jsonl, and a literal "/x/.claude/y.jsonl".
# Running twice makes the assertion differential: a constant cannot vary with its
# input. Basenames are pure-alphanumeric so they survive any sane path mangle
# unchanged, which keeps this from reimplementing the mangle (two implementations
# of one rule is how the 0.30.0 mangle bug hid).
#
# NO --transcripts: transcript_glob(workspace, arg) returns `arg` on its first
# line, so supplying one skips derivation entirely.
_T=$(mktemp -d); mkdir -p "$_T/wsalpha" "$_T/wsbeta"
_glob_for() {
  timeout 120 python3 "$SENSOR" --workspace "$1" --window 7 --json --no-store 2>/dev/null \
    | python3 -c "
import sys, json
try:
    print(json.load(sys.stdin).get('transcript_glob') or '')
except Exception:
    print('')"
}
_GA=$(_glob_for "$_T/wsalpha")
_GB=$(_glob_for "$_T/wsbeta")
rm -rf "$_T"

if [ -z "$_GA" ] || [ -z "$_GB" ]; then
  bad "T10 sensor emitted no transcript_glob (A='$_GA' B='$_GB') — the record carries nothing for anyone to check the 'no data' verdict against"
elif [ "$_GA" = "$_GB" ]; then
  bad "T10 the glob did not vary with the workspace — a constant, not a derivation: '$_GA'"
elif printf '%s' "$_GA" | grep -qF wsalpha && printf '%s' "$_GB" | grep -qF wsbeta; then
  ok "T10 the sensor DERIVES transcript_glob per workspace (differential: two inputs, two globs)"
else
  bad "T10 globs differ but neither carries its own workspace name: A='$_GA' B='$_GB'"
fi

echo
echo "  ${PASS} passed, ${FAIL} failed"
[ "$FAIL" -eq 0 ]
