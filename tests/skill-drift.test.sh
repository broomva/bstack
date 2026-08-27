#!/usr/bin/env bash
# tests/skill-drift.test.sh — BRO-2369 (child of BRO-2368).
#
# Merging to skills/main does not deploy a skill. What runs is whatever branch
# the checkout behind the symlink happens to be parked on. When that was found,
# 89 installed skills were running from a checkout 3 commits behind main, and
# three merged lint/bookkeeping GATE fixes were inert with no signal anywhere.
#
# scripts/lib/skill-drift.py reports that difference. Its whole reason to exist
# is that the failure mode is a SILENT PASS, so the tests below are weighted
# toward one property: it must never say "current" about something it did not
# actually verify.
#
# Fixtures are real git repos, not stubs — the thing under test is what git
# reports about a working tree, so a mocked git would be testing the mock.
#
# Asserts:
#   1. a skill on a branch behind origin/main is reported as drifted
#   2. NEGATIVE CONTROL: a skill whose checkout is current reports [ok]
#   3. a repo with no origin/main ref is UNKNOWN, never clean
#   4. one skill reached through two roots is counted once
#   5. a dangling symlink is reported, not silently skipped
#   6. an installed copy with no git provenance is counted, not called current
#   7. uncommitted changes in the checkout are surfaced
#   8. the check never exits non-zero — it is advisory, never a gate
#   9. doctor §27 emits no gap() — it can never become a gate
#  10. a checkout AHEAD of origin/main is drift, not "current"
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DRIFT="$REPO/scripts/lib/skill-drift.py"
PY="${PYTHON:-python3}"
export PYTHONDONTWRITEBYTECODE=1

PASS=0; FAIL=0; FAILED=()
pass() { echo "  [ok] $1"; PASS=$((PASS + 1)); }
fail() { echo "  [FAIL] $1"; FAIL=$((FAIL + 1)); FAILED+=("$1"); }

echo "── skill drift vs origin/main (BRO-2369) ──────────────"

TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

# ── build an "upstream" and two clones: one current, one parked behind ───────
UP="$TMP/upstream"
mkdir -p "$UP" && ( cd "$UP" && git init -q -b main .
    mkdir -p skills/alpha skills/beta
    echo v1 > skills/alpha/SKILL.md; echo v1 > skills/beta/SKILL.md
    git add -A && git -c user.email=t@t -c user.name=t commit -q -m c1 )

git clone -q "$UP" "$TMP/current" 2>/dev/null
git clone -q "$UP" "$TMP/behind"  2>/dev/null

# advance upstream, then refresh only the refs — `behind` now trails main
( cd "$UP" && echo v2 > skills/alpha/SKILL.md \
    && git -c user.email=t@t -c user.name=t commit -qam c2 )
( cd "$TMP/behind"  && git fetch -q origin && git checkout -q -b feature/parked )
( cd "$TMP/current" && git fetch -q origin && git checkout -q -B main origin/main )

mkroot() { mkdir -p "$1"; }
run()    { "$PY" "$DRIFT" --skills-dir "$@" 2>&1; }

# ── 1. behind origin/main -> drifted ────────────────────────────────────────
R1="$TMP/root1"; mkroot "$R1"
ln -s "$TMP/behind/skills/alpha" "$R1/alpha"
OUT=$(run "$R1")
if echo "$OUT" | grep -q 'commit(s) behind' && echo "$OUT" | grep -q 'feature/parked'; then
    pass "1. a checkout behind origin/main is reported as drifted"
else
    fail "1. drift not reported: $OUT"
fi

# ── 2. NEGATIVE CONTROL — a current checkout must report [ok] ───────────────
# Without this, assert 1 passes for a checker that flags everything, and a
# clean report would carry no information.
R2="$TMP/root2"; mkroot "$R2"
ln -s "$TMP/current/skills/alpha" "$R2/alpha"
OUT=$(run "$R2")
if echo "$OUT" | grep -q '\[ok\]' && ! echo "$OUT" | grep -q 'behind'; then
    pass "2. NEGATIVE CONTROL: a current checkout reports [ok], no drift"
else
    fail "2. current checkout not reported clean: $OUT"
fi

# ── 3. no origin/main ref -> UNKNOWN, never clean ───────────────────────────
NR="$TMP/noremote"
mkdir -p "$NR/skills/alpha" && ( cd "$NR" && git init -q -b main .
    echo x > skills/alpha/SKILL.md && git add -A \
    && git -c user.email=t@t -c user.name=t commit -q -m c1 )
R3="$TMP/root3"; mkroot "$R3"
ln -s "$NR/skills/alpha" "$R3/alpha"
OUT=$(run "$R3")
if echo "$OUT" | grep -q 'UNKNOWN' && ! echo "$OUT" | grep -q '\[ok\]'; then
    pass "3. a repo with no origin/main is UNKNOWN, not clean"
else
    fail "3. missing origin/main did not read as unknown: $OUT"
fi

