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

# ═══════════════════════════════════════════════════════════════════════
#  P20 round-1 adversarial tests — one per defect, written to FALSIFY.
#  Each assertion targets a specific code path the canned smoke can't
#  reach. Author intent: prove the fix works AND that the bug existed.
# ═══════════════════════════════════════════════════════════════════════

# ── 15. evaluator stub fails cleanly (defect #1) ─────────────────────────
# StubLLMJudgeEvaluator must surface a clear migration message via stderr,
# not a raw Python traceback. Prior to P20 round-1, `_run_task` caught
# NotImplementedError from the runner but not the evaluator.
LLM_HOME="$(mktemp -d -t bstack-bench-llm.XXXXXX)"
set +e
llm_out="$(BSTACK_BENCH_HOME="$LLM_HOME" "$BSTACK_BENCH" \
    run --tasks bstack-smoke --runner dry-run --evaluator llm --phase 1 2>&1)"
llm_rc=$?
set -e
rm -rf "$LLM_HOME"
# Must NOT be a raw traceback (rc != 1), must contain a clear message, must
# end as a clean structural failure (rc=6 — all tasks failed via stub).
if [ "$llm_rc" = "6" ] \
    && echo "$llm_out" | grep -qi "evaluator.*not.*wired\|LLM judge\|NotImplementedError" \
    && ! echo "$llm_out" | grep -q "Traceback (most recent call last)"; then
    assert_pass "evaluator llm-judge stub surfaces clean migration message (no traceback)"
else
    assert_fail "evaluator llm-judge stub should fail cleanly (rc=6, no traceback), got rc=$llm_rc" "$llm_out"
fi

# ── 16. budget-on-resume counts prior cost (defect #2) ───────────────────
# Pre-populate phase1_results.jsonl with a row that has cost_usd=0.10.
# Then --resume with --budget-usd 0.05 must refuse to start (exit 4).
RESUME_HOME="$(mktemp -d -t bstack-bench-resume.XXXXXX)"
RESUME_RUN_ID="20260101T000000-budgettest"
RESUME_DIR="$RESUME_HOME/runs/$RESUME_RUN_ID"
mkdir -p "$RESUME_DIR"
# Minimal config + a phase 1 results file with prior cost.
cat > "$RESUME_DIR/config.json" <<EOF
{"run_id": "$RESUME_RUN_ID", "tasks_set": "bstack-smoke", "runner": "dry-run", "phase": "both"}
EOF
cat > "$RESUME_DIR/phase1_results.jsonl" <<'EOF'
{"task_id":"ticket-triage-001","phase":1,"runner":"dry-run","tokens":{"cost_usd":0.10,"total_tokens":1000,"llm_calls":1},"evaluation":{"quality_score":1.0,"payment_usd":5.0,"actual_payment_usd":5.0},"exit_status":"success"}
EOF
set +e
resume_out="$(BSTACK_BENCH_HOME="$RESUME_HOME" "$BSTACK_BENCH" \
    run --tasks bstack-smoke --phase both --dry-run \
    --resume "$RESUME_RUN_ID" --budget-usd 0.05 2>&1)"
resume_rc=$?
set -e
rm -rf "$RESUME_HOME"
if [ "$resume_rc" = "4" ] && echo "$resume_out" | grep -qi "prior.*exceeds\|already exceeds budget"; then
    assert_pass "budget-on-resume counts prior cost (refuses fresh run when budget already exceeded)"
else
    assert_fail "budget-on-resume should refuse with rc=4 (got rc=$resume_rc)" "$resume_out"
fi

