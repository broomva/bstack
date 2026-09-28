#!/usr/bin/env bash
# tests/l3-rate-gate.test.sh — L3 rate gate counts MUTATIONS, not creations (BRO-1435).
#
# Regression guard for the Day-1 bug where `bstack bootstrap`'s initial commit
# (which CREATES the governance files) was blocked by the rate gate. The gate
# must:
#   A. exempt newly-CREATED L3 files (creation is not mutation),
#   B. allow 1 L3 MODIFICATION per window,
#   C. block the 2nd L3 modification in the same window,
#   D. ignore non-governance changes.
#
# Cases E-J cover the declared-correction lane: a change that restores
# correspondence between an L3 rule and the tree spends a separate, smaller
# budget instead of the mutation budget. The lane is DECLARED, not detected, so
# the cases that matter most are the ones proving it is bounded — F (the lane
# runs out) and G (an undeclared change is still blocked with the lane wide
# open). Without those two this would be an unbounded self-granted exemption.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
GATE="$SCRIPT_DIR/scripts/l3-rate-gate.sh"

pass=0; fail=0
check() { # name  want_exit  got_exit
    if [ "$3" = "$2" ]; then echo "  [pass] $1"; pass=$((pass + 1))
    else echo "  [FAIL] $1 — want exit $2, got $3"; fail=$((fail + 1)); fi
}

WS="$(mktemp -d)"; trap 'rm -rf "$WS"' EXIT
cd "$WS" || exit 2
git init -q; git config user.email t@t; git config user.name t
printf 'x\n' > app.py; git add app.py; git commit -q -m seed

mkdir -p .control
cp "$SCRIPT_DIR/assets/templates/rcs-parameters.toml.template" .control/rcs-parameters.toml
printf '# gov\n' > CLAUDE.md; printf '# gov\n' > AGENTS.md; printf '# gov\n' > METALAYER.md
printf 'version: "1.0"\n' > .control/policy.yaml

echo "L3 rate gate — creation vs mutation (BRO-1435)"

# A — create governance files (the bstack bootstrap scenario): EXEMPT
git add -A
BROOMVA_WORKSPACE="$WS" bash "$GATE" --staged >/dev/null 2>&1; check "A: creation of 5 L3 files is exempt (exit 0)" 0 $?
git commit -q -m "create governance"

# B — modify ONE existing governance file: within budget
printf '# tweak\n' >> CLAUDE.md; git add CLAUDE.md
BROOMVA_WORKSPACE="$WS" bash "$GATE" --staged >/dev/null 2>&1; check "B: 1 modification within budget (exit 0)" 0 $?
git commit -q -m "modify governance #1"

# C — modify again in the same window: EXCEEDED
printf '# tweak2\n' >> CLAUDE.md; git add CLAUDE.md
BROOMVA_WORKSPACE="$WS" bash "$GATE" --staged >/dev/null 2>&1; check "C: 2nd modification in window blocked (exit 1)" 1 $?

# D — non-governance change only: ignored
git restore --staged . 2>/dev/null || git reset -q
git checkout -q -- CLAUDE.md 2>/dev/null || true
printf 'y\n' >> app.py; git add app.py
BROOMVA_WORKSPACE="$WS" bash "$GATE" --staged >/dev/null 2>&1; check "D: non-governance change ignored (exit 0)" 0 $?

echo "L3 rate gate — the declared-correction lane"

# Reset to a clean window boundary: a fresh workspace, one governance mutation
# already spent, so every case below starts from "the mutation budget is gone".
spend_mutation_budget() {
  git restore --staged . 2>/dev/null || git reset -q
  git checkout -q -- . 2>/dev/null || true
  printf '# spent\n' >> CLAUDE.md
  git add CLAUDE.md
  git commit -q -m "modify governance (spends the mutation budget)"
}
spend_mutation_budget

# E — the budget is gone, and a DECLARED correction still commits.
printf '# corrected\n' >> AGENTS.md; git add AGENTS.md
BSTACK_L3_CORRECTION="the rule states what the code disproved" \
  BROOMVA_WORKSPACE="$WS" bash "$GATE" --staged >/dev/null 2>&1
check "E: a declared correction does not spend the mutation budget (exit 0)" 0 $?

