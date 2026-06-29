#!/usr/bin/env bash
# skills-install-uses-skill-flag.test.sh — installer contract guard.
# Every roster entry now points at the broomva/skills monorepo (BRO-1584). The
# installer MUST therefore install each with `--skill <name>` — a bare
# `npx skills add broomva/skills` would pull the entire monorepo. This test mocks
# the npx invocation and asserts every install carries --skill <name>.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PASS=0; FAIL=0
ok()   { PASS=$((PASS + 1)); echo "  [pass] $1"; }
fail() { FAIL=$((FAIL + 1)); echo "  [FAIL] $1"; }

LOG="$(mktemp)"; MOCK="$(mktemp)"
cat > "$MOCK" <<'M'
#!/usr/bin/env bash
echo "$@" >> "$NPX_LOG"
M
chmod +x "$MOCK"

NPX_LOG="$LOG" BSTACK_DIR="$HERE" BSTACK_NPX_CMD="$MOCK" \
  "$HERE/bin/bstack-skills" install --all >/dev/null 2>&1 || true

TOTAL="$(wc -l < "$LOG" | tr -d ' ')"
if [ "$TOTAL" -gt 0 ]; then ok "installer attempted $TOTAL skills"; else fail "installer made no invocations"; fi

# every invocation that targets broomva/skills must carry --skill
BARE="$(grep -E '(^| )broomva/skills( |$)' "$LOG" | grep -vc -- '--skill' || true)"
if [ "${BARE:-0}" -eq 0 ]; then
  ok "every broomva/skills install carries --skill <name> (no wholesale-monorepo install)"
else
  fail "$BARE install(s) hit broomva/skills without --skill:"; grep -E '(^| )broomva/skills( |$)' "$LOG" | grep -v -- '--skill' | head
fi

# every install must be global (-g) so it lands in ~/.{agents,claude}/skills, where
# `status` looks. Without -g the skills CLI installs cwd-relative (./.agents/skills),
# invisible to status — an install↔status mismatch. (BRO-1588)
NONGLOBAL="$(grep -E '(^| )broomva/skills( |$)' "$LOG" | grep -vcE '(^| )-g( |$)' || true)"
if [ "${NONGLOBAL:-0}" -eq 0 ]; then
  ok "every broomva/skills install carries -g (global install, status-visible)"
else
  fail "$NONGLOBAL install(s) hit broomva/skills without -g (would land cwd-relative, invisible to status):"; grep -E '(^| )broomva/skills( |$)' "$LOG" | grep -vE '(^| )-g( |$)' | head
fi

rm -f "$LOG" "$MOCK"
echo ""
echo "skills-install-uses-skill-flag: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
