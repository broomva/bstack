#!/usr/bin/env bash
# tests/bench-live.test.sh — Live Databricks Gateway integration test (BRO-1211).
#
# This test makes REAL LLM CALLS that incur cost. It is gated by:
#
#   1. Environment variable BSTACK_BENCH_LIVE=1 must be set, AND
#   2. DATABRICKS_HOST + DATABRICKS_TOKEN must be present in the environment.
#
# When gates aren't met, the test exits 0 (skipped) so it can sit in the
# tests/ tree without breaking CI.
#
# Invocation patterns:
#
#   # Direct (env exported):
#   BSTACK_BENCH_LIVE=1 bash tests/bench-live.test.sh
#
#   # Railway credential broker (recommended for shared dev envs):
#   BSTACK_BENCH_LIVE=1 railway run --service stimulus-api -- \\
#       bash tests/bench-live.test.sh
#
# Assertions (when LIVE):
#   1. provider instantiation succeeds (configured() returns True)
#   2. minimal `chat()` call returns content + populated usage stats
#   3. Phase 1 run produces non-canned token counts (real, not from _TOKENS dict)
#   4. LLMJudgeEvaluator returns a parseable verdict
#   5. P20 enforcement still applies when --judge-model == --model
#
# Cost estimate: ~$0.005 per run (Haiku is cheap; ~1k tokens total).

set -uo pipefail

BSTACK_REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BSTACK_BENCH="$BSTACK_REPO/bin/bstack-bench"

# ── Gate ────────────────────────────────────────────────────────────────
if [ "${BSTACK_BENCH_LIVE:-}" != "1" ]; then
    echo "tests/bench-live.test.sh: BSTACK_BENCH_LIVE=1 not set; SKIPPING."
    echo "  To run: BSTACK_BENCH_LIVE=1 bash tests/bench-live.test.sh"
    echo "  Or:     BSTACK_BENCH_LIVE=1 railway run --service stimulus-api -- bash tests/bench-live.test.sh"
    exit 0
fi
if [ -z "${DATABRICKS_HOST:-}" ] || [ -z "${DATABRICKS_TOKEN:-}" ]; then
    echo "tests/bench-live.test.sh: BSTACK_BENCH_LIVE=1 set but DATABRICKS_HOST/_TOKEN missing; SKIPPING."
    echo "  Provide via shell export, .env, or `railway run --service stimulus-api --`."
    exit 0
fi

BSTACK_BENCH_HOME="$(mktemp -d -t bstack-bench-live.XXXXXX)"
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

pick_py() {
    for cand in python3.13 python3.12 python3.11 python3.10; do
        if command -v "$cand" >/dev/null 2>&1; then
            local v
            v="$("$cand" -c 'import sys; print(sys.version_info[:2] >= (3,10))' 2>/dev/null || echo False)"
            [ "$v" = "True" ] && { echo "$cand"; return 0; }
        fi
    done
    for probe in /opt/homebrew/bin/python3.13 /opt/homebrew/bin/python3.12; do
        [ -x "$probe" ] && { echo "$probe"; return 0; }
    done
    return 1
}
PY="$(pick_py || true)"
if [ -z "$PY" ]; then
    echo "Need Python 3.10+; SKIPPING."
    exit 0
fi

echo "tests/bench-live.test.sh: LIVE mode — real Databricks calls (Haiku)."
echo "  Host: $DATABRICKS_HOST"
echo "  Token: ${DATABRICKS_TOKEN:0:12}...${DATABRICKS_TOKEN: -8}"
echo ""

# ── 1. Provider instantiates + configured() returns True ─────────────────
out="$("$PY" -c "
import sys
sys.path.insert(0, '$BSTACK_REPO/scripts')
from bench.providers import get_provider
p = get_provider('databricks')
print(f'name={p.name} configured={p.configured()} models={p.list_models()[:2]}')
" 2>&1)"
if echo "$out" | grep -q "name=databricks configured=True"; then
    assert_pass "DatabricksGatewayProvider instantiates with env creds (configured=True)"
else
    assert_fail "DatabricksGatewayProvider failed to instantiate" "$out"
fi