# G first, because it shares E's staged state: the SAME staged change, undeclared,
# is still blocked. This is what stops the lane being a blanket exemption.
BROOMVA_WORKSPACE="$WS" bash "$GATE" --staged >/dev/null 2>&1
check "G: the same change undeclared is still blocked (exit 1)" 1 $?

# H — an EMPTY reason is not a declaration.
BSTACK_L3_CORRECTION="" BROOMVA_WORKSPACE="$WS" bash "$GATE" --staged >/dev/null 2>&1
check "H: an empty BSTACK_L3_CORRECTION is not a declaration (exit 1)" 1 $?

git commit -q -m "correct AGENTS.md

L3-Correction: the rule states what the code disproved"

# I — the trailer is what makes a COMMITTED correction countable as one. The
# mutation budget is still at 1 of 1 (only the spend_mutation_budget commit), so
# an ordinary staged change is blocked while a declared one is not.
printf '# more\n' >> METALAYER.md; git add METALAYER.md
BROOMVA_WORKSPACE="$WS" bash "$GATE" --staged >/dev/null 2>&1
check "I: a trailer-carrying commit is not counted as a mutation (exit 1 for the undeclared next one)" 1 $?

# F — the lane is FINITE. correction_budget defaults to 3; commit three declared
# corrections, then a fourth must be refused. An exemption that cannot be
# exhausted is an exemption, not a budget.
BSTACK_L3_CORRECTION="c2" BROOMVA_WORKSPACE="$WS" bash "$GATE" --staged >/dev/null 2>&1
check "F1: 2nd declared correction still within lane (exit 0)" 0 $?
git commit -q -m "correct 2

L3-Correction: c2"

printf '# p\n' >> .control/policy.yaml; git add .control/policy.yaml
BSTACK_L3_CORRECTION="c3" BROOMVA_WORKSPACE="$WS" bash "$GATE" --staged >/dev/null 2>&1
check "F2: 3rd declared correction still within lane (exit 0)" 0 $?
git commit -q -m "correct 3

L3-Correction: c3"

printf '# q\n' >> CLAUDE.md; git add CLAUDE.md
BSTACK_L3_CORRECTION="c4" BROOMVA_WORKSPACE="$WS" bash "$GATE" --staged >/dev/null 2>&1
check "F3: 4th declared correction EXHAUSTS the lane (exit 1)" 1 $?

# J — the lane names itself in the JSON contract, so a caller can tell the two
# refusals apart rather than reading prose.
lane="$(BSTACK_L3_CORRECTION="c4" BROOMVA_WORKSPACE="$WS" bash "$GATE" --staged --json 2>/dev/null \
  | grep -o '"exceeded_lane": "[a-z]*"' | head -1)"
if [ "$lane" = '"exceeded_lane": "correction"' ]; then
  echo "  [pass] J: JSON names the exhausted lane"; pass=$((pass + 1))
else
  echo "  [FAIL] J: JSON names the exhausted lane — got: $lane"; fail=$((fail + 1))
fi

# A workspace of its own, so the verdict is decided ONLY by the thing under test.
# The first version of K and L reused the shared $WS, whose budget was already
# blown by earlier cases; both passed in BOTH worlds and proved nothing. Caught
# by reverting each fix and seeing the suite stay green.
fresh_ws() {
  local d; d="$(mktemp -d)"
  git -C "$d" init -q; git -C "$d" config user.email t@t; git -C "$d" config user.name t
  mkdir -p "$d/.control"
  cp "$SCRIPT_DIR/assets/templates/rcs-parameters.toml.template" "$d/.control/rcs-parameters.toml"
  printf '# gov\n' > "$d/CLAUDE.md"; printf '# gov\n' > "$d/AGENTS.md"
  printf '# gov\n' > "$d/METALAYER.md"; printf 'version: "1.0"\n' > "$d/.control/policy.yaml"
  git -C "$d" add -A; git -C "$d" commit -q -m "create governance"
  echo "$d"
}

# K — a line starting with the trailer key in a MIDDLE paragraph is prose, not a
# declaration. git treats only the final paragraph as trailers. Discriminating
# because the window then holds exactly one MUTATION: if the pseudo-trailer were
# counted as a correction the staged change would be 1/1 and pass, and if it is
# correctly read as prose the staged change is 2/1 and is refused.
K="$(fresh_ws)"
printf '# k\n' >> "$K/AGENTS.md"; git -C "$K" add AGENTS.md
git -C "$K" commit -q -m "not actually a correction

