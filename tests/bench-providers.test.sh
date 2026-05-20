#!/usr/bin/env bash
# tests/bench-providers.test.sh — Provider abstraction tests (v0.11.0).
#
# Validates the LLM provider abstraction shipped in BRO-1211:
#
#   1. Unknown provider name exits 2 with clear message
#   2. `--runner live --provider` without --model exits 2
#   3. `--provider mock --model mock-small` runs end-to-end (offline)
#   4. mock provider populates real token usage from response.usage
#   5. P20 violation: same model for agent + judge exits 8
#   6. P20 override with --allow-same-judge-model captures rationale in config
#   7. P20 distinct models proceeds (no violation)
#   8. databricks provider without DATABRICKS_TOKEN exits 9 (ProviderNotConfigured)
#   9. databricks provider name resolves (lazy import — no openai needed for resolution)
#  10. providers/list_providers includes mock + databricks
#
# Runs offline. Live integration test lives in tests/bench-live.test.sh
# and is gated by BSTACK_BENCH_LIVE=1.

set -uo pipefail

BSTACK_REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BSTACK_BENCH="$BSTACK_REPO/bin/bstack-bench"

BSTACK_BENCH_HOME="$(mktemp -d -t bstack-bench-providers.XXXXXX)"
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

# Pick a python3.10+ interpreter the same way bstack-bench does.
pick_py() {
    for cand in python3.13 python3.12 python3.11 python3.10; do
        if command -v "$cand" >/dev/null 2>&1; then
            local v
            v="$("$cand" -c 'import sys; print(sys.version_info[:2] >= (3,10))' 2>/dev/null || echo False)"
            [ "$v" = "True" ] && { echo "$cand"; return 0; }
        fi
    done
    for probe in /opt/homebrew/bin/python3.13 /opt/homebrew/bin/python3.12 \
                 /opt/homebrew/bin/python3.11 /opt/homebrew/bin/python3.10; do
        [ -x "$probe" ] && { echo "$probe"; return 0; }
    done
    return 1
}
PY="$(pick_py || true)"
if [ -z "$PY" ]; then
    echo "bench-providers.test.sh: needs Python 3.10+; skipping."
    exit 0
fi

# ── 1. unknown provider name → rc=2 ──────────────────────────────────────
set +e
out="$("$BSTACK_BENCH" run --tasks bstack-smoke --runner live \
    --provider nonexistent-provider --model whatever --phase 1 --no-dry-run 2>&1)"
rc=$?
set -e
if [ "$rc" = "2" ] && echo "$out" | grep -qi "Unknown provider"; then
    assert_pass "unknown provider name fails with rc=2 + 'Unknown provider'"
else
    assert_fail "unknown provider should rc=2 with clear message (got rc=$rc)" "$out"
fi

# ── 2. --runner live without --model → rc=2 ──────────────────────────────
set +e
out="$("$BSTACK_BENCH" run --tasks bstack-smoke --runner live \
    --provider mock --phase 1 --no-dry-run 2>&1)"
rc=$?
set -e
if [ "$rc" = "2" ] && echo "$out" | grep -qi "model.*required\|--model"; then
    assert_pass "--runner live without --model fails fast (rc=2)"
else
    assert_fail "live without --model should rc=2 (got rc=$rc)" "$out"
fi

# ── 3. mock provider end-to-end ──────────────────────────────────────────
RUN_HOME="$(mktemp -d -t bstack-bench-mock.XXXXXX)"
set +e
out="$(BSTACK_BENCH_HOME="$RUN_HOME" "$BSTACK_BENCH" \
    run --tasks bstack-smoke --runner live \
    --provider mock --model mock-small \
    --phase 1 --no-dry-run 2>&1)"
rc=$?
set -e
if [ "$rc" = "0" ] || [ "$rc" = "6" ]; then
    # rc=0 if some tasks pass the rubric, rc=6 if mock content fails rubric
    # entirely. Either is fine — the substrate ran. Verify results.jsonl.
    run_dir="$(ls -1 "$RUN_HOME/runs" 2>/dev/null | head -1)"
    if [ -n "$run_dir" ] && [ -f "$RUN_HOME/runs/$run_dir/phase1_results.jsonl" ]; then
        # Confirm tokens came from the mock provider's canned values.
        mock_tokens="$("$PY" -c "
import json
with open('$RUN_HOME/runs/$run_dir/phase1_results.jsonl') as f:
    for line in f:
        obj = json.loads(line)
        print(obj['tokens']['total_tokens'])
        break
")"
        if [ "$mock_tokens" = "20" ]; then
            assert_pass "mock provider end-to-end produces results with provider-reported tokens (20 = 12 prompt + 8 completion)"
        else
            assert_fail "mock provider tokens should be 20 (canned), got $mock_tokens"
        fi
    else
        assert_fail "mock provider run produced no phase1_results.jsonl"
    fi
else
    assert_fail "mock provider should rc=0 or 6 (got rc=$rc)" "$out"
fi
rm -rf "$RUN_HOME"

# ── 4. P20 violation: same model for agent + judge → rc=8 ────────────────
set +e
out="$("$BSTACK_BENCH" run --tasks bstack-smoke --runner live --evaluator llm-judge \
    --provider mock --model mock-small --judge-model mock-small \
    --phase 1 --no-dry-run 2>&1)"