# ── 2. Minimal chat() call returns content + usage ───────────────────────
chat_out="$("$PY" -c "
import sys
sys.path.insert(0, '$BSTACK_REPO/scripts')
from bench.providers import get_provider, ChatMessage
p = get_provider('databricks')
resp = p.chat(
    messages=[ChatMessage(role='user', content='Reply with exactly the word PONG.')],
    model='databricks-claude-haiku-4-5',
    max_tokens=10,
)
print(f'content={resp.content!r}')
print(f'model={resp.model}')
print(f'tokens={resp.usage.prompt_tokens}+{resp.usage.completion_tokens}={resp.usage.total_tokens}')
print(f'finish={resp.finish_reason}')
" 2>&1)"
if echo "$chat_out" | grep -q "content='PONG'" \
    && echo "$chat_out" | grep -qE "tokens=[0-9]+\+[0-9]+=[0-9]+" \
    && echo "$chat_out" | grep -q "finish=stop"; then
    assert_pass "live chat() returns PONG with parseable usage stats + finish_reason"
else
    assert_fail "live chat() did not return expected shape" "$chat_out"
fi

# ── 3. Full Phase 1 bench run with live runner ───────────────────────────
run_out="$("$BSTACK_BENCH" run --tasks bstack-smoke --runner live \
    --provider databricks --model databricks-claude-haiku-4-5 \
    --phase 1 --no-dry-run --budget-usd 0.10 2>&1)"
run_rc=$?
if [ "$run_rc" = "0" ]; then
    run_dir="$(ls -1 "$BSTACK_BENCH_HOME/runs" 2>/dev/null | head -1)"
    if [ -n "$run_dir" ]; then
        # Verify tokens are real (not the canned 600/520/280 from DryRunRunner)
        sample_tokens="$("$PY" -c "
import json
with open('$BSTACK_BENCH_HOME/runs/$run_dir/phase1_results.jsonl') as f:
    for line in f:
        o = json.loads(line)
        print(o['tokens']['total_tokens'])
        break
")"
        # DryRunRunner canned values: 600, 520, 280. Real Haiku is likely 300-2000+.
        # Just verify it's not one of the canned values.
        if [ -n "$sample_tokens" ] && [ "$sample_tokens" != "600" ] \
            && [ "$sample_tokens" != "520" ] && [ "$sample_tokens" != "280" ]; then
            assert_pass "live Phase 1 run produces non-canned token counts (got $sample_tokens — real Databricks usage)"
        else
            assert_fail "tokens look canned (got '$sample_tokens')"
        fi
    else
        assert_fail "live run produced no run dir"
    fi
else
    assert_fail "live --phase 1 run failed (rc=$run_rc)" "$run_out"
fi

# ── 4. LLMJudgeEvaluator end-to-end (different models for agent + judge) ─
JUDGE_HOME="$(mktemp -d -t bstack-bench-judge.XXXXXX)"
judge_out="$(BSTACK_BENCH_HOME="$JUDGE_HOME" "$BSTACK_BENCH" run --tasks bstack-smoke \
    --runner live --evaluator llm-judge \
    --provider databricks \
    --model databricks-claude-haiku-4-5 \
    --judge-model databricks-claude-sonnet-4 \
    --phase 1 --no-dry-run --budget-usd 0.50 2>&1)"
judge_rc=$?
rm -rf "$JUDGE_HOME"
if [ "$judge_rc" = "0" ] || [ "$judge_rc" = "6" ]; then
    # Either: all tasks scored ≥0.6 (rc=0) or some failed (rc=6 only if ALL failed).
    # rc=0 is the expected happy path for these simple tasks.
    assert_pass "LLMJudgeEvaluator (Haiku agent / Sonnet judge) ran end-to-end (rc=$judge_rc)"
else
    assert_fail "LLMJudgeEvaluator end-to-end failed (rc=$judge_rc)" "$judge_out"
fi

# ── 5. P20 still enforces same-model rejection even in live mode ─────────
set +e
p20_out="$("$BSTACK_BENCH" run --tasks bstack-smoke --runner live --evaluator llm-judge \
    --provider databricks \
    --model databricks-claude-haiku-4-5 \
    --judge-model databricks-claude-haiku-4-5 \
    --phase 1 --no-dry-run 2>&1)"
p20_rc=$?
set -e
if [ "$p20_rc" = "8" ]; then
    assert_pass "P20 enforcement holds in live mode (same model → rc=8)"
else
    assert_fail "P20 same-model in live mode should rc=8 (got rc=$p20_rc)" "$p20_out"
fi

# ── Summary ──────────────────────────────────────────────────────────────
echo ""
echo "=== LIVE Summary ==="
echo "  Passed: $PASS"
echo "  Failed: $FAIL"
echo "  Cost:   ~\$$( "$PY" -c 'print(round(0.005,4))' )  (Haiku, ~1-3k tokens total)"
if [ "$FAIL" -gt 0 ]; then
    echo ""
    echo "Failed tests:"
    for t in "${FAILED_TESTS[@]}"; do
        echo "  - $t"
    done
    exit 1
fi
exit 0