L3-Correction: this sits in a middle paragraph

and this trailing prose is what stops git treating the line above as a trailer."
printf '# k2\n' >> "$K/METALAYER.md"; git -C "$K" add METALAYER.md
BROOMVA_WORKSPACE="$K" bash "$GATE" --staged >/dev/null 2>&1
check "K: a mid-body line is prose, not a trailer (exit 1)" 1 $?

# K2 — POSITIVE CONTROL for K. Same setup, the key in the FINAL paragraph, which
# is a real trailer. Without this, K would also pass against a gate that never
# recognised any trailer at all.
K2="$(fresh_ws)"
printf '# k\n' >> "$K2/AGENTS.md"; git -C "$K2" add AGENTS.md
git -C "$K2" commit -q -m "a real correction

L3-Correction: stated in the trailer block where git can see it"
printf '# k2\n' >> "$K2/METALAYER.md"; git -C "$K2" add METALAYER.md
BROOMVA_WORKSPACE="$K2" bash "$GATE" --staged >/dev/null 2>&1
check "K2: a real trailer IS recognised, so K is not vacuous (exit 0)" 0 $?

# L — bool is a subclass of int in Python, so `correction_budget = true` was
# emitted as CORRECTION_BUDGET=True and every [ n -gt True ] then errored.
# Edited IN the existing [gates.l3_paths] table: the first version appended a
# second one, which is a TOML duplicate-key error, so the whole config was
# discarded and the default appeared for the wrong reason.
budget_json() {
  local d="$1" val="$2"
  python3 - "$d/.control/rcs-parameters.toml" "$val" <<'PY'
import re, sys
path, val = sys.argv[1], sys.argv[2]
src = open(path).read()
src = re.sub(r"(?m)^correction_budget\s*=.*$", "", src)
src = src.replace("[gates.l3_paths]", f"[gates.l3_paths]\ncorrection_budget = {val}", 1)
open(path, "w").write(src)
PY
  BSTACK_L3_CORRECTION="c" BROOMVA_WORKSPACE="$d" bash "$GATE" --staged --json 2>&1 \
    | grep -o '"correction_budget": [A-Za-z0-9]*'
}

# L1 — POSITIVE CONTROL: a valid integer must actually reach the gate, or L2
# below is satisfied by a default that was never configurable in the first place.
L1="$(fresh_ws)"
got="$(budget_json "$L1" 5)"
if [ "$got" = '"correction_budget": 5' ]; then
  echo "  [pass] L1: an integer correction_budget is read from config"; pass=$((pass + 1))
else
  echo "  [FAIL] L1: an integer correction_budget is read from config — got: $got"; fail=$((fail + 1))
fi

# L2 — a bool must be REJECTED. Since BRO-2651 a malformed config fails CLOSED
# (exit 2) instead of being dropped for the default in silence, so the JSON
# never reaches stdout and the exit code is the verdict.
L2="$(fresh_ws)"
budget_json "$L2" true >/dev/null
BSTACK_L3_CORRECTION="c" BROOMVA_WORKSPACE="$L2" bash "$GATE" --staged --json >/dev/null 2>&1
check "L2: a boolean correction_budget fails closed (exit 2)" 2 $?

echo "─────────────────────────────────────"
# -- M: the correction budget must not be settable from the environment -------
#
# `CORRECTION_BUDGET="${CORRECTION_BUDGET:-3}"` reads an ambient variable of that
# plain, unnamespaced name. The TOML reader only EMITS CORRECTION_BUDGET when the
# config sets it, so with the key absent -- the default for every install, since
# rcs-parameters.toml.template does not ship it -- nothing defined the variable
# and the caller's environment reached the comparison.
#
# Not a cosmetic precedence nit: it is the difference between a bounded
# self-asserted exemption and an unbounded one. `CORRECTION_BUDGET=999` buys
# unlimited declared L3 corrections with no config edit, no bypass flag, and no
# trace in any commit message. This lane's own header names finiteness as the
# first of three things that make a self-asserted declaration acceptable ("the
# budget is finite, so a false claim buys 3 and not infinity"). L1 and L2 cover
# config VALUES; nothing covered where a value may come FROM.
#
# TAU_A_L3 is not exposed this way only because the config always emits it, so
# the hole is a property of being OPTIONAL. M2 pins the config path as well: the
# reset must not make a legitimately configured budget unreachable.
env_budget() { # env_budget <workspace> <ambient-value>
  local d="$1" amb="$2"
  CORRECTION_BUDGET="$amb" BSTACK_L3_CORRECTION="c" BROOMVA_WORKSPACE="$d" \
    bash "$GATE" --staged --json 2>&1 | grep -o '"correction_budget": [A-Za-z0-9]*'
}

