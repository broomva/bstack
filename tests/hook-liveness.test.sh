#!/usr/bin/env bash
# tests/hook-liveness.test.sh — doctor §26 hook liveness (BRO-2021).
#
# Claude Code loads hooks from THREE registration surfaces (project settings,
# personal ~/.claude/settings.json, plugin hooks/hooks.json). Before §26, bstack
# resolved exactly one of them, only the first absolute-path token, and never the
# ${CLAUDE_PLUGIN_ROOT} form — so a hook could be registered, fire on every
# session, and do nothing, with doctor green.
#
# Every assertion runs against a scratch workspace + scratch HOME. The operator's
# real ~/.claude/settings.json is never read or written by this test (doctor's
# BSTACK_DOCTOR_USER_SETTINGS override exists for exactly this reason).
#
# Asserts:
#   1. clean workspace → §26 reports every surface live, no gap
#   2. dead PROJECT hook → gap
#   3. dead USER hook (surface 2 — previously invisible) → gap
#   4. dead PLUGIN hook via ${CLAUDE_PLUGIN_ROOT} (surface 3) → gap
#   5. wrapper form `bash GUARD.sh python3 INNER.py` → BOTH verified
#   6. self-guarding `if [ -f X ] ... then X` with X ABSENT → NOT flagged
#   7. `sh -c` composite + interpreter-prefixed live hook → NOT flagged
#   8. non-executable: direct invocation → gap; interpreter-invoked → info only
#   9. live hook with a dead INTERNAL reference → advisory info, never a gap
#  10. the advisory paths (6/7/9) do not change --strict exit status
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DOCTOR="$REPO/scripts/doctor.sh"
PASS=0; FAIL=0; FAILED=()
pass() { echo "  [ok] $1"; PASS=$((PASS + 1)); }
fail() { echo "  [FAIL] $1"; FAIL=$((FAIL + 1)); FAILED+=("$1"); }

echo "── hook-liveness / doctor §26 (BRO-2021) ──────────────"

TW=$(mktemp -d)          # workspace under test
TH=$(mktemp -d)          # isolated HOME — the real one is never touched
trap 'rm -rf "$TW" "$TH"' EXIT
mkdir -p "$TW/.control" "$TW/.claude" "$TW/scripts" "$TH/.claude"

live_script() {           # live_script <path> [mode]
    mkdir -p "$(dirname "$1")"
    printf '#!/bin/bash\nexit 0\n' > "$1"
    chmod "${2:-755}" "$1"
}

# settings <file> <json-array-of-commands...> — one SessionStart block per command
write_settings() {
    local out="$1"; shift
    python3 - "$out" "$@" <<'PY'
import json, sys
out, cmds = sys.argv[1], sys.argv[2:]
json.dump({"hooks": {"SessionStart": [
    {"hooks": [{"type": "command", "command": c}]} for c in cmds]}},
    open(out, "w"), indent=2)
PY
}

doc() {                   # doc [extra env assignments applied by caller]
    HOME="$TH" BROOMVA_WORKSPACE="$TW" \
    BSTACK_DOCTOR_USER_SETTINGS="${USER_SETTINGS:-$TH/.claude/settings.json}" \
        bash "$DOCTOR" 2>&1
}
sec26() { doc | sed -n '/^26\. Hook liveness/,/^$/p'; }

# ── 1. clean baseline ────────────────────────────────────────────────────────
live_script "$TW/scripts/alpha-hook.sh"
live_script "$TW/scripts/beta-hook.sh"
write_settings "$TW/.claude/settings.json" "$TW/scripts/alpha-hook.sh" "bash $TW/scripts/beta-hook.sh"
write_settings "$TH/.claude/settings.json" "$TW/scripts/alpha-hook.sh"
BASE=$(sec26)
if echo "$BASE" | grep -q '\[ok\] project .claude/settings.json — 2 hook entries, 2 script(s), all live' \
   && echo "$BASE" | grep -q '\[ok\] user ~/.claude/settings.json — 1 hook entries, 1 script(s), all live' \
   && echo "$BASE" | grep -q '\[ok\] plugin .*hooks/hooks.json'; then
    pass "1. clean run: all three surfaces resolved + reported live"
else
    fail "1. clean run did not report all three surfaces live:"$'\n'"$BASE"
