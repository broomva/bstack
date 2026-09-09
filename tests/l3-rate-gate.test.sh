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

echo "─────────────────────────────────────"
echo "Passed: $pass  Failed: $fail"
[ "$fail" -eq 0 ] && echo "All tests passed." || exit 1