strip_budget_key() {
  python3 -c 'import re,sys; p=sys.argv[1]; s=open(p).read(); open(p,"w").write(re.sub(r"(?m)^correction_budget\s*=.*$","",s))' "$1"
}

# M1 -- key ABSENT from config: an ambient value must be ignored.
M1="$(fresh_ws)"
strip_budget_key "$M1/.control/rcs-parameters.toml"
got="$(env_budget "$M1" 999)"
if [ "$got" = '"correction_budget": 3' ]; then
  echo "  [pass] M1: an ambient CORRECTION_BUDGET cannot raise the lane"; pass=$((pass + 1))
else
  echo "  [FAIL] M1: ambient CORRECTION_BUDGET reached the gate -- got: $got"; fail=$((fail + 1))
fi

# M2 -- key PRESENT in config: config wins, and the reset did not sever it.
M2="$(fresh_ws)"
budget_json "$M2" 7 >/dev/null
got="$(env_budget "$M2" 999)"
if [ "$got" = '"correction_budget": 7' ]; then
  echo "  [pass] M2: config wins over a hostile ambient value"; pass=$((pass + 1))
else
  echo "  [FAIL] M2: configured budget not honoured alongside an ambient var -- got: $got"; fail=$((fail + 1))
fi

echo "L3 rate gate — the config is data, never shell (BRO-2651)"
#
# The reader used to print shell assignments and `eval` them, so a pattern of
# "$(cmd)" or a tau_a of "86400; cmd" ran cmd. In CI the config is the PR head's,
# so that was command execution on the runner for anyone able to open a PR.
#
# Every payload case asserts BOTH polarities:
#   - the marker file does NOT exist after the gate ran (nothing executed), and
#   - a positive control proves the same payload DOES create the marker when it
#     is handed to `eval` in the shape the old reader used. Without the control,
#     a payload that could never fire (a typo, a quoting slip in this file)
#     would pass the first assertion and prove nothing.

# A workspace whose config is replaced by a hand-written one. Only the key under
# test differs from a valid config.
config_ws() { # config_ws <patterns-toml> <tau-toml> [extra-l3-gate-lines]
  local d; d="$(mktemp -d)"
  git -C "$d" init -q; git -C "$d" config user.email t@t; git -C "$d" config user.name t
  mkdir -p "$d/.control"
  printf '[[levels]]\nid = "L3"\ntau_a = %s\n\n[gates.l3_paths]\npatterns = %s\n%s\n' \
    "$2" "$1" "${3:-}" > "$d/.control/rcs-parameters.toml"
  printf '# gov\n' > "$d/CLAUDE.md"
  git -C "$d" add -A; git -C "$d" commit -q -m seed
  echo "$d"
}

# The old reader's two sinks, reproduced so the control fires in the same way.
old_sink_pattern() { eval "L3_PATHS=(\"$1\")"; }
old_sink_tau()     { eval "TAU_A_L3=$1"; }

# Markers are ABSOLUTE paths outside every workspace. The old reader ran eval
# BEFORE the gate's own cd, so a relative marker would land in whatever
# directory the test happened to be in, and "no marker in the workspace" would
# pass against the vulnerable gate.
newmark() { M="$(mktemp -d)/PWNED"; }

payload_case() { # payload_case <name> <sink> <raw-value> <patterns-toml> <tau-toml> <want-exit>
  local name="$1" sink="$2" raw="$3" pat="$4" tau="$5" want="$6" d rc
  d="$(config_ws "$pat" "$tau")"
  # Control: the raw value is live under the old shape.
  "$sink" "$raw" >/dev/null 2>&1
  if [ -e "$M" ]; then
    echo "  [pass] $name control: the payload executes under the old eval shape"; pass=$((pass + 1))
    rm -f "$M"
  else
    echo "  [FAIL] $name control: the payload did not fire under eval, so the case is vacuous"; fail=$((fail + 1))
  fi
  BROOMVA_WORKSPACE="$d" bash "$GATE" --json >/dev/null 2>&1; rc=$?
  if [ -e "$M" ]; then
    echo "  [FAIL] $name: the gate EXECUTED the payload (marker exists)"; fail=$((fail + 1))
  else
    echo "  [pass] $name: no marker, nothing executed"; pass=$((pass + 1))
  fi
  check "$name: exit code" "$want" "$rc"
  rm -rf "$d" "$(dirname "$M")"
}

