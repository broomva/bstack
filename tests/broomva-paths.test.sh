#!/usr/bin/env bash
# tests/broomva-paths.test.sh — BRO-3216. The path registry: runs the reader
# suite (tests/test_broomva_paths.py, stdlib unittest) under bstack CI, which
# discovers tests/*.test.sh only, and checks doctor §30 against seeded faults.
#
# No skip path: a skipped test, an expected failure or a shrunk suite fails the
# run, because unittest exits 0 on all three.
#
#   bash tests/broomva-paths.test.sh

set -uo pipefail

BSTACK_REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$BSTACK_REPO" || exit 1
MIN_TESTS=29

PASS=0
FAIL=0
assert_pass() { PASS=$((PASS + 1)); echo "  [pass] $1"; }
assert_fail() { FAIL=$((FAIL + 1)); echo "  [FAIL] $1"; [ -n "${2:-}" ] && echo "         $2"; }

echo "── path registry (BRO-3216) ───────────────────────────────────────"

if python3 -c 'import tomllib' >/dev/null 2>&1; then
    if python3 scripts/gen_paths.py --check; then
        assert_pass "generated readers match references/paths.toml"
    else
        assert_fail "generated readers match references/paths.toml" "run python3 scripts/gen_paths.py"
    fi
else
    assert_fail "python3 has tomllib (3.11+) for gen_paths.py --check" "not skipping: install a newer python3"
fi

LOG="$(mktemp)"
SCRATCH="$(mktemp -d)"
trap 'rm -rf "$LOG" "$SCRATCH"' EXIT
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -v tests.test_broomva_paths >"$LOG" 2>&1
RC=$?
if [ "$RC" -eq 0 ]; then assert_pass "unittest suite exits clean"; else assert_fail "unittest suite exits clean" "exit $RC: $(tail -5 "$LOG")"; fi
VERDICT="$(grep -E '^(OK|FAILED)' "$LOG" | tail -1)"
if [ "$VERDICT" = "OK" ]; then assert_pass "verdict is a bare OK"; else assert_fail "verdict is a bare OK" "got: ${VERDICT:-<none>}"; fi
if grep -qE '\.\.\. (skipped|expected failure|unexpected success)' "$LOG"; then
    assert_fail "no test silently stopped asserting" "$(grep -E '\.\.\. (skipped|expected failure|unexpected success)' "$LOG" | head -3)"
else
    assert_pass "no test silently stopped asserting"
fi
COUNT="$(grep -oE '^Ran [0-9]+ test' "$LOG" | tail -1 | awk '{print $2}')"
if [ "${COUNT:-0}" -ge "$MIN_TESTS" ]; then assert_pass "suite size >= $MIN_TESTS (ran $COUNT)"; else assert_fail "suite size >= $MIN_TESTS" "only ${COUNT:-0} ran"; fi

# doctor §30, against seeded faults. A copy of bstack, so the real readers stay intact.
COPY="$SCRATCH/bstack"
mkdir -p "$COPY"
tar -C "$BSTACK_REPO" --exclude=.git -cf - . | tar -C "$COPY" -xf -
WS="$SCRATCH/ws"
mkdir -p "$WS/.control/asks"
# doctor resolves its workspace from BROOMVA_WORKSPACE (or the cwd), not from an argument.
section30() { HOME="$SCRATCH/home" BROOMVA_WORKSPACE="$1" bash "$COPY/scripts/doctor.sh" 2>&1 | sed -n '/^30\. Path registry/,/^$/p'; }
mkdir -p "$SCRATCH/home"

OUT="$(section30 "$WS")"
if printf '%s\n' "$OUT" | grep -q "\[ok\] path registry readers carry the table's hash" \
    && ! printf '%s\n' "$OUT" | grep -q "\[gap\]"; then
    assert_pass "doctor §30 passes a clean fixture"
else
    assert_fail "doctor §30 passes a clean fixture" "$OUT"
fi

mkdir -p "$WS/.broomva/asks"
OUT="$(section30 "$WS")"
if printf '%s\n' "$OUT" | grep -q "\[gap\] names under both .broomva/ and .control/ .*asks"; then
    assert_pass "doctor §30 fails a name under both roots"
else
    assert_fail "doctor §30 fails a name under both roots" "$OUT"
fi
rmdir "$WS/.broomva/asks"

printf '\n# a hand edit the readers never saw\n' >> "$COPY/references/paths.toml"
OUT="$(section30 "$WS")"
if printf '%s\n' "$OUT" | grep -q "\[gap\] path registry readers are stale"; then
    assert_pass "doctor §30 fails readers that do not carry the table's hash"
else
    assert_fail "doctor §30 fails readers that do not carry the table's hash" "$OUT"
fi

echo ""
echo "passed: $PASS  failed: $FAIL"
[ "$FAIL" -eq 0 ]
