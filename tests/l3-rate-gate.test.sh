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
# Cases E-J cover the declared-correction lane (GetStimulus/sri STI-2767): a change that restores
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

echo "L3 rate gate — the declared-correction lane (GetStimulus/sri STI-2767)"

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

# L2 — a bool must be REJECTED, leaving the default.
L2="$(fresh_ws)"
got="$(budget_json "$L2" true)"
if [ "$got" = '"correction_budget": 3' ]; then
  echo "  [pass] L2: a boolean correction_budget is rejected for the default"; pass=$((pass + 1))
else
  echo "  [FAIL] L2: a boolean correction_budget is rejected for the default — got: $got"; fail=$((fail + 1))
fi

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

echo "Passed: $pass  Failed: $fail"
[ "$fail" -eq 0 ] && echo "All tests passed." || exit 1