# N1 — command substitution in a pattern. It IS a valid string, so the gate reads
# it as a literal pathspec and runs normally (exit 0).
newmark; payload_case "N1 pattern \$(...)" old_sink_pattern "\$(touch $M)" \
  "[\"CLAUDE.md\", \"\$(touch $M)\"]" 86400 0
# N2 — backtick substitution in a pattern.
newmark; payload_case "N2 pattern backticks" old_sink_pattern "\`touch $M\`" \
  "[\"CLAUDE.md\", \"\`touch $M\`\"]" 86400 0
# N3 — a pattern that closes the old double quote and appends a command.
newmark; payload_case "N3 pattern quote breakout" old_sink_pattern "x\"); touch $M; #" \
  "['CLAUDE.md', 'x\"); touch $M; #']" 86400 0
# N4 — tau_a as a string carrying a command: not a number, so it fails CLOSED.
newmark; payload_case "N4 tau_a string" old_sink_tau "86400; touch $M" \
  '["CLAUDE.md"]' "\"86400; touch $M\"" 2
# N5 — tau_a as command substitution.
newmark; payload_case "N5 tau_a \$(...)" old_sink_tau "\$(touch $M)" \
  '["CLAUDE.md"]' "\"\$(touch $M)\"" 2
# N5b — correction_budget cannot carry a payload either: it fails CLOSED before
# anything is emitted. (The old reader only emitted it as a checked int.)
newmark
N5b="$(config_ws '["CLAUDE.md"]' 86400 "correction_budget = \"3; touch $M\"")"
BROOMVA_WORKSPACE="$N5b" bash "$GATE" >/dev/null 2>&1; rc=$?
check "N5b: a string correction_budget fails closed (exit 2)" 2 "$rc"
[ -e "$M" ] && { echo "  [FAIL] N5b: marker exists"; fail=$((fail + 1)); }
rm -rf "$N5b" "$(dirname "$M")"

# N6 — the literal survives as DATA. A reader that simply discarded every odd
# pattern would pass N1-N3 too; the JSON must carry the pattern verbatim.
newmark
N6="$(config_ws "[\"CLAUDE.md\", \"\$(touch $M)\", \"a\\\\b\\\"c\"]" 86400)"
got="$(BROOMVA_WORKSPACE="$N6" bash "$GATE" --json 2>/dev/null)"
want_paths="\"l3_paths\": [\"CLAUDE.md\",\"\$(touch $M)\",\"a\\\\b\\\"c\"]"
if printf '%s\n' "$got" | grep -qF "$want_paths" \
   && printf '%s' "$got" | python3 -c 'import json,sys; json.load(sys.stdin)' 2>/dev/null; then
  echo "  [pass] N6: the payload is carried verbatim as data, in valid JSON"; pass=$((pass + 1))
else
  echo "  [FAIL] N6: payload not carried verbatim as data — got: $(printf '%s' "$got" | grep l3_paths)"; fail=$((fail + 1))
fi
[ -e "$M" ] && { echo "  [FAIL] N6: marker exists"; fail=$((fail + 1)); }
rm -rf "$N6" "$(dirname "$M")"

# N7 — a newline inside a pattern would forge a second record in a line-based
# transport. The reader refuses control characters, so it fails CLOSED.
N7="$(config_ws '["CLAUDE.md", "x\nTAU_A_L3\t1"]' 86400)"
BROOMVA_WORKSPACE="$N7" bash "$GATE" --json >/dev/null 2>&1
check "N7: a control character in a pattern fails closed (exit 2)" 2 $?
rm -rf "$N7"

