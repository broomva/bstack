#!/usr/bin/env bash
# tests/skill-graduate.test.sh — Smoke tests for `bstack skills graduate`.
#
# Tests run offline. Arg-parse + dry-run paths make no network calls.
# The execution path is exercised with stub `gh`/`git` (via
# BSTACK_GRADUATE_GH / BSTACK_GRADUATE_GIT) against a fake source tree to
# verify the copy + exclude logic without touching GitHub.
#
# Run from the bstack repo root:
#   bash tests/skill-graduate.test.sh

set -uo pipefail

BSTACK_REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SKILLS_BIN="$BSTACK_REPO/bin/bstack-skills"
GRADUATE_SH="$BSTACK_REPO/scripts/skill-graduate.sh"

PASS=0
FAIL=0
FAILED_TESTS=()

assert_pass() { PASS=$((PASS + 1)); echo "  [pass] $1"; }
assert_fail() { FAIL=$((FAIL + 1)); FAILED_TESTS+=("$1"); echo "  [FAIL] $1"; [ -n "${2:-}" ] && echo "         $2"; }

echo "── skill-graduate CLI smoke tests ─────────────────────────────────"

# T1: bstack-skills usage advertises graduate
t="bstack-skills --help advertises graduate"
if "$SKILLS_BIN" --help 2>&1 | grep -q 'graduate <name>'; then assert_pass "$t"; else assert_fail "$t"; fi

# T2: graduate --help routes to the script's usage
t="graduate --help routes to script"
if "$SKILLS_BIN" graduate --help 2>&1 | grep -q 'migrate a standalone skill repo'; then assert_pass "$t"; else assert_fail "$t"; fi

# T3: missing <name> exits non-zero
t="missing <name> exits 2"
"$GRADUATE_SH" --category content >/dev/null 2>&1; rc=$?
if [ "$rc" -eq 2 ]; then assert_pass "$t"; else assert_fail "$t" "expected exit 2, got $rc"; fi

# T4: invalid target name exits non-zero
t="invalid target name exits 2"
"$GRADUATE_SH" foo --target "Bad_Name" --dry-run >/dev/null 2>&1; rc=$?
if [ "$rc" -eq 2 ]; then assert_pass "$t"; else assert_fail "$t" "expected exit 2, got $rc"; fi

# T5: unknown option exits non-zero
t="unknown option exits 2"
"$GRADUATE_SH" foo --bogus >/dev/null 2>&1; rc=$?
if [ "$rc" -eq 2 ]; then assert_pass "$t"; else assert_fail "$t" "expected exit 2, got $rc"; fi

# T6: dry-run prints plan + registry entry, exits 0, makes no network calls
t="dry-run prints plan + registry entry"
out=$("$GRADUATE_SH" myskill --category content --description "Does a thing" --dry-run 2>&1); rc=$?
if [ "$rc" -eq 0 ] && echo "$out" | grep -q 'skillPath: skills/myskill/SKILL.md' && echo "$out" | grep -q 'category: content'; then
    assert_pass "$t"
else
    assert_fail "$t" "rc=$rc"
fi

# T7: rename detection in dry-run output
t="rename detection (foo-skill -> foo)"
out=$("$GRADUATE_SH" foo-skill --target foo --dry-run 2>&1)
if echo "$out" | grep -q 'renamed: foo-skill -> foo' && echo "$out" | grep -q 'skillPath: skills/foo/SKILL.md'; then
    assert_pass "$t"
else
    assert_fail "$t"
fi

# Shared stub-builder: writes a gh stub (clone copies a fixture; pr/* are no-ops)
# and a no-op git stub into $1/bin. $2 = source fixture dir.
build_stubs() {
    local bindir="$1" src="$2"
    mkdir -p "$bindir"
    cat > "$bindir/gh" <<STUBGH
#!/usr/bin/env bash
case "\$1" in
  repo) dir="\$4"; if echo "\$3" | grep -q 'broomva/skills'; then mkdir -p "\$dir"; else cp -R "$src/." "\$dir"; fi ;;
  pr)
    # 'gh pr list ...' must return empty (no existing PR) for the idempotency check
    if [ "\$2" = "list" ]; then echo ""; else echo "https://github.com/stub/pr/1"; fi ;;
  *) : ;;
esac
exit 0
STUBGH
    cat > "$bindir/git" <<'STUBGIT'
#!/usr/bin/env bash
exit 0
STUBGIT
    chmod +x "$bindir/gh" "$bindir/git"
}

