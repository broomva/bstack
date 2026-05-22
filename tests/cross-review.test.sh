#!/usr/bin/env bash
# tests/cross-review.test.sh — Smoke tests for bstack cross-review (BRO-1227).
#
# Tests run offline (no `gh` network calls) — they exercise the CLI's
# local logic only. End-to-end network-bound validation happens via the
# manual smoke documented in the PR body.
#
# Run from the bstack repo root:
#   bash tests/cross-review.test.sh

set -uo pipefail

BSTACK_REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BSTACK_BIN="$BSTACK_REPO/bin/bstack"
CROSS_REVIEW_PY="$BSTACK_REPO/scripts/cross-review.py"

PASS=0
FAIL=0
FAILED_TESTS=()

assert_pass() { PASS=$((PASS + 1)); echo "  [pass] $1"; }
assert_fail() { FAIL=$((FAIL + 1)); FAILED_TESTS+=("$1"); echo "  [FAIL] $1"; [ -n "${2:-}" ] && echo "         $2"; }

echo "── cross-review CLI smoke tests ───────────────────────────────────"

# T1: dispatcher routes cross-review to the sub-binary
test1_name="bstack --help advertises cross-review"
if "$BSTACK_BIN" --help 2>&1 | grep -q 'cross-review <pr-num>'; then
    assert_pass "$test1_name"
else
    assert_fail "$test1_name"
fi

# T2: dispatcher invokes the Python script
test2_name="bstack cross-review --help routes to argparse"
if "$BSTACK_BIN" cross-review --help 2>&1 | grep -q 'P20 cross-model adversarial review'; then
    assert_pass "$test2_name"
else
    assert_fail "$test2_name"
fi

# T3: Python script standalone --help works
test3_name="cross-review.py --help works directly"
if python3 "$CROSS_REVIEW_PY" --help 2>&1 | grep -q '\-\-repo'; then
    assert_pass "$test3_name"
else
    assert_fail "$test3_name"
fi

# T4: missing required --repo is rejected
test4_name="argparse rejects missing --repo"
test4_out="$(python3 "$CROSS_REVIEW_PY" 123 2>&1 || true)"
if echo "$test4_out" | grep -qE 'the following arguments are required|required.*repo'; then
    assert_pass "$test4_name"
else
    assert_fail "$test4_name" "$test4_out"
fi

# T5: non-numeric PR rejected
test5_name="argparse rejects non-numeric PR"
out="$(python3 "$CROSS_REVIEW_PY" abc --repo foo/bar 2>&1 || true)"
if echo "$out" | grep -qi "invalid int\|argument pr"; then
    assert_pass "$test5_name"
else
    assert_fail "$test5_name" "$out"
fi

# T6: Python syntax + module-level import works
test6_name="cross-review.py imports cleanly"
if python3 -c "
import sys, importlib.util
spec = importlib.util.spec_from_file_location('cross_review', '$CROSS_REVIEW_PY')
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
assert hasattr(mod, 'main')
assert hasattr(mod, 'try_parse_json')
assert hasattr(mod, 'build_codex_prompt')
" 2>&1; then
    assert_pass "$test6_name"
else
    assert_fail "$test6_name"
fi

# T7: try_parse_json extracts JSON from prose
test7_name="try_parse_json recovers from codex-style prose-wrapped JSON"
if python3 -c "
import importlib.util
spec = importlib.util.spec_from_file_location('cross_review', '$CROSS_REVIEW_PY')
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
result = mod.try_parse_json('thinking… here it is:\n{\"verdict\": \"pass\", \"score\": 8}\n— done')
assert result == {'verdict': 'pass', 'score': 8}, result
result2 = mod.try_parse_json('{\"verdict\": \"fail\"}')
assert result2 == {'verdict': 'fail'}
result3 = mod.try_parse_json('no json here')
assert result3 is None
" 2>&1; then
    assert_pass "$test7_name"
else
    assert_fail "$test7_name"
fi

# T8: exit_code_for_verdict maps verdicts correctly
test8_name="exit_code_for_verdict mapping"
if python3 -c "
import importlib.util
spec = importlib.util.spec_from_file_location('cross_review', '$CROSS_REVIEW_PY')
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
assert mod.exit_code_for_verdict('pass') == 0
assert mod.exit_code_for_verdict('concerns') == 10
assert mod.exit_code_for_verdict('fail') == 20
assert mod.exit_code_for_verdict('skipped') == 30
assert mod.exit_code_for_verdict('garbage') == 30
" 2>&1; then
    assert_pass "$test8_name"
else
    assert_fail "$test8_name"
fi

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
