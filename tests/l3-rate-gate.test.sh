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

echo "─────────────────────────────────────"
echo "Passed: $pass  Failed: $fail"
[ "$fail" -eq 0 ] && echo "All tests passed." || exit 1