echo "L3 rate gate — malformed config fails CLOSED (BRO-2651)"
# The stderr must NAME the offending key. That is what an operator needs, and it
# is what separates a deliberate refusal from a crash that happens to exit
# non-zero: with a type check deleted, a string tau_a still stops the gate, but
# only via a python traceback, and the exit code alone cannot tell them apart.
malformed() { # malformed <name> <patterns-toml> <tau-toml> <stderr-key> [extra]
  local d err rc; d="$(config_ws "$2" "$3" "${5:-}")"
  err="$(BROOMVA_WORKSPACE="$d" bash "$GATE" --warn-only 2>&1 >/dev/null)"; rc=$?
  check "$1 (exit 2, even under --warn-only)" 2 "$rc"
  if printf '%s' "$err" | grep -q "malformed config.*$4" && ! printf '%s' "$err" | grep -q Traceback; then
    echo "  [pass] $1: the refusal names $4"; pass=$((pass + 1))
  else
    echo "  [FAIL] $1: the refusal does not name $4 — stderr: $err"; fail=$((fail + 1))
  fi
  rm -rf "$d"
}
malformed "O1: tau_a as a numeric STRING"   '["CLAUDE.md"]' '"86400"' tau_a
malformed "O2: tau_a = true"                '["CLAUDE.md"]' 'true' tau_a
malformed "O3: tau_a = 0"                   '["CLAUDE.md"]' '0' tau_a
malformed "O4: tau_a = nan"                 '["CLAUDE.md"]' 'nan' tau_a
malformed "O5: tau_a beyond one year"       '["CLAUDE.md"]' '31536001' tau_a
malformed "O6: patterns is a string"        '"CLAUDE.md"'   86400 patterns
malformed "O7: a pattern is a number"       '["CLAUDE.md", 7]' 86400 patterns
malformed "O8: an empty pattern"            '["CLAUDE.md", ""]' 86400 patterns
malformed "O9: correction_budget a string"  '["CLAUDE.md"]' 86400 correction_budget 'correction_budget = "3; touch x"'
malformed "O10: correction_budget negative" '["CLAUDE.md"]' 86400 correction_budget 'correction_budget = -1'
O11="$(config_ws '["CLAUDE.md"]' 86400)"
printf 'this is = = not toml [\n' >> "$O11/.control/rcs-parameters.toml"
BROOMVA_WORKSPACE="$O11" bash "$GATE" >/dev/null 2>&1
check "O11: unparseable TOML fails closed (exit 2)" 2 $?
rm -rf "$O11"
O12="$(config_ws '["CLAUDE.md"]' 86400)"
BROOMVA_WORKSPACE="$O12" bash "$GATE" --window='1;touch x' >/dev/null 2>&1
check "O12: a non-numeric --window is refused (exit 2)" 2 $?
rm -rf "$O12"

echo "L3 rate gate — benign configs read exactly as before (BRO-2651)"
# P1 — the shipped template: default window and every template pattern.
P1="$(fresh_ws)"
got="$(BROOMVA_WORKSPACE="$P1" bash "$GATE" --json 2>/dev/null)"
want_tmpl="$(python3 - "$SCRIPT_DIR/assets/templates/rcs-parameters.toml.template" <<'PY'
import sys, tomllib
d = tomllib.load(open(sys.argv[1], "rb"))
print(",".join('"%s"' % p for p in d["gates"]["l3_paths"]["patterns"]))
PY
)"
if printf '%s\n' "$got" | grep -qF '"window_seconds": 86400,' \
   && printf '%s\n' "$got" | grep -qF "\"l3_paths\": [$want_tmpl]"; then
  echo "  [pass] P1: the template reads as 86400s and its own pattern list"; pass=$((pass + 1))
else
  echo "  [FAIL] P1: template not read as before — got: $(printf '%s' "$got" | grep -E 'window|l3_paths')"; fail=$((fail + 1))
fi
rm -rf "$P1"
# P2 — a float tau_a rounds the way the old printf '%.0f' did.
P2="$(config_ws '["CLAUDE.md"]' 3600.4)"
BROOMVA_WORKSPACE="$P2" bash "$GATE" --json 2>/dev/null | grep -qF '"window_seconds": 3600,'
check "P2: a float tau_a (3600.4) reads as 3600s" 0 $?
rm -rf "$P2"
# P3 — an absent tau_a and an absent pattern list take the documented defaults,
# and an ambient TAU_A_L3 does not (the same hole M closed for the budget).
P3="$(mktemp -d)"
git -C "$P3" init -q; mkdir -p "$P3/.control"
printf '[[levels]]\nid = "L3"\n' > "$P3/.control/rcs-parameters.toml"
got="$(TAU_A_L3=5 BROOMVA_WORKSPACE="$P3" bash "$GATE" --json 2>/dev/null)"
if printf '%s\n' "$got" | grep -qF '"window_seconds": 86400,' \
   && printf '%s\n' "$got" | grep -qF '"l3_paths": ["CLAUDE.md","AGENTS.md",".control/policy.yaml",".control/rcs-parameters.toml","METALAYER.md"]'; then
  echo "  [pass] P3: absent keys take the defaults; an ambient TAU_A_L3 is ignored"; pass=$((pass + 1))