fi
BASE_GAPS=$(doc | sed -n 's/^\[bstack doctor\].*, \([0-9]*\) gap(s).*/\1/p')
[ -z "$BASE_GAPS" ] && BASE_GAPS=0
gaps_now() { local g; g=$(doc | sed -n 's/^\[bstack doctor\].*, \([0-9]*\) gap(s).*/\1/p'); echo "${g:-0}"; }

# ── 2. dead hook on the PROJECT surface ──────────────────────────────────────
write_settings "$TW/.claude/settings.json" "$TW/scripts/alpha-hook.sh" "$TW/scripts/ghost-project.sh"
if sec26 | grep -q '\[gap\] dead hook on project .claude/settings.json.*ghost-project.sh (missing)'; then
    pass "2. dead PROJECT hook is gapped"
else
    fail "2. dead PROJECT hook not gapped:"$'\n'"$(sec26)"
fi

# ── 3. dead hook on the USER surface (the surface nothing checked) ───────────
write_settings "$TW/.claude/settings.json" "$TW/scripts/alpha-hook.sh"
write_settings "$TH/.claude/settings.json" "$TH/ghost-user.sh"
if sec26 | grep -q '\[gap\] dead hook on user ~/.claude/settings.json.*ghost-user.sh (missing)'; then
    pass "3. dead USER hook is gapped (surface 2 now covered)"
else
    fail "3. dead USER hook not gapped:"$'\n'"$(sec26)"
fi
# the override itself must be load-bearing: point it elsewhere, gap disappears
write_settings "$TH/clean-user.json" "$TW/scripts/alpha-hook.sh"
if USER_SETTINGS="$TH/clean-user.json" sec26 | grep -q 'ghost-user.sh'; then
    fail "3b. BSTACK_DOCTOR_USER_SETTINGS override ignored (real user file would be read)"
else
    pass "3b. BSTACK_DOCTOR_USER_SETTINGS redirects surface 2 (tests never touch the live file)"
fi
write_settings "$TH/.claude/settings.json" "$TW/scripts/alpha-hook.sh"

# ── 4. dead hook on the PLUGIN surface, via ${CLAUDE_PLUGIN_ROOT} ────────────
PLUG="$TH/.agents/skills/bstack"
mkdir -p "$PLUG/.claude-plugin" "$PLUG/hooks" "$PLUG/scripts"
echo '{"name":"bstack"}' > "$PLUG/.claude-plugin/plugin.json"
live_script "$PLUG/scripts/plugin-live.sh"
python3 - "$PLUG/hooks/hooks.json" <<'PY'
import json, sys
json.dump({"hooks": {"SessionStart": [{"hooks": [
    {"type": "command", "command": 'bash "${CLAUDE_PLUGIN_ROOT}/scripts/plugin-live.sh"'},
    {"type": "command", "command": 'bash "${CLAUDE_PLUGIN_ROOT}/scripts/plugin-ghost.sh"'},
]}]}}, open(sys.argv[1], "w"), indent=2)
PY
P4=$(sec26)
if echo "$P4" | grep -q '\[gap\] dead hook on plugin .*plugin-ghost.sh (missing)' \
   && ! echo "$P4" | grep -q 'plugin-live.sh'; then
    pass "4. dead PLUGIN hook gapped + \${CLAUDE_PLUGIN_ROOT} expanded (live sibling not flagged)"
else
    fail "4. plugin surface / \${CLAUDE_PLUGIN_ROOT} handling wrong:"$'\n'"$P4"
fi
rm -rf "$TH/.agents"

# ── 5. wrapper form: two scripts in one command, both verified ───────────────
live_script "$TW/scripts/guard.sh"
write_settings "$TW/.claude/settings.json" \
    "bash $TW/scripts/guard.sh python3 $TW/scripts/ghost-inner.py --throttle 21600"
if sec26 | grep -q 'dead hook on project .*ghost-inner.py (missing)'; then
    pass "5. wrapper form: the SECOND script in the command is verified too"
else
    fail "5. wrapper form: inner script not verified:"$'\n'"$(sec26)"
fi

# ── 6. self-guarding inline conditional with an ABSENT target → no gap ───────
GUARDED="$TH/.orca/agent-hooks/claude-hook.sh"      # deliberately never created
write_settings "$TW/.claude/settings.json" "$TW/scripts/alpha-hook.sh" \
    "if [ -f '$GUARDED' ] && [ -r '$GUARDED' ] && [ -x '$GUARDED' ]; then /bin/sh '$GUARDED'; else cat >/dev/null 2>&1 || :; fi"