# ── 4. one skill through two roots is counted once ──────────────────────────
# ~/.claude/skills/x -> ~/.agents/skills/x -> checkout is the normal shape here;
# counting per-root double-reported every skill on this machine.
RA="$TMP/rootA"; RB="$TMP/rootB"; mkroot "$RA"; mkroot "$RB"
ln -s "$TMP/behind/skills/alpha" "$RB/alpha"
ln -s "$RB/alpha" "$RA/alpha"
N=$("$PY" "$DRIFT" --skills-dir "$RA" --skills-dir "$RB" --json 2>/dev/null \
     | "$PY" -c 'import json,sys; d=json.load(sys.stdin); print(sum(len(x["skills"]) for x in d["drifted"]))')
if [ "$N" = "1" ]; then
    pass "4. a skill reached through two roots is counted once"
else
    fail "4. expected 1 skill, got $N (double-counted)"
fi

# ── 5. a dangling symlink is reported ───────────────────────────────────────
# `readlink` still prints a path for these, so they are easy to miss.
R5="$TMP/root5"; mkroot "$R5"
ln -s "$TMP/does-not-exist" "$R5/ghost"
OUT=$(run "$R5")
if echo "$OUT" | grep -q 'could not be resolved' && echo "$OUT" | grep -q 'ghost'; then
    pass "5. a dangling symlink is reported, not skipped"
else
    fail "5. dangling symlink not surfaced: $OUT"
fi

# ── 6. an installed copy with no git provenance is counted, not called current
R6="$TMP/root6"; mkroot "$R6"
mkdir -p "$R6/plaincopy" && echo x > "$R6/plaincopy/SKILL.md"
OUT=$(run "$R6")
if echo "$OUT" | grep -q 'no git provenance'; then
    pass "6. a non-git installed copy is counted as not-evaluable"
else
    fail "6. non-git copy not surfaced: $OUT"
fi

# ── 7. uncommitted changes in the checkout are surfaced ─────────────────────
echo dirty >> "$TMP/behind/skills/beta/SKILL.md"
OUT=$(run "$R1")
if echo "$OUT" | grep -q 'uncommitted'; then
    pass "7. uncommitted changes in the checkout are surfaced"
else
    fail "7. uncommitted changes not surfaced: $OUT"
fi

# ── 8. advisory: never exits non-zero, even with drift present ──────────────
"$PY" "$DRIFT" --skills-dir "$R1" >/dev/null 2>&1
RC=$?
if [ "$RC" = "0" ]; then
    pass "8. advisory — exits 0 even when drift is found"
else
    fail "8. exited $RC with drift present; this must never gate"
fi

# ── 10. AHEAD of origin/main is drift too ──────────────────────────────────
# The first draft measured only `HEAD..origin/main` — "is it missing merged
# work". That is not the question. A checkout sitting 0 behind on a branch
# carrying its own commits runs code that never merged, and the checker called it
# "current with origin/main": the exact silent pass it exists to prevent,
# reproduced inside it.
AH="$TMP/aheadrepo"
git clone -q "$UP" "$AH" 2>/dev/null
( cd "$AH" && git fetch -q origin && git checkout -q -b local-work \
    && echo LOCAL > skills/beta/SKILL.md \
    && git -c user.email=t@t -c user.name=t commit -qam "never merged" )
R10="$TMP/root10"; mkroot "$R10"
ln -s "$AH/skills/beta" "$R10/beta"
BEHIND=$( cd "$AH" && git rev-list --count HEAD..origin/main )
OUT=$(run "$R10")
if [ "$BEHIND" = "0" ] && echo "$OUT" | grep -q 'unmerged commit' \
   && ! echo "$OUT" | grep -q '\[ok\]'; then
    pass "10. 0-behind but ahead of origin/main is reported as drift"
else
    fail "10. ahead-of-main read as current (behind=$BEHIND): $OUT"
fi

# ── 9. the doctor section itself can never become a gate ───────────────────
# The value of this check is that it is safe to leave on. If a future edit turns
# an [info] into a gap(), every workspace with a parked checkout starts failing
# `doctor --strict` on a deployment fact, and the check gets disabled instead.
# Anchored on ASCII only. The first draft terminated on the box-drawing "# ──"
# comment, where `.` matching a multi-byte char is locale- and awk-dependent.
SEC=$(sed -n '/^section "27\./,/^TOTAL=/p' "$REPO/scripts/doctor.sh")
if [ -n "$SEC" ] && ! echo "$SEC" | grep -qE '(^|[^_[:alnum:]])gap[[:space:]]+"'; then
    pass "9. doctor §27 emits no gap() — advisory by construction"
else
    fail "9. doctor §27 calls gap(), or the section was not found"
fi

echo ""
if [ "$FAIL" = "0" ]; then
    echo "[skill-drift] $PASS/$PASS passed"
    exit 0
fi
echo "[skill-drift] $PASS passed, $FAIL failed:"
for f in "${FAILED[@]}"; do echo "   - $f"; done
exit 1
