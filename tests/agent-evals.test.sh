#!/usr/bin/env bash
# tests/agent-evals.test.sh — BRO-2542. Runs the agent_evals contract suite under
# bstack CI, which discovers tests/*.test.sh.
#
# Stdlib unittest only, so there is nothing to install and deliberately NO skip path:
# a wrapper that exits 0 around a suite that never ran is the failure mode this repo
# has hit before (the check exists, the check passes, the check is inert).
#
# Guards beyond "unittest exited 0":
#   - the final status line must be exactly "OK". `OK (skipped=1)` or
#     `OK (expected failures=1)` FAILS the run: a test that silently stopped
#     asserting still reports green.
#   - a floor on "Ran N tests". unittest exits 0 on a module that collects nothing
#     useful, so the exit code alone cannot tell "56 ran" from "3 ran".
#   - the CLI must run as a subprocess, and the shipped example eval must both
#     validate and PROVE (its checks fail on a no-op, pass on its reference, and fail
#     on each of its named violations) in a throwaway repo. The suite imports the module; consumers exec it.
#
# No test calls a model: `claude` is a fake executable written into a temp dir.
#
# Run from the bstack repo root:
#   bash tests/agent-evals.test.sh

set -uo pipefail

BSTACK_REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MODULE="tests.test_agent_evals"
SCRIPT="$BSTACK_REPO/scripts/agent_evals.py"
EXAMPLE="$BSTACK_REPO/references/templates/eval.example.json"
MIN_TESTS=89

PASS=0
FAIL=0
FAILED_TESTS=()
assert_pass() { PASS=$((PASS + 1)); echo "  [pass] $1"; }
assert_fail() { FAIL=$((FAIL + 1)); FAILED_TESTS+=("$1"); echo "  [FAIL] $1"; [ -n "${2:-}" ] && echo "         $2"; }

echo "── agent-evals contract suite (BRO-2542) ──────────────────────────"

[ -f "$BSTACK_REPO/tests/test_agent_evals.py" ] || { echo "  [FAIL] missing tests/test_agent_evals.py"; exit 1; }
[ -f "$SCRIPT" ]  || { echo "  [FAIL] missing $SCRIPT"; exit 1; }
[ -f "$EXAMPLE" ] || { echo "  [FAIL] missing $EXAMPLE"; exit 1; }
command -v git >/dev/null 2>&1 || { echo "  [FAIL] git not on PATH — not skipping"; exit 1; }

LOG="$(mktemp)"; SCRATCH="$(mktemp -d)"
trap 'rm -rf "$LOG" "$SCRATCH"' EXIT

(cd "$BSTACK_REPO" && python3 -m unittest -v "$MODULE") >"$LOG" 2>&1
RC=$?

if [ "$RC" -eq 0 ]; then
    assert_pass "unittest suite exits clean"
else
    assert_fail "unittest suite exits clean" "$(tail -15 "$LOG")"
fi

STATUS_LINE="$(grep -E '^(OK|FAILED)' "$LOG" | tail -1)"
if [ "$STATUS_LINE" = "OK" ]; then
    assert_pass "final status is exactly OK (no skips, no expected failures)"
else
    assert_fail "final status is exactly OK (no skips, no expected failures)" "got: ${STATUS_LINE:-<none>}"
fi

if grep -qE '(\.\.\. skipped|expected failure|unexpected success)' "$LOG"; then
    assert_fail "no test silently stopped asserting" "$(grep -E '(\.\.\. skipped|expected failure|unexpected success)' "$LOG" | head -3)"
else
    assert_pass "no test silently stopped asserting"
fi

COUNT="$(grep -oE '^Ran [0-9]+ tests?' "$LOG" | tail -1 | grep -oE '[0-9]+')"
COUNT="${COUNT:-0}"
if [ "$COUNT" -ge "$MIN_TESTS" ]; then
    assert_pass "suite size >= $MIN_TESTS (ran $COUNT)"
else
    assert_fail "suite size >= $MIN_TESTS" "only $COUNT ran — collection may have silently shrunk"
fi

if python3 "$SCRIPT" --help >/dev/null 2>&1; then
    assert_pass "agent_evals.py --help runs as a subprocess"
else
    assert_fail "agent_evals.py --help runs as a subprocess"
fi

# The shipped example must prove in a throwaway repo: a template whose checks cannot
# fail would teach every adopter to write checks that cannot fail.
G=(git -c core.fsmonitor=false -c core.hooksPath=/dev/null -c user.name=t -c user.email=t@example.invalid)
if "${G[@]}" init -q -b main "$SCRATCH/repo" \
   && mkdir -p "$SCRATCH/repo/.control" && echo "gates: []" >"$SCRATCH/repo/.control/policy.yaml" \
   && "${G[@]}" -C "$SCRATCH/repo" add -A && "${G[@]}" -C "$SCRATCH/repo" commit -q -m init; then
    if OUT="$(python3 "$SCRIPT" validate "$EXAMPLE" --prove --require-reference --require-violations --repo "$SCRATCH/repo" 2>&1)"; then
        assert_pass "shipped eval.example.json proves (reference + every violation caught)"
    else
        assert_fail "shipped eval.example.json proves (reference + every violation caught)" "$(echo "$OUT" | tail -5)"
    fi
    if [ "$("${G[@]}" -C "$SCRATCH/repo" worktree list | wc -l | tr -d ' ')" = "1" ]; then
        assert_pass "--prove left no scratch worktree behind"
    else
        assert_fail "--prove left no scratch worktree behind" "$("${G[@]}" -C "$SCRATCH/repo" worktree list)"
    fi
else
    assert_fail "could build a throwaway repo for --prove"
fi

echo
echo "── unittest tail ──────────────────────────────────────────────────"
tail -4 "$LOG"
echo
echo "── Summary ────────────────────────────────────────────────────────"
echo "  passed: $PASS"
echo "  failed: $FAIL"
if [ "$FAIL" -gt 0 ]; then
    echo "  failed tests:"
    for t in "${FAILED_TESTS[@]}"; do
        echo "    - $t"
    done
    exit 1
fi
exit 0