S6=$(sec26)
if echo "$S6" | grep -q 'claude-hook.sh'; then
    fail "6. FALSE POSITIVE: self-guarding inline conditional flagged:"$'\n'"$S6"
elif echo "$S6" | grep -q 'self-guarded (intended no-op)'; then
    pass "6. self-guarding inline conditional with an absent target is not a defect"
else
    fail "6. self-guarded command was not accounted for:"$'\n'"$S6"
fi

# ── 7. exotic-but-fine forms are not flagged ────────────────────────────────
write_settings "$TW/.claude/settings.json" "sh -c 'echo hi'" "python3 $TW/scripts/alpha-hook.sh --flag"
S7=$(sec26)
if echo "$S7" | grep -q '\[gap\]'; then
    fail "7. FALSE POSITIVE on sh -c composite / interpreter-prefixed live hook:"$'\n'"$S7"
else
    pass "7. sh -c composite + interpreter-prefixed live hook are not flagged"
fi

# ── 8. executability: direct invocation vs interpreter-invoked ──────────────
live_script "$TW/scripts/noexec.sh" 644
write_settings "$TW/.claude/settings.json" "$TW/scripts/noexec.sh"
if sec26 | grep -q 'dead hook on project .*noexec.sh (not executable)'; then
    pass "8a. directly-invoked non-executable script is gapped"
else
    fail "8a. directly-invoked non-executable script not gapped:"$'\n'"$(sec26)"
fi
write_settings "$TW/.claude/settings.json" "bash $TW/scripts/noexec.sh"
S8=$(sec26)
if echo "$S8" | grep -q '\[gap\].*noexec.sh'; then
    fail "8b. FALSE POSITIVE: interpreter-invoked script gapped for a mode bit that does not matter"
elif echo "$S8" | grep -q '\[info\].*noexec.sh — not executable'; then
    pass "8b. interpreter-invoked non-executable script is info, not a gap"
else
    fail "8b. interpreter-invoked non-executable script not reported at all:"$'\n'"$S8"
fi

# ── 9. live hook, dead INTERNAL reference (the 8-day-silent class) ──────────
cat > "$TW/scripts/inner-ref-hook.sh" <<'EOS'
#!/bin/bash
ROOT="${SOME_ROOT:-${HOME}/broomva}"
TOOL_PY="${ROOT}/skills/bookkeeping/scripts/bookkeeping.py"
[ -f "$TOOL_PY" ] || exit 0
python3 "$TOOL_PY" index
EOS
chmod 755 "$TW/scripts/inner-ref-hook.sh"
write_settings "$TW/.claude/settings.json" "$TW/scripts/inner-ref-hook.sh"
S9=$(sec26)
if echo "$S9" | grep -q 'live hook .*inner-ref-hook.sh references a dead path via \$TOOL_PY'; then
    pass "9. live hook with a dead internal reference is surfaced"
else
    fail "9. dead internal reference not surfaced:"$'\n'"$S9"
fi
if echo "$S9" | grep -q '\[gap\]'; then
    fail "9b. inner-reference finding wrongly counted as a gap (it is heuristic → advisory)"
else
    pass "9b. inner-reference finding is advisory, not a gap"
fi

# ── 10. advisory paths never change --strict status ─────────────────────────
# Baseline settings (all live) vs the advisory-heavy variant must yield the same
# gap total — the check must not redden a healthy workspace.
write_settings "$TW/.claude/settings.json" "$TW/scripts/alpha-hook.sh"
CLEAN_GAPS=$(gaps_now)
write_settings "$TW/.claude/settings.json" "$TW/scripts/alpha-hook.sh" \
    "$TW/scripts/inner-ref-hook.sh" "sh -c 'echo hi'" \
    "if [ -f '$GUARDED' ]; then /bin/sh '$GUARDED'; else cat >/dev/null; fi"
ADVISORY_GAPS=$(gaps_now)
if [ "$CLEAN_GAPS" = "$ADVISORY_GAPS" ]; then
    pass "10. advisory findings (guarded / composite / inner-ref) add 0 gaps ($CLEAN_GAPS = $ADVISORY_GAPS)"
else
    fail "10. advisory findings changed the gap total ($CLEAN_GAPS → $ADVISORY_GAPS)"
fi

echo "─────────────────────────────────────"
echo "  Passed: $PASS"
echo "  Failed: $FAIL"
if [ "$FAIL" -gt 0 ]; then
    for t in "${FAILED[@]}"; do echo "    - $t"; done
    exit 1
fi
echo "  hook-liveness passed."
