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
MIN_TESTS=87

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
if printf '%s\n' "$OUT" | grep -q "\[ok\] path registry readers are what gen_paths.py writes" \
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

expect_gap() {  # expect_gap LABEL PATTERN
    OUT="$(section30 "$WS")"
    if printf '%s\n' "$OUT" | grep -q "\[gap\] $2"; then assert_pass "doctor §30 fails $1"; else assert_fail "doctor §30 fails $1" "$OUT"; fi
}

# C round-2 N1: the readers' workspace ($HOME/broomva) does not exist, and the one
# doctor audits holds both roots. That must still be a gap, never a vacuous ok.
section30_unset() { HOME="$SCRATCH/home" BROOMVA_WORKSPACE='' MAESTRO_WORKSPACE='' BSTACK_WORKSPACE='' \
    bash -c 'cd "$1" && bash "$2/scripts/doctor.sh"' _ "$WS" "$COPY" 2>&1 | sed -n '/^30\. Path registry/,/^$/p'; }
git -C "$WS" init -q 2>/dev/null
OUT="$(section30_unset)"
if printf '%s\n' "$OUT" | grep -q "\[gap\]"; then
    assert_fail "doctor §30 is clean on a CI layout (no readers' workspace)" "$OUT"
else
    assert_pass "doctor §30 is clean on a CI layout (no readers' workspace)"
fi
mkdir -p "$WS/.broomva/asks"
OUT="$(section30_unset)"
if printf '%s\n' "$OUT" | grep -q "\[gap\] names under both .broomva/ and .control/ in .*asks" \
    && printf '%s\n' "$OUT" | grep -q "\[info\] the readers' workspace .* does not exist"; then
    assert_pass "doctor §30 checks the audited workspace when the readers' one is missing"
else
    assert_fail "doctor §30 checks the audited workspace when the readers' one is missing" "$OUT"
fi
rmdir "$WS/.broomva/asks"

mkdir -p "$WS/.broomva" && touch "$WS/.broomva/policy.yaml"
expect_gap "a partially moved workspace" ".* has moved to .broomva/ but these are still under .control/: asks"
rm -f "$WS/.broomva/policy.yaml" && rmdir "$WS/.broomva"

mkdir -p "$SCRATCH/elsewhere" && ln -s "$SCRATCH/elsewhere" "$WS/.broomva"
expect_gap "a symlinked .broomva (paths would leave the repo)" "a registry name does not resolve in .*symlink"
rm -f "$WS/.broomva"

mkdir "$SCRATCH/home/.control"
expect_gap "a ~/.control (a tool fell back to \$HOME)" '\$HOME/.control exists'
rmdir "$SCRATCH/home/.control"

mkdir -p "$SCRATCH/home/.broomva" && touch "$SCRATCH/home/.broomva/policy.yaml"
expect_gap "a repo-scope name at the top of ~/.broomva" "repo-scope names at the top of .*policy.yaml"
rm -f "$SCRATCH/home/.broomva/policy.yaml"

mkdir -p "$WS/.broomva/asks"
chmod 0 "$WS/.broomva"
expect_gap "a workspace it cannot read (never an ok)" "could not check .* for names under both roots"
chmod 755 "$WS/.broomva" && rmdir "$WS/.broomva/asks" "$WS/.broomva"

git -C "$SCRATCH/home" init -q
expect_gap "a git repo at \$HOME" '\$HOME is a git top level'
mv "$SCRATCH/home/.git" "$SCRATCH/home-git-was-here"
mkdir -p "$SCRATCH/realhome" && git -C "$SCRATCH/realhome" init -q && ln -s "$SCRATCH/realhome" "$SCRATCH/linkhome"
OUT="$(HOME="$SCRATCH/linkhome" BROOMVA_WORKSPACE="$WS" bash "$COPY/scripts/doctor.sh" 2>&1 | sed -n '/^30\. Path registry/,/^$/p')"
if printf '%s\n' "$OUT" | grep -q '\[gap\] \$HOME is a git top level'; then
    assert_pass "doctor §30 fails a \$HOME that is a symlink to a git repo"
else
    assert_fail "doctor §30 fails a \$HOME that is a symlink to a git repo" "$OUT"
fi

# A hand edit that keeps the hash line: only the generator's own --check sees it.
cp "$COPY/scripts/broomva_paths.py" "$SCRATCH/reader.bak"
sed -i.orig "s|'policy': ('policy.yaml'|'policy': ('../../etc/passwd'|" "$COPY/scripts/broomva_paths.py"
if cmp -s "$COPY/scripts/broomva_paths.py" "$SCRATCH/reader.bak"; then
    assert_fail "the hand-edit fixture applied" "sed matched nothing"
else
    expect_gap "a hand-edited reader that kept its hash line" "path registry readers are stale or hand-edited"
fi
cp "$SCRATCH/reader.bak" "$COPY/scripts/broomva_paths.py"

printf '\n# a table edit the readers never saw\n' >> "$COPY/references/paths.toml"
expect_gap "readers older than the table" "path registry readers are stale"

echo ""
echo "passed: $PASS  failed: $FAIL"
[ "$FAIL" -eq 0 ]
