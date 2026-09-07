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
#   1. a merged change the checkout has not pulled is reported
#   2. NEGATIVE CONTROL: a checkout matching origin/main reports the clean line
#   3. no origin/main is UNKNOWN — a stale origin/master does not stand in
#   4. one skill reached through two roots is counted once
#   5. a dangling symlink is reported, not silently skipped
#   6. an installed copy with no git provenance is counted, not called current
#   7. an uncommitted edit is drift even when commit topology says 0 behind/ahead
#   8. an untracked file inside the skill is drift
#   9. a README-only commit does NOT mark skills drifted
#  10. a 0-behind branch carrying unmerged work is drift
#  11. advisory — never exits non-zero
#  12. doctor §27 emits no gap()
#  13/14. assume-unchanged / skip-worktree are UNVERIFIABLE, not clean
#  15. NEGATIVE CONTROL for 13/14
#  16. a staged-but-uncommitted edit is drift
#  17. a file RENAMED out of the skill dir is drift (rename detection hides the source)
#  18. a NON-ASCII diverging path is drift (git C-quotes it without -z)
#  19. a NON-ASCII assume-unchanged path is UNVERIFIABLE (same quoting blind spot)
#  20. a STALE origin/main is UNKNOWN, not a basis for "matches origin/main"
#  21. NEGATIVE CONTROL for 20 — a freshly fetched ref still compares normally
#  22. STRUCTURAL: the undatable-ref arm exists (unreachable by construction)
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

# ── one upstream; clones are made per-case so each starts from a known ref ──
UP="$TMP/upstream"
mkdir -p "$UP" && ( cd "$UP" && git init -q -b main .
    mkdir -p skills/alpha skills/beta
    echo v1 > skills/alpha/SKILL.md; echo v1 > skills/beta/SKILL.md
    echo readme > README.md
    git add -A && git -c user.email=t@t -c user.name=t commit -q -m c1 )

up_commit() {  # $1 = path, $2 = content, $3 = message
    ( cd "$UP" && echo "$2" > "$1" \
      && git -c user.email=t@t -c user.name=t commit -qam "$3" )
}
clone() {      # $1 = name -> $TMP/$1, checked out at origin/main
    git clone -q "$UP" "$TMP/$1" 2>/dev/null
    ( cd "$TMP/$1" && git fetch -q origin && git checkout -q -B main origin/main )
}
link() {       # $1 = root, $2 = target
    mkdir -p "$1" && ln -s "$2" "$1/$(basename "$2")"
}
run() { "$PY" "$DRIFT" --skills-dir "$@" 2>&1; }

# ── 1. a merged change to the skill, not pulled -> drift ───────────────────
clone c1
up_commit skills/alpha/SKILL.md v2 "alpha v2"
( cd "$TMP/c1" && git fetch -q origin )
R1="$TMP/root1"; link "$R1" "$TMP/c1/skills/alpha"
OUT=$(run "$R1")
if echo "$OUT" | grep -q 'differ from origin/main' && echo "$OUT" | grep -q 'alpha'; then
    pass "1. a merged change the checkout has not pulled is reported"
else
    fail "1. drift not reported: $OUT"
fi

# ── 2. NEGATIVE CONTROL — a matching checkout reports the clean line ───────
# Without this, assert 1 passes for a checker that flags everything and a clean
# report carries no information.
clone c2
R2="$TMP/root2"; link "$R2" "$TMP/c2/skills/alpha"
OUT=$(run "$R2")
if echo "$OUT" | grep -qE '\[info\].*match origin/main' && ! echo "$OUT" | grep -q 'differ'; then
    pass "2. NEGATIVE CONTROL: a matching checkout reports the clean line"
else
    fail "2. matching checkout not reported clean: $OUT"
fi

# ── 3. no origin/main -> UNKNOWN, even with a stale origin/master that matches
# A fallback to master would compare against the wrong ref and then print
# "current with origin/main" — inventing a clean answer out of a missing one.
clone c3
( cd "$TMP/c3" && git update-ref refs/remotes/origin/master HEAD \
    && git update-ref -d refs/remotes/origin/main )