else
  echo "  [FAIL] P3: defaults not applied — got: $(printf '%s' "$got" | grep -E 'window|l3_paths')"; fail=$((fail + 1))
fi
rm -rf "$P3"
# A python3 shim first on PATH. The gate runs `python3 -I`, which ignores
# PYTHONPATH, so the only way to change what the reader sees is to change the
# interpreter. The shim prepends $SHIM_PRELUDE to the program on stdin and
# execs the real python3 with the same arguments.
REAL_PY="$(command -v python3)"
SHIMDIR="$(mktemp -d)"
cat > "$SHIMDIR/python3" <<SH
#!/usr/bin/env bash
prog="\$(cat)"
printf '%s\n%s\n' "\${SHIM_PRELUDE:-}" "\$prog" | exec "$REAL_PY" "\$@"
SH
chmod +x "$SHIMDIR/python3"

# P4 — no TOML parser at all: nothing is read, so nothing can execute; the
# defaults apply with a warning.
newmark
P4="$(config_ws "[\"\$(touch $M)\"]" "\"1; touch $M\"")"
err="$(SHIM_PRELUDE='import sys; sys.modules["tomllib"] = None; sys.modules["tomli"] = None' \
  PATH="$SHIMDIR:$PATH" BROOMVA_WORKSPACE="$P4" bash "$GATE" 2>&1 >/dev/null)"; rc=$?
if [ "$rc" = 0 ] && [ ! -e "$M" ] && printf '%s' "$err" | grep -q 'no TOML parser'; then
  echo "  [pass] P4: no parser -> defaults, a warning, and no execution"; pass=$((pass + 1))
else
  echo "  [FAIL] P4: rc=$rc marker=$([ -e "$M" ] && echo yes || echo no) err=$err"; fail=$((fail + 1))
fi
rm -rf "$P4" "$(dirname "$M")"

echo "L3 rate gate — a PR's own python modules are never imported (BRO-2651 round 2)"
# `python3 -` puts the current directory first on sys.path. In CI the current
# directory is the PR checkout, so a tomllib.py or math.py committed at its
# root replaced the standard library and ran as code, with the gate still
# exiting 0. Both scripts the workflow runs are covered: this gate and
# compute-lambda.sh. Control: the same shim IS imported by a plain `python3 -`
# from that directory, so the case cannot pass because the shim never loads.
# A module compiled into the interpreter cannot be shadowed from a directory
# (math is built in on Linux CPython, a separate file on macOS), so it is
# skipped there; the positive control below would otherwise report the case as
# vacuous, which is exactly what it caught in CI. tomllib is pure Python on
# every build, so at least one module is always exercised.
for mod in tomllib math; do
  if "$REAL_PY" -I -c "import sys; sys.exit(0 if '$mod' in sys.builtin_module_names else 1)"; then
    echo "  [info] Q-$mod: built into this interpreter, cannot be shadowed; skipped"
    continue
  fi
  newmark
  Q="$(fresh_ws)"
  printf 'import os\nopen(%s, "w").close()\n' "'$M'" > "$Q/$mod.py"
  ( cd "$Q" && printf 'import %s\n' "$mod" | "$REAL_PY" - ) >/dev/null 2>&1
  if [ -e "$M" ]; then
    echo "  [pass] Q-$mod control: a plain python3 - imports the PR's $mod.py"; pass=$((pass + 1)); rm -f "$M"
  else
    echo "  [FAIL] Q-$mod control: the shadow module never loaded, so the case is vacuous"; fail=$((fail + 1))
  fi
  ( cd "$Q" && BROOMVA_WORKSPACE="$Q" bash "$GATE" --json ) >/dev/null 2>&1
  if [ -e "$M" ]; then
    echo "  [FAIL] Q-$mod: l3-rate-gate imported the PR's $mod.py"; fail=$((fail + 1)); rm -f "$M"
  else
    echo "  [pass] Q-$mod: l3-rate-gate does not import the PR's $mod.py"; pass=$((pass + 1))
  fi
  ( cd "$Q" && BROOMVA_WORKSPACE="$Q" bash "$SCRIPT_DIR/scripts/compute-lambda.sh" --human ) >/dev/null 2>&1
  if [ -e "$M" ]; then
    echo "  [FAIL] Q-$mod: compute-lambda imported the PR's $mod.py"; fail=$((fail + 1))
  else
    echo "  [pass] Q-$mod: compute-lambda does not import the PR's $mod.py"; pass=$((pass + 1))
  fi
  rm -rf "$Q" "$(dirname "$M")"