# ── 17. aggregate dedupes duplicate task_id rows (defect #3) ─────────────
# Inject a phase1 results file with two rows for the same task_id (a
# failure followed by a success — the canonical resume-recovery shape).
# `compare` must report task_count=1, not 2; total_tokens must come from
# the success row (last-write-wins), not the sum.
DEDUPE_HOME="$(mktemp -d -t bstack-bench-dedupe.XXXXXX)"
DEDUPE_RUN_ID="20260101T000000-dedupetest"
DEDUPE_DIR="$DEDUPE_HOME/runs/$DEDUPE_RUN_ID"
mkdir -p "$DEDUPE_DIR"
cat > "$DEDUPE_DIR/config.json" <<EOF
{"run_id": "$DEDUPE_RUN_ID", "tasks_set": "bstack-smoke", "runner": "dry-run"}
EOF
# Failure row + success row, same task_id. Aggregate must collapse to 1.
cat > "$DEDUPE_DIR/phase1_results.jsonl" <<'EOF'
{"task_id":"ticket-triage-001","phase":1,"runner":"dry-run","tokens":{"cost_usd":0.0,"total_tokens":0,"llm_calls":0},"exit_status":"failure","error":"first attempt crashed"}
{"task_id":"ticket-triage-001","phase":1,"runner":"dry-run","tokens":{"cost_usd":0.01,"total_tokens":500,"llm_calls":1},"evaluation":{"quality_score":1.0,"payment_usd":5.0,"actual_payment_usd":5.0},"exit_status":"success"}
EOF
# Minimal phase2 so compare runs (defect #4 fix means compare needs both).
cat > "$DEDUPE_DIR/phase2_results.jsonl" <<'EOF'
{"task_id":"ticket-triage-001","phase":2,"runner":"dry-run","tokens":{"cost_usd":0.005,"total_tokens":250,"llm_calls":1},"evaluation":{"quality_score":1.0,"payment_usd":5.0,"actual_payment_usd":5.0},"exit_status":"success"}
EOF
set +e
dedupe_out="$(BSTACK_BENCH_HOME="$DEDUPE_HOME" "$BSTACK_BENCH" compare --run-id "$DEDUPE_RUN_ID" 2>&1)"
dedupe_rc=$?
set -e
if [ "$dedupe_rc" = "0" ]; then
    p1_count="$(python3 -c "
import json
d = json.load(open('$DEDUPE_DIR/comparison.json'))
print(d['phase1']['task_count'])
" 2>/dev/null)"
    p1_tokens="$(python3 -c "
import json
d = json.load(open('$DEDUPE_DIR/comparison.json'))
print(d['phase1']['total_tokens'])
" 2>/dev/null)"
    if [ "$p1_count" = "1" ] && [ "$p1_tokens" = "500" ]; then
        assert_pass "aggregate dedupes by task_id (1 task, 500 tokens — success row wins, failure row collapsed)"
    else
        assert_fail "aggregate should report task_count=1 + tokens=500 (got count=$p1_count tokens=$p1_tokens)" "$dedupe_out"
    fi
else
    assert_fail "compare should succeed with 1+1 rows (got rc=$dedupe_rc)" "$dedupe_out"
fi
rm -rf "$DEDUPE_HOME"

# ── 18. compare refuses missing phase (defect #4) ────────────────────────
# Phase 1 has 3 results, phase 2 doesn't exist. compare must NOT silently
# emit a "Phase 2 = 0 tokens / Δquality = -X" report.
MISSING_HOME="$(mktemp -d -t bstack-bench-missing.XXXXXX)"
set +e
"$BSTACK_BENCH" --help >/dev/null 2>&1
# Run phase 1 only, then attempt compare.
BSTACK_BENCH_HOME="$MISSING_HOME" "$BSTACK_BENCH" \
    run --tasks bstack-smoke --phase 1 --dry-run >/dev/null 2>&1
miss_out="$(BSTACK_BENCH_HOME="$MISSING_HOME" "$BSTACK_BENCH" compare 2>&1)"
miss_rc=$?
set -e
rm -rf "$MISSING_HOME"
if [ "$miss_rc" = "7" ] && echo "$miss_out" | grep -qi "requires both phases"; then
    assert_pass "compare refuses missing phase with rc=7 + clear message"
else
    assert_fail "compare on phase-1-only run should exit 7 with clear message (got rc=$miss_rc)" "$miss_out"
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