R3="$TMP/root3"; link "$R3" "$TMP/c3/skills/alpha"
OUT=$(run "$R3")
if echo "$OUT" | grep -q 'UNKNOWN' && ! echo "$OUT" | grep -q '\[ok\]'; then
    pass "3. missing origin/main is UNKNOWN, and origin/master does not stand in"
else
    fail "3. missing origin/main did not read as unknown: $OUT"
fi

# ── 4. one skill through two roots is counted once ─────────────────────────
clone c4
up_commit skills/alpha/SKILL.md v3 "alpha v3"
( cd "$TMP/c4" && git fetch -q origin )
RA="$TMP/rootA"; RB="$TMP/rootB"; mkdir -p "$RA" "$RB"
ln -s "$TMP/c4/skills/alpha" "$RB/alpha"
ln -s "$RB/alpha" "$RA/alpha"
N=$("$PY" "$DRIFT" --skills-dir "$RA" --skills-dir "$RB" --json 2>/dev/null \
     | "$PY" -c 'import json,sys; d=json.load(sys.stdin); print(sum(len(x["skills"]) for x in d["drifted"]))')
if [ "$N" = "1" ]; then
    pass "4. a skill reached through two roots is counted once"
else
    fail "4. expected 1 skill, got $N (double-counted)"
fi

# ── 5. a dangling symlink is reported ──────────────────────────────────────
# `readlink` still prints a path for these, so they are easy to miss.
R5="$TMP/root5"; mkdir -p "$R5"
ln -s "$TMP/does-not-exist" "$R5/ghost"
OUT=$(run "$R5")
if echo "$OUT" | grep -q 'could not be resolved' && echo "$OUT" | grep -q 'ghost'; then
    pass "5. a dangling symlink is reported, not skipped"
else
    fail "5. dangling symlink not surfaced: $OUT"
fi

# ── 6. an installed copy with no git provenance is counted, not called current
R6="$TMP/root6"; mkdir -p "$R6/plaincopy"; echo x > "$R6/plaincopy/SKILL.md"
OUT=$(run "$R6")
if echo "$OUT" | grep -q 'no git provenance'; then
    pass "6. a non-git installed copy is counted as not-evaluable"
else
    fail "6. non-git copy not surfaced: $OUT"
fi

# ── 7. THE BLOCKER — HEAD matches origin/main, file modified on disk ───────
# The first design compared commit topology, so this printed "[ok] current"
# while the skill executed modified code. The earlier version of this test
# passed for the wrong reason: its checkout was already behind, and it dirtied
# `beta` while scanning `alpha`. Both are fixed here — same skill, no other
# source of drift.
clone c7
R7="$TMP/root7"; link "$R7" "$TMP/c7/skills/alpha"
BA=$( cd "$TMP/c7" && git rev-list --left-right --count origin/main...HEAD | tr -d '[:space:]' )
echo MODIFIED-ON-DISK > "$TMP/c7/skills/alpha/SKILL.md"
OUT=$(run "$R7")
if [ "$BA" = "00" ] && echo "$OUT" | grep -q 'differ from origin/main'; then
    pass "7. an uncommitted edit to the installed skill is drift (topology says 0/0)"
else
    fail "7. working-tree edit missed (behind/ahead=$BA): $OUT"
fi

# ── 8. an untracked file inside the skill is drift ─────────────────────────
# `git diff` alone does not see a new file, so a skill could gain a script that
# never merged and still read as matching.
clone c8
R8="$TMP/root8"; link "$R8" "$TMP/c8/skills/alpha"
echo "print('new')" > "$TMP/c8/skills/alpha/extra.py"
OUT=$(run "$R8")
if echo "$OUT" | grep -q 'differ from origin/main'; then
    pass "8. an untracked file inside the skill counts as drift"