done

echo "L3 rate gate — the shell side trusts no record (BRO-2651 round 2)"
# The reader validates in python; bash re-checks what reaches arithmetic and
# refuses unknown keys. Those checks are unreachable through a correct reader,
# so each case replaces the reader's output with a hostile record set via the
# shim (the prelude prints the records and exits before the real program).
hostile() { # hostile <name> <want-exit> <records-printf-format>
  local d rc; newmark; d="$(fresh_ws)"
  SHIM_PRELUDE="import sys; sys.stdout.write('$3'.replace('@M', '$M')); sys.exit(0)" \
    PATH="$SHIMDIR:$PATH" BROOMVA_WORKSPACE="$d" bash "$GATE" --json >/dev/null 2>&1; rc=$?
  check "$1" "$2" "$rc"
  if [ -e "$M" ]; then echo "  [FAIL] $1: marker exists"; fail=$((fail + 1)); fi
  rm -rf "$d" "$(dirname "$M")"
}
hostile "R1: a non-numeric TAU_A_L3 record is refused (exit 2)"        2 'TAU_A_L3\t1;touch @M\n'
hostile "R2: a non-numeric CORRECTION_BUDGET record is refused (exit 2)" 2 'CORRECTION_BUDGET\t$(touch @M)\n'
hostile "R3: an unknown record key is refused (exit 2)"                2 'L3_PATHS\tx\n'
hostile "R4: a PATH record is assigned as data, never run (exit 0)"    0 'PATH\t$(touch @M)\nPATH\t`touch @M`\n'
rm -rf "$SHIMDIR"

# O13 — the pattern count is capped.
O13="$(config_ws "[$(python3 -c 'print(",".join(["\"p%d\"" % i for i in range(257)]))')]" 86400)"
BROOMVA_WORKSPACE="$O13" bash "$GATE" >/dev/null 2>&1
check "O13: 257 patterns fail closed (exit 2)" 2 $?
rm -rf "$O13"
# O14 — --window has the config's one-year ceiling; past it the cutoff overflows.
O14="$(config_ws '["CLAUDE.md"]' 86400)"
BROOMVA_WORKSPACE="$O14" bash "$GATE" --window=99999999999999999999999 >/dev/null 2>&1
check "O14: an overflowing --window is refused (exit 2)" 2 $?
BROOMVA_WORKSPACE="$O14" bash "$GATE" --window=3600 >/dev/null 2>&1
check "O14b: a normal --window still works (exit 0)" 0 $?
rm -rf "$O14"
# O15 — a pattern that looks like a grep option is a path, not an option.
O15="$(config_ws '["CLAUDE.md", "--output=x"]' 86400)"
printf 'x\n' > "$O15/--output=x"
git -C "$O15" add -A >/dev/null 2>&1; git -C "$O15" commit -qm add >/dev/null 2>&1
printf '# t\n' >> "$O15/CLAUDE.md"; git -C "$O15" add CLAUDE.md
err="$(cd "$O15" && BROOMVA_WORKSPACE="$O15" bash "$GATE" --staged 2>&1 >/dev/null)"
if printf '%s' "$err" | grep -qi 'unrecognized option\|invalid option'; then
  echo "  [FAIL] O15: a pattern reached grep as an option: $err"; fail=$((fail + 1))
else
  echo "  [pass] O15: a pattern starting with -- is not parsed as a grep option"; pass=$((pass + 1))
fi
rm -rf "$O15"

echo "Passed: $pass  Failed: $fail"
[ "$fail" -eq 0 ] && echo "All tests passed." || exit 1
