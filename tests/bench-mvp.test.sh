#!/usr/bin/env bash
# tests/bench-mvp.test.sh — Phase MVP (v0.10.0) bench harness smoke.
#
# Validates the dry-run two-phase protocol end-to-end:
#   1. `bstack bench --help` exits 0 and mentions the four subcommands
#   2. `bstack bench tasks list` surfaces bstack-smoke (3 tasks)
#   3. Unknown task set exits 3
#   4. Unknown subcommand exits 2
#   5. `bstack bench run --phase both --dry-run` produces phase1+phase2 results
#   6. Each results.jsonl line parses as JSON with the expected schema keys
#   7. comparison.json + REPORT.md exist after a `both` run
#   8. Phase 2 token count is lower than Phase 1 (dry-run canned delta)
#   9. Phase 2 mean quality is >= Phase 1 mean quality (dry-run canned delta)
#  10. `bstack bench compare` runs without args against the latest run
#  11. `bstack bench status` lists the recently-created run
#  12. `--budget-usd 0.0001` halts the run early with exit 4
#  13. The `live` runner stub raises a clear NotImplementedError
#  14. Snapshot tarball is created between phases

set -uo pipefail

BSTACK_REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BSTACK_BENCH="$BSTACK_REPO/bin/bstack-bench"

# Isolate state in a tempdir so the test never touches the user's real config.
BSTACK_BENCH_HOME="$(mktemp -d -t bstack-bench-test.XXXXXX)"
export BSTACK_BENCH_HOME

cleanup() {
    rm -rf "$BSTACK_BENCH_HOME"
}
trap cleanup EXIT

PASS=0
FAIL=0
FAILED_TESTS=()

assert_pass() { PASS=$((PASS + 1)); echo "  ✓ $1"; }
assert_fail() {
    FAIL=$((FAIL + 1))
    FAILED_TESTS+=("$1")
    echo "  ✗ $1"
    [ -n "${2:-}" ] && echo "    ${2}"
}

# ── 1. --help ────────────────────────────────────────────────────────────
help_out="$("$BSTACK_BENCH" --help 2>&1)"
if echo "$help_out" | grep -q "bstack bench run" \
    && echo "$help_out" | grep -q "compare" \
    && echo "$help_out" | grep -q "tasks list" \
    && echo "$help_out" | grep -q "status"; then
    assert_pass "--help lists all four subcommands"
else
    assert_fail "--help missing subcommands" "$help_out"
fi

# ── 2. tasks list ────────────────────────────────────────────────────────
tasks_out="$("$BSTACK_BENCH" tasks list 2>&1)"
if echo "$tasks_out" | grep -q "bstack-smoke" \
    && echo "$tasks_out" | grep -q "(3 tasks)"; then
    assert_pass "tasks list surfaces bstack-smoke with 3 tasks"
else
    assert_fail "tasks list missing bstack-smoke" "$tasks_out"
fi

# ── 3. unknown task set → exit 3 ─────────────────────────────────────────
set +e
unknown_out="$("$BSTACK_BENCH" run --tasks nonexistent-set --phase 1 --dry-run 2>&1)"
unknown_rc=$?
set -e
if [ "$unknown_rc" = "3" ]; then
    assert_pass "unknown task set exits 3"
else
    assert_fail "unknown task set should exit 3 (got $unknown_rc)" "$unknown_out"
fi

# ── 4. unknown subcommand → exit 2 ───────────────────────────────────────
set +e
sub_out="$("$BSTACK_BENCH" wat 2>&1)"
sub_rc=$?
set -e
if [ "$sub_rc" = "2" ]; then
    assert_pass "unknown subcommand exits 2"
else
    assert_fail "unknown subcommand should exit 2 (got $sub_rc)" "$sub_out"
fi

# ── 5. happy-path: run --phase both --dry-run ────────────────────────────
set +e
run_out="$("$BSTACK_BENCH" run --tasks bstack-smoke --phase both --dry-run 2>&1)"
run_rc=$?
set -e
if [ "$run_rc" = "0" ]; then
    assert_pass "run --phase both --dry-run exits 0"
else
    assert_fail "run --phase both --dry-run failed (rc=$run_rc)" "$run_out"
fi

# Locate the run directory created above.
runs_root="$BSTACK_BENCH_HOME/runs"
run_dir="$(ls -1 "$runs_root" 2>/dev/null | head -1)"
if [ -z "$run_dir" ]; then
    assert_fail "no run directory created in $runs_root"
    echo ""
    echo "=== Summary ==="
    echo "  Passed: $PASS"
    echo "  Failed: $FAIL"
    exit 1
fi
RUN_DIR="$runs_root/$run_dir"

# ── 6. JSONL shape ───────────────────────────────────────────────────────
ok=1
for phase in 1 2; do
    results="$RUN_DIR/phase${phase}_results.jsonl"
    if [ ! -f "$results" ]; then ok=0; break; fi
    # Every line parses, contains task_id + phase + tokens + evaluation.
    while IFS= read -r line; do
        [ -z "$line" ] && continue
        echo "$line" | python3 -c "