else
    fail "8. untracked file missed: $OUT"
fi

# ── 9. a commit that touches only README leaves skills clean ───────────────
# Commit topology marked every skill in the repo drifted for this, which is
# noise, and an advisory that cries wolf gets switched off.
clone c9
up_commit README.md changed "readme only"
( cd "$TMP/c9" && git fetch -q origin )
BEHIND=$( cd "$TMP/c9" && git rev-list --count HEAD..origin/main )
R9="$TMP/root9"; link "$R9" "$TMP/c9/skills/alpha"
OUT=$(run "$R9")
if [ "$BEHIND" = "1" ] && echo "$OUT" | grep -qE '\[info\].*match origin/main'; then
    pass "9. a README-only commit does not mark skills drifted (behind=$BEHIND)"
else
    fail "9. README-only commit misreported (behind=$BEHIND): $OUT"
fi

# ── 10. a local commit that never merged is drift ──────────────────────────
clone c10
( cd "$TMP/c10" && git checkout -q -b local-work \
    && echo LOCAL > skills/alpha/SKILL.md \
    && git -c user.email=t@t -c user.name=t commit -qam "never merged" )
BEHIND=$( cd "$TMP/c10" && git rev-list --count HEAD..origin/main )
R10="$TMP/root10"; link "$R10" "$TMP/c10/skills/alpha"
OUT=$(run "$R10")
if [ "$BEHIND" = "0" ] && echo "$OUT" | grep -q 'differ from origin/main'; then
    pass "10. a 0-behind branch carrying unmerged work is drift"
else
    fail "10. unmerged local commit read as current (behind=$BEHIND): $OUT"
fi

# ── 11. advisory: never exits non-zero, even with drift present ────────────
"$PY" "$DRIFT" --skills-dir "$R1" >/dev/null 2>&1
RC=$?
if [ "$RC" = "0" ]; then
    pass "11. advisory — exits 0 even when drift is found"
else
    fail "11. exited $RC with drift present; this must never gate"
fi

# ── 13/14. index flags that hide a file from git ───────────────────────────
# `assume-unchanged` and `skip-worktree` exist to make a modified file invisible
# to git. Both produced a FALSE CLEAN: the file on disk read SILENTLY-EDITED and
# the verdict was "[ok] matches origin/main". A path git has been told not to
# look at is UNVERIFIABLE, and unverifiable is never reported as current.
n=13
for flag in assume-unchanged skip-worktree; do
    clone "idx$n"
    ( cd "$TMP/idx$n" && git update-index --$flag skills/alpha/SKILL.md )
    echo SILENTLY-EDITED > "$TMP/idx$n/skills/alpha/SKILL.md"
    RI="$TMP/rootidx$n"; link "$RI" "$TMP/idx$n/skills/alpha"
    SEEN=$( cd "$TMP/idx$n" && git diff --name-only origin/main )
    OUT=$(run "$RI")
    if [ -z "$SEEN" ] && echo "$OUT" | grep -q 'UNVERIFIABLE'; then
        pass "$n. --$flag hides the edit from git; reported UNVERIFIABLE, not clean"
    else
        fail "$n. --$flag: git saw '$SEEN', checker said: $OUT"
    fi
    n=$((n + 1))
done

# ── 15. NEGATIVE CONTROL for 13/14 — no flags, unmodified, still clean ─────
# Without this, 13/14 pass for a checker that calls everything unverifiable.
clone idx15
R15="$TMP/root15"; link "$R15" "$TMP/idx15/skills/alpha"
OUT=$(run "$R15")
if echo "$OUT" | grep -qE '\[info\].*match origin/main' && ! echo "$OUT" | grep -q 'UNVERIFIABLE'; then
    pass "15. NEGATIVE CONTROL: no index flags, unmodified -> still clean"
else
    fail "15. clean checkout misreported after the index-flag change: $OUT"
fi