rc=$?
set -e
if [ "$rc" = "8" ] && echo "$out" | grep -qi "P20.*violation\|judge model.*equals agent model"; then
    assert_pass "P20 violation (same model agent+judge) fails fast with rc=8"
else
    assert_fail "P20 same-model should rc=8 (got rc=$rc)" "$out"
fi

# ── 5. P20 override with rationale → rationale captured in config ────────
RUN_HOME="$(mktemp -d -t bstack-bench-p20override.XXXXXX)"
set +e
out="$(BSTACK_BENCH_HOME="$RUN_HOME" "$BSTACK_BENCH" \
    run --tasks bstack-smoke --runner live --evaluator llm-judge \
    --provider mock --model mock-small --judge-model mock-small \
    --allow-same-judge-model "smoke test only" \
    --phase 1 --no-dry-run 2>&1)"
rc=$?
set -e
# rc can be 0 or 6 — what we care about is the rationale was captured.
run_dir="$(ls -1 "$RUN_HOME/runs" 2>/dev/null | head -1)"
if [ -n "$run_dir" ]; then
    rationale="$("$PY" -c "
import json
d = json.load(open('$RUN_HOME/runs/$run_dir/config.json'))
print(d.get('allow_same_judge_model_rationale', ''))
")"
    if [ "$rationale" = "smoke test only" ]; then
        assert_pass "P20 override rationale captured in config.json"
    else
        assert_fail "rationale should be 'smoke test only' (got '$rationale')" "$out"
    fi
else
    assert_fail "P20 override should create run dir even on failure"
fi
rm -rf "$RUN_HOME"

# ── 6. P20 distinct models → no violation ────────────────────────────────
RUN_HOME="$(mktemp -d -t bstack-bench-p20ok.XXXXXX)"
set +e
out="$(BSTACK_BENCH_HOME="$RUN_HOME" "$BSTACK_BENCH" \
    run --tasks bstack-smoke --runner live --evaluator llm-judge \
    --provider mock --model mock-small --judge-model mock-large \
    --phase 1 --no-dry-run 2>&1)"
rc=$?
set -e
# Mock provider returns a non-JSON canned string, so the judge parse will
# fail and tasks will fail. We just verify no P20 violation fired.
if [ "$rc" != "8" ] && ! echo "$out" | grep -qi "P20.*violation"; then
    assert_pass "P20 distinct models proceeds (no violation)"
else
    assert_fail "P20 distinct models should NOT trigger violation (got rc=$rc)" "$out"
fi
rm -rf "$RUN_HOME"

# ── 7. databricks without DATABRICKS_TOKEN → rc=9 ────────────────────────
set +e
NOENV_OUT="$(env -i \
    HOME="$HOME" PATH="$PATH" BSTACK_BENCH_HOME="$BSTACK_BENCH_HOME" \
    "$BSTACK_BENCH" run --tasks bstack-smoke --runner live \
    --provider databricks --model databricks-claude-haiku-4-5 \
    --phase 1 --no-dry-run 2>&1)"
NOENV_RC=$?
set -e
if [ "$NOENV_RC" = "9" ] && echo "$NOENV_OUT" | grep -qi "DATABRICKS_HOST\|DATABRICKS_TOKEN\|not configured"; then
    assert_pass "databricks provider without env vars fails with rc=9 (not configured)"
else
    assert_fail "databricks without env should rc=9 (got rc=$NOENV_RC)" "$NOENV_OUT"
fi

# ── 8. list_providers() includes mock + databricks ───────────────────────
list_out="$("$PY" -c "
import sys
sys.path.insert(0, '$BSTACK_REPO/scripts')
from bench.providers import list_providers
print(','.join(list_providers()))
" 2>&1)"
if echo "$list_out" | grep -q "databricks" && echo "$list_out" | grep -q "mock"; then
    assert_pass "list_providers() returns databricks + mock"
else
    assert_fail "list_providers() should include databricks + mock (got: $list_out)"
fi

# ── 9. provider standards reference doc exists ───────────────────────────
if [ -f "$BSTACK_REPO/references/provider-standards.md" ]; then
    if grep -q "OpenAI Chat Completions API" "$BSTACK_REPO/references/provider-standards.md" \
        && grep -q "DatabricksGatewayProvider\|databricks" "$BSTACK_REPO/references/provider-standards.md" \
        && grep -q "railway run" "$BSTACK_REPO/references/provider-standards.md"; then
        assert_pass "references/provider-standards.md exists with required sections"
    else
        assert_fail "provider-standards.md missing required sections (OpenAI contract / databricks / railway)"
    fi
else
    assert_fail "references/provider-standards.md missing"
fi

# ── 10. Provider error types are exposed at package level ────────────────
type_check="$("$PY" -c "
import sys
sys.path.insert(0, '$BSTACK_REPO/scripts')
from bench.providers import (
    Provider, ChatMessage, ChatCompletion, Usage,
    ProviderError, ProviderNotConfigured, ProviderNotInstalled,
    get_provider, list_providers, register_provider,
)
print('ALL_GOOD')
" 2>&1)"
if echo "$type_check" | grep -q "ALL_GOOD"; then
    assert_pass "providers public API exposes all required symbols"
else
    assert_fail "providers package missing public symbols" "$type_check"
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