import json, sys
o = json.loads(sys.stdin.read())
required = {'task_id', 'phase', 'tokens', 'evaluation', 'skills'}
missing = required - set(o.keys())
if missing:
    sys.exit('missing keys: ' + ','.join(sorted(missing)))
" >/dev/null 2>&1 || { ok=0; break 2; }
    done < "$results"
done
if [ "$ok" = "1" ]; then
    assert_pass "phase results JSONL has required schema (task_id, phase, tokens, evaluation, skills)"
else
    assert_fail "results JSONL schema invalid in $RUN_DIR"
fi

# ── 7. comparison.json + REPORT.md ───────────────────────────────────────
if [ -f "$RUN_DIR/comparison.json" ] && [ -f "$RUN_DIR/REPORT.md" ]; then
    assert_pass "comparison.json + REPORT.md exist after both phases"
else
    assert_fail "missing comparison artifacts" "$(ls "$RUN_DIR" 2>&1)"
fi

# ── 8. Phase 2 tokens < Phase 1 tokens (canned dry-run delta) ────────────
ratio="$(python3 -c "
import json
d = json.load(open('$RUN_DIR/comparison.json'))
print(d.get('phase2_tokens_over_phase1'))
" 2>/dev/null)"
if python3 -c "import sys; sys.exit(0 if float('$ratio') < 1.0 else 1)" 2>/dev/null; then
    assert_pass "Phase 2 token ratio ($ratio) < 1.0 — comparison detects token delta"
else
    assert_fail "expected Phase 2 token ratio < 1.0, got '$ratio'"
fi

# ── 9. Phase 2 quality >= Phase 1 quality ────────────────────────────────
qual_delta="$(python3 -c "
import json
d = json.load(open('$RUN_DIR/comparison.json'))
print(d.get('phase2_quality_minus_phase1'))
" 2>/dev/null)"
if python3 -c "import sys; sys.exit(0 if float('$qual_delta') >= 0 else 1)" 2>/dev/null; then
    assert_pass "Phase 2 quality delta ($qual_delta) >= 0 — comparison detects quality direction"
else
    assert_fail "expected Phase 2 quality delta >= 0, got '$qual_delta'"
fi

# ── 10. compare without args → uses latest run ───────────────────────────
set +e
cmp_out="$("$BSTACK_BENCH" compare 2>&1)"
cmp_rc=$?
set -e
if [ "$cmp_rc" = "0" ] && echo "$cmp_out" | grep -q "REPORT.md"; then
    assert_pass "compare without --run-id picks latest"
else
    assert_fail "compare (latest) failed (rc=$cmp_rc)" "$cmp_out"
fi

# ── 11. status lists the run ─────────────────────────────────────────────
status_out="$("$BSTACK_BENCH" status 2>&1)"
if echo "$status_out" | grep -q "$run_dir"; then
    assert_pass "status lists the recent run"
else
    assert_fail "status did not surface run $run_dir" "$status_out"
fi

# ── 12. budget-usd 0.0001 halts the run ──────────────────────────────────
# Fresh BSTACK_BENCH_HOME for this test so the existing run doesn't satisfy.
BUDGET_HOME="$(mktemp -d -t bstack-bench-budget.XXXXXX)"
set +e
budget_out="$(BSTACK_BENCH_HOME="$BUDGET_HOME" "$BSTACK_BENCH" \
    run --tasks bstack-smoke --phase both --dry-run --budget-usd 0.0001 2>&1)"
budget_rc=$?
set -e
rm -rf "$BUDGET_HOME"
if [ "$budget_rc" = "4" ]; then
    assert_pass "budget cap halts run with exit 4"
else
    assert_fail "budget cap should halt with rc=4, got rc=$budget_rc" "$budget_out"
fi

# ── 13. live runner is a clear stub ──────────────────────────────────────
LIVE_HOME="$(mktemp -d -t bstack-bench-live.XXXXXX)"
set +e
live_out="$(BSTACK_BENCH_HOME="$LIVE_HOME" "$BSTACK_BENCH" \
    run --tasks bstack-smoke --runner live --phase 1 --no-dry-run 2>&1)"
live_rc=$?
set -e
rm -rf "$LIVE_HOME"
if [ "$live_rc" != "0" ] && echo "$live_out" | grep -qi "not.*wired\|NotImplementedError\|stub\|live mode"; then
    assert_pass "live runner stub surfaces a clear migration message"
else
    assert_fail "live runner stub should fail with a clear message" "$live_out"
fi

# ── 14. snapshot tarball exists ──────────────────────────────────────────
snap="$RUN_DIR/phase1_skills_snapshot.tar.gz"
if [ -f "$snap" ]; then
    assert_pass "Phase 1 → Phase 2 snapshot tarball created"
else
    assert_fail "snapshot tarball missing at $snap"
fi

# ── Summary ──────────────────────────────────────────────────────────────
echo ""
echo "=== Summary ==="
echo "  Passed: $PASS"
echo "  Failed: $FAIL"
if [ "$FAIL" -gt 0 ]; then
    echo ""
    echo "Failed tests:"
    for t in "${FAILED_TESTS[@]}"; do
        echo "  - $t"
    done
    exit 1
fi
exit 0