# ── 16. a staged-but-uncommitted edit is drift ─────────────────────────────
clone idx16
echo STAGED > "$TMP/idx16/skills/alpha/SKILL.md"
( cd "$TMP/idx16" && git add skills/alpha/SKILL.md )
R16="$TMP/root16"; link "$R16" "$TMP/idx16/skills/alpha"
OUT=$(run "$R16")
if echo "$OUT" | grep -q 'differ from origin/main'; then
    pass "16. a staged-but-uncommitted edit is drift"
else
    fail "16. staged edit missed: $OUT"
fi

# ── 12. the doctor section itself can never become a gate ───────────────────
# The value of this check is that it is safe to leave on. If a future edit turns
# an [info] into a gap(), every workspace with a parked checkout starts failing
# `doctor --strict` on a deployment fact, and the check gets disabled instead.
# Anchored on ASCII only. The first draft terminated on the box-drawing "# ──"
# comment, where `.` matching a multi-byte char is locale- and awk-dependent.
# Terminate at §28, not TOTAL=. v0.40.1 inserted §28 between §27 and the
# summary, so the original range silently grew to cover BOTH sections: the
# assertion would then pass or fail on §28's behaviour while naming §27.
SEC=$(sed -n '/^section "27\./,/^# ── Section 28:/p' "$REPO/scripts/doctor.sh")
if [ -n "$SEC" ] && ! echo "$SEC" | grep -qE '(^|[^_[:alnum:]])gap[[:space:]]+"'; then
    pass "12. doctor §27 emits no gap() — advisory by construction"
else
    fail "12. doctor §27 calls gap(), or the section was not found"
fi

# ── 17. a file RENAMED OUT of the skill dir is drift ───────────────────────
# git detects renames by default (diff.renames=true), and for a rename
# `--name-only` prints only the DESTINATION. Moving skills/alpha/SKILL.md out
# therefore produced a diff naming only the new path and nothing under
# skills/alpha/, so drifted_paths() found nothing and the skill whose only file
# had just left was reported as MATCHING origin/main — a positive clean verdict
# on a drifted skill, which is precisely the failure this module exists to
# prevent. Found by CodeRabbit on PR #105; `--no-renames` lists both sides.
clone c17
R17="$TMP/root17"; link "$R17" "$TMP/c17/skills/alpha"
( cd "$TMP/c17" && git mv skills/alpha/SKILL.md README-moved.md )
OUT=$(run "$R17")
if echo "$OUT" | grep -q 'differ from origin/main' && echo "$OUT" | grep -q 'alpha'; then
    pass "17. a file renamed OUT of the skill dir is drift, not clean"
else
    fail "17. rename out of the skill dir misreported as clean: $OUT"
fi

# ── 18/19. NON-ASCII PATHS. git renders any path containing a byte >= 0x80, a
# quote, a backslash or a control char as a C-quoted string wrapped in literal
# double quotes — "skills/alpha/NARI\303\221O.txt" — unless -z is passed. The
# prefix test in _under() then never matches, the path is dropped, and the skill
# reports as MATCHING origin/main: a positive clean verdict on a diverging
# skill. The same quoting defeated the assume-unchanged detection independently,
# so both arms are pinned. Live on this machine: the tracked file
# skills/knowledge/colombia-conflict/.../CEV_TERRITORIAL_NARI<N-tilde>O_*.txt.gz.
# Found by P20 round 1 on PR #105.
NON_ASCII="NARI$(printf '\303\221')O.txt"

clone c18
# NOT up_commit: that uses `commit -am`, which stages modifications to TRACKED
# files only, so a brand-new path would never reach the upstream commit and the
# case would pass vacuously against a clone that is not actually behind.
( cd "$UP" && printf 'v2\n' > "skills/alpha/$NON_ASCII" && git add -A \
  && git -c user.email=t@t -c user.name=t commit -qm "non-ascii divergence" )