# T8: execution path — inspect the COPIED TREE (not just exit code)
t="execution copies canonical content + excludes dot-dirs/LICENSE/lock"
WORK="$(mktemp -d)"
SRC_FIXTURE="$WORK/src"
mkdir -p "$SRC_FIXTURE/references" "$SRC_FIXTURE/.claude" "$SRC_FIXTURE/scripts"
printf -- '---\nname: testskill\n---\nbody\n' > "$SRC_FIXTURE/SKILL.md"
echo "ref" > "$SRC_FIXTURE/references/r.md"
echo "scr" > "$SRC_FIXTURE/scripts/s.sh"
echo "MIT" > "$SRC_FIXTURE/LICENSE"
echo "{}" > "$SRC_FIXTURE/skills-lock.json"
echo "mirror" > "$SRC_FIXTURE/.claude/m.md"
build_stubs "$WORK/bin" "$SRC_FIXTURE"
RUN_TMP="$WORK/run"; mkdir -p "$RUN_TMP"
BSTACK_GRADUATE_GH="$WORK/bin/gh" BSTACK_GRADUATE_GIT="$WORK/bin/git" \
  BSTACK_GRADUATE_TMPDIR="$RUN_TMP" BSTACK_GRADUATE_NO_CLEANUP=1 \
  "$GRADUATE_SH" testskill --category knowledge --no-stub >/dev/null 2>&1; rc=$?
DST="$RUN_TMP/monorepo/skills/testskill"
if [ "$rc" -eq 0 ] \
   && [ -f "$DST/SKILL.md" ] \
   && [ -f "$DST/references/r.md" ] \
   && [ -f "$DST/scripts/s.sh" ] \
   && [ ! -e "$DST/.claude" ] \
   && [ ! -e "$DST/LICENSE" ] \
   && [ ! -e "$DST/skills-lock.json" ]; then
    assert_pass "$t"
else
    assert_fail "$t" "rc=$rc; tree: $(ls -A "$DST" 2>/dev/null | tr '\n' ' ')"
fi
rm -rf "$WORK"

# T10: regression — source filename WITH A SPACE must not break the copy loop
t="regression: spaced filename in source copies cleanly (no word-split break)"
WORK="$(mktemp -d)"
SRC_FIXTURE="$WORK/src"; mkdir -p "$SRC_FIXTURE/references"
printf -- '---\nname: spacetest\n---\n' > "$SRC_FIXTURE/SKILL.md"
echo "spaced" > "$SRC_FIXTURE/references/a file with spaces.md"
echo "glob" > "$SRC_FIXTURE/references/star[1].md"
build_stubs "$WORK/bin" "$SRC_FIXTURE"
RUN_TMP="$WORK/run"; mkdir -p "$RUN_TMP"
BSTACK_GRADUATE_GH="$WORK/bin/gh" BSTACK_GRADUATE_GIT="$WORK/bin/git" \
  BSTACK_GRADUATE_TMPDIR="$RUN_TMP" BSTACK_GRADUATE_NO_CLEANUP=1 \
  "$GRADUATE_SH" spacetest --category knowledge --no-stub >/dev/null 2>&1; rc=$?
DST="$RUN_TMP/monorepo/skills/spacetest"
if [ "$rc" -eq 0 ] && [ -f "$DST/references/a file with spaces.md" ] && [ -f "$DST/references/star[1].md" ]; then
    assert_pass "$t"
else
    assert_fail "$t" "rc=$rc — spaced/glob filename broke the copy loop"
fi
rm -rf "$WORK"

# T9: execution fails cleanly when source has no SKILL.md
t="execution errors when no SKILL.md in source"
WORK="$(mktemp -d)"; SRC_FIXTURE="$WORK/src"; mkdir -p "$SRC_FIXTURE"
echo "no skill here" > "$SRC_FIXTURE/README.md"
STUB_BIN="$WORK/bin"; mkdir -p "$STUB_BIN"
cat > "$STUB_BIN/gh" <<STUBGH
#!/usr/bin/env bash
case "\$1" in
  repo) dir="\$4"; if echo "\$3" | grep -q 'skills'; then mkdir -p "\$dir"; else cp -R "$SRC_FIXTURE/." "\$dir"; fi ;;
  pr) echo "stub" ;; *) : ;;
esac
exit 0
STUBGH
cat > "$STUB_BIN/git" <<'STUBGIT'
#!/usr/bin/env bash
exit 0
STUBGIT
chmod +x "$STUB_BIN/gh" "$STUB_BIN/git"
BSTACK_GRADUATE_GH="$STUB_BIN/gh" BSTACK_GRADUATE_GIT="$STUB_BIN/git" BSTACK_GRADUATE_TMPDIR="$WORK/run" \
  "$GRADUATE_SH" emptyskill --no-stub >/dev/null 2>&1; rc=$?
if [ "$rc" -eq 1 ]; then assert_pass "$t"; else assert_fail "$t" "expected exit 1 (no SKILL.md), got $rc"; fi
rm -rf "$WORK"

echo ""
echo "── results: $PASS passed, $FAIL failed ────────────────────────────"
if [ "$FAIL" -gt 0 ]; then
    printf '  failed: %s\n' "${FAILED_TESTS[@]}"
    exit 1
fi
exit 0
