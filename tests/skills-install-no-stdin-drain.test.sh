#!/usr/bin/env bash
# skills-install-no-stdin-drain.test.sh — regression guard for BRO-1588.
#
# cmd_install iterates the roster with `while read ... done < <(parse_roster)`, so
# the loop body's stdin (fd 0) IS the roster pipe. If the per-skill install
# subprocess reads stdin — and the real `npx skills add` CLI does — it drains the
# remaining roster rows off that pipe and the enclosing `read` hits EOF after skill
# #1. Symptom: only 1 of N skills installs, silently ("Installed: 1").
#
# The existing skills-install-uses-skill-flag.test.sh mock does NOT read stdin, so
# it cannot catch this. This test mocks NPX_CMD with a subprocess that DRAINS stdin
# (exactly like the real CLI) and asserts every roster skill is attempted. Without
# the `</dev/null` guard in cmd_install it fails (1 attempt); with it, all N.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BSTACK_SKILLS="$HERE/bin/bstack-skills"

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# Roster: N=4 skills, all broomva/skills, all required.
ROSTER="$TMP/roster.yaml"
cat > "$ROSTER" <<'YAML'
skills:
  - name: alpha
    repo: broomva/skills
    category: meta
    required: true
  - name: bravo
    repo: broomva/skills
    category: meta
    required: true
  - name: charlie
    repo: broomva/skills
    category: meta
    required: true
  - name: delta
    repo: broomva/skills
    category: meta
    required: true
YAML
EXPECTED=4

# Mock npx that DRAINS stdin (reproduces the real `skills add` CLI) and logs each
# invocation's skill name. The `cat >/dev/null` is the whole point — on a stdin-
# unguarded cmd_install it eats the roster pipe.
LOG="$TMP/attempts.log"
MOCK="$TMP/npx-mock.sh"
cat > "$MOCK" <<MOCKEOF
#!/usr/bin/env bash
cat >/dev/null 2>&1   # drain stdin, exactly like the real CLI does
echo "\$*" >> "$LOG"  # args: broomva/skills --skill <name>
exit 0
MOCKEOF
chmod +x "$MOCK"

# Empty HOME store so nothing reads as already-installed → all N attempted.
STORE="$TMP/home"; mkdir -p "$STORE"

HOME="$STORE" BSTACK_DIR="$HERE" BSTACK_SKILLS_YAML="$ROSTER" \
  BSTACK_NPX_CMD="$MOCK" bash "$BSTACK_SKILLS" install >/dev/null 2>&1 || true

ATTEMPTS=0
[ -f "$LOG" ] && ATTEMPTS=$(wc -l < "$LOG" | tr -d ' ')

echo "skills-install-no-stdin-drain: expected $EXPECTED install attempts, got $ATTEMPTS"
if [ "$ATTEMPTS" = "$EXPECTED" ]; then
  echo "  [pass] every roster skill attempted despite a stdin-draining install subprocess"
  echo ""
  echo "skills-install-no-stdin-drain: 1 passed, 0 failed"
  exit 0
else
  echo "  [FAIL] only $ATTEMPTS/$EXPECTED skills attempted — cmd_install stdin drained by the install subprocess (BRO-1588 regression)"
  [ -f "$LOG" ] && { echo "  attempted:"; sed 's/^/    /' "$LOG"; }
  echo ""
  echo "skills-install-no-stdin-drain: 0 passed, 1 failed"
  exit 1
fi