( cd "$TMP/c18" && git fetch -q origin )
BEHIND18=$( cd "$TMP/c18" && git rev-list --count HEAD..origin/main )
R18="$TMP/root18"; link "$R18" "$TMP/c18/skills/alpha"
OUT=$(run "$R18")
if [ "$BEHIND18" = "1" ] && echo "$OUT" | grep -q 'differ from origin/main' && echo "$OUT" | grep -q 'alpha'; then
    pass "18. a non-ASCII diverging path is drift, not clean"
else
    fail "18. non-ASCII divergence misreported (behind=$BEHIND18): $OUT"
fi

clone c19
R19="$TMP/root19"; link "$R19" "$TMP/c19/skills/alpha"
( cd "$TMP/c19" && printf 'v1\n' > "skills/alpha/$NON_ASCII" \
  && git add -A && git -c user.email=t@t -c user.name=t commit -qm "add non-ascii" \
  && git update-index --assume-unchanged "skills/alpha/$NON_ASCII" \
  && printf 'EDITED\n' > "skills/alpha/$NON_ASCII" )
OUT=$(run "$R19")
if echo "$OUT" | grep -q 'UNVERIFIABLE'; then
    pass "19. a non-ASCII assume-unchanged path is UNVERIFIABLE, not clean"
else
    fail "19. non-ASCII assume-unchanged misreported: $OUT"
fi

# ── 20/21. REF FRESHNESS. `origin/main` that exists but was never refreshed is
# not a comparison, it is a comparison against a fiction — and the module's own
# rule is that what cannot be verified is never reported as current. Measured on
# this machine when the check was written: a clone with no FETCH_HEAD whose
# packed-refs was last written 61 days earlier had an origin/main 180 commits
# behind upstream, and its 23 skills were counted inside "match origin/main".
# Dated from mtimes, so still no network. Found by P20 round 1 on PR #105.
clone c20
R20="$TMP/root20"; link "$R20" "$TMP/c20/skills/alpha"
GD20="$(cd "$TMP/c20" && git rev-parse --absolute-git-dir)"
OLD=$(( $(date +%s) - 60*86400 ))
for f in FETCH_HEAD packed-refs refs/remotes/origin/main; do
    [ -e "$GD20/$f" ] && touch -t "$(date -r $OLD +%Y%m%d%H%M.%S)" "$GD20/$f"
done
OUT=$(run "$R20")
if echo "$OUT" | grep -q 'UNKNOWN' && echo "$OUT" | grep -q 'too stale to compare'; then
    pass "20. a 60d-stale origin/main is UNKNOWN, not a comparison"
else
    fail "20. stale ref treated as a valid basis: $OUT"
fi

# Without this, case 20 passes for a checker that calls EVERY repo stale.
clone c21
R21="$TMP/root21"; link "$R21" "$TMP/c21/skills/alpha"
OUT=$(run "$R21")
if echo "$OUT" | grep -qE '\[info\].*match origin/main' && ! echo "$OUT" | grep -q 'too stale'; then
    pass "21. NEGATIVE CONTROL: a freshly fetched ref still compares"
else
    fail "21. a fresh ref was misreported as stale: $OUT"
fi

# ── 22. STRUCTURAL — the undatable-ref arm exists. It is unreachable: the ref
# verifies only if packed-refs or a loose refs/remotes/origin/main is present,
# and either dates it; remove both and rev-parse --verify fails first. So it
# survives behavioural mutation by construction and is asserted at the source
# instead of being given a test that would pass without exercising anything.
if grep -q 'cannot date origin/main' "$REPO/scripts/lib/skill-drift.py"; then
    pass "22. the undatable-ref arm is present (structural; unreachable by construction)"
else
    fail "22. the undatable-ref arm was removed"
fi

echo ""
if [ "$FAIL" = "0" ]; then
    echo "[skill-drift] $PASS/$PASS passed"
    exit 0
fi
echo "[skill-drift] $PASS passed, $FAIL failed:"
for f in "${FAILED[@]}"; do echo "   - $f"; done
exit 1
