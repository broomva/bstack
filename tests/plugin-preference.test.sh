#!/usr/bin/env bash
# tests/plugin-preference.test.sh — bstack-plugin preference at install (BRO-1929).
#
# When bstack is installed as a skills-dir plugin (bstack@skills-dir), the six
# hooks it provides (bstack-autoupdate, knowledge-wakeup, autonomous-posture,
# arc-continuation, leverage-sensor, l3-stability-pretool) must NOT also be
# hand-wired into a workspace .claude/settings.json — that double-fires. bootstrap
# / repair now PREFER the plugin: enable it host-scope, skip those six, and
# migrate away any already-wired copy. Non-plugin hooks (control-gate,
# conversation-bridge, knowledge-catalog-refresh, skill-freshness, auth-preflight,
# role-x-*) stay hand-wired.
#
# Validates:
#   Lib:   1. manifest present → preferred; absent → not; BSTACK_NO_PLUGIN=1 → not
#          2. provides_hook: the 6 plugin hooks true; non-plugin hooks false
#          3. enable writes enabledPlugins["bstack@skills-dir"]=true, idempotent
#          4. plugin_enabled: false before enable, true after
#   Boot:  5. preferred install skips the 4 snippet plugin hooks + wires non-plugin + enables
#          6. migration: a pre-wired plugin hook is stripped; a non-plugin hook is kept
#          7. fallback (no manifest): all hooks incl. plugin ones are wired

set -uo pipefail

BSTACK_REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LIB="$BSTACK_REPO/scripts/lib/plugin-preference.sh"
BOOTSTRAP="$BSTACK_REPO/scripts/bootstrap.sh"

PASS=0
FAIL=0
FAILED_TESTS=()
assert_pass() { PASS=$((PASS + 1)); echo "  ✓ $1"; }
assert_fail() { FAIL=$((FAIL + 1)); FAILED_TESTS+=("$1"); echo "  ✗ $1"; }

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# Fresh HOME with a fake plugin manifest → the plugin is "installed".
make_home_with_plugin() {
    local h="$1"
    mkdir -p "$h/.agents/skills/bstack/.claude-plugin"
    printf '{"name":"bstack"}\n' > "$h/.agents/skills/bstack/.claude-plugin/plugin.json"
}

# ── Lib unit tests ─────────────────────────────────────────────────────────
unset BSTACK_HOME BSTACK_NO_PLUGIN 2>/dev/null || true
# shellcheck source=scripts/lib/plugin-preference.sh
. "$LIB"

# 1. detection
H_ON="$TMP/home_on"; make_home_with_plugin "$H_ON"
H_OFF="$TMP/home_off"; mkdir -p "$H_OFF"
if HOME="$H_ON" bstack_plugin_preferred; then
    assert_pass "detect: manifest present → preferred"
else
    assert_fail "detect: manifest present → preferred (was not)"
fi
if HOME="$H_OFF" bstack_plugin_preferred; then
    assert_fail "detect: no manifest → not preferred (was)"
else
    assert_pass "detect: no manifest → not preferred"
fi
if HOME="$H_ON" BSTACK_NO_PLUGIN=1 bstack_plugin_preferred; then
    assert_fail "detect: BSTACK_NO_PLUGIN=1 forces legacy (did not)"
else
    assert_pass "detect: BSTACK_NO_PLUGIN=1 forces legacy path"
fi

# 2. provides_hook — the six plugin hooks (incl. args + absolute paths) vs non-plugin
_all_plugin=1
for cmd in \
    "/x/scripts/bstack-autoupdate-hook.sh" \
    "/x/scripts/knowledge-wakeup-hook.sh" \
    "/x/scripts/autonomous-posture-hook.sh" \
    "/x/scripts/arc-continuation-hook.sh" \
    "/x/scripts/leverage-sensor.py --throttle 21600" \
    "l3-stability-pretool-hook.sh"; do
    bstack_plugin_provides_hook "$cmd" || _all_plugin=0
done
if [ "$_all_plugin" = "1" ]; then
    assert_pass "provides_hook: all 6 plugin hooks recognized (incl. args/paths)"
else
    assert_fail "provides_hook: a plugin hook was NOT recognized"
fi
_any_nonplugin=0
for cmd in \
    "\${BROOMVA_WORKSPACE}/scripts/control-gate-hook.sh" \
    "/x/scripts/auth-preflight-hook.sh" \
    "/x/scripts/conversation-bridge-hook.sh" \
    "/x/scripts/skill-freshness-hook.sh"; do
    bstack_plugin_provides_hook "$cmd" && _any_nonplugin=1
done
if [ "$_any_nonplugin" = "0" ]; then
    assert_pass "provides_hook: non-plugin hooks correctly excluded"
else
    assert_fail "provides_hook: a non-plugin hook was wrongly matched"
fi

# 3 + 4. enable + enabled
H_EN="$TMP/home_enable"; make_home_with_plugin "$H_EN"
if HOME="$H_EN" bstack_plugin_enabled; then
    assert_fail "enabled: false before enable (was true)"
else
    assert_pass "enabled: false before enable"
fi
HOME="$H_EN" bstack_enable_plugin >/dev/null
if HOME="$H_EN" bstack_plugin_enabled; then
    assert_pass "enable → enabled: enabledPlugins[bstack@skills-dir]=true written"
else
    assert_fail "enable → enabled: not enabled after bstack_enable_plugin"
fi
# idempotent — second enable must not duplicate / must stay valid JSON == true
HOME="$H_EN" bstack_enable_plugin >/dev/null
_val="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["enabledPlugins"]["bstack@skills-dir"])' "$H_EN/.claude/settings.json" 2>/dev/null || echo ERR)"
if [ "$_val" = "True" ]; then
    assert_pass "enable: idempotent (still exactly true after 2nd call)"
else
    assert_fail "enable: idempotent (got '$_val')"
fi

# ── Bootstrap integration tests ────────────────────────────────────────────
run_bootstrap() {  # $1=HOME $2=WORKSPACE
    HOME="$1" BROOMVA_WORKSPACE="$2" BSTACK_SKIP_SKILLS=1 BSTACK_SKIP_RCS=1 \
        bash "$BOOTSTRAP" >/dev/null 2>&1
}
plugin_hooks_in() {  # count of the 4 snippet-provided plugin hooks in a settings.json
    [ -f "$1" ] || { echo 0; return; }
    # grep -Ec prints "0" AND exits 1 on zero matches — swallow the exit, keep the count.
    grep -Ec 'bstack-autoupdate-hook\.sh|knowledge-wakeup-hook\.sh|autonomous-posture-hook\.sh|arc-continuation-hook\.sh' "$1" 2>/dev/null || true
}

# 5. preferred install
H5="$TMP/h5"; make_home_with_plugin "$H5"; W5="$TMP/w5"; mkdir -p "$W5"
run_bootstrap "$H5" "$W5"
S5="$W5/.claude/settings.json"
if [ "$(plugin_hooks_in "$S5")" = "0" ] \
   && grep -q 'control-gate-hook.sh' "$S5" \
   && grep -q 'conversation-bridge-hook.sh' "$S5" \
   && grep -q 'knowledge-catalog-refresh-hook.sh' "$S5" \
   && grep -q 'skill-freshness-hook.sh' "$S5"; then
    assert_pass "bootstrap(preferred): 0 plugin hooks wired, non-plugin hooks present"
else
    assert_fail "bootstrap(preferred): plugin hooks=$(plugin_hooks_in "$S5"), non-plugin presence off"
fi
if HOME="$H5" bstack_plugin_enabled; then
    assert_pass "bootstrap(preferred): enabled bstack@skills-dir host-scope"
else
    assert_fail "bootstrap(preferred): did not enable the plugin"
fi

# 6. migration — a pre-wired plugin hook is stripped; a non-plugin hook survives
H6="$TMP/h6"; make_home_with_plugin "$H6"; W6="$TMP/w6"; mkdir -p "$W6/.claude"
cat > "$W6/.claude/settings.json" <<'JSON'
{
  "hooks": {
    "Stop": [
      { "hooks": [ { "type": "command", "command": "/legacy/scripts/arc-continuation-hook.sh" } ] },
      { "hooks": [ { "type": "command", "command": "/legacy/scripts/conversation-bridge-hook.sh" } ] }
    ]
  }
}
JSON
run_bootstrap "$H6" "$W6"
S6="$W6/.claude/settings.json"
if ! grep -q 'arc-continuation-hook.sh' "$S6" && grep -q 'conversation-bridge-hook.sh' "$S6"; then
    assert_pass "bootstrap(migration): legacy plugin hook stripped, non-plugin hook kept"
else
    assert_fail "bootstrap(migration): arc-continuation still present or bridge lost"
fi

# 7. fallback — no manifest → the plugin hooks ARE hand-wired (legacy path)
H7="$TMP/h7"; mkdir -p "$H7"; W7="$TMP/w7"; mkdir -p "$W7"
run_bootstrap "$H7" "$W7"
S7="$W7/.claude/settings.json"
if [ "$(plugin_hooks_in "$S7")" -ge 4 ] && ! HOME="$H7" bstack_plugin_enabled; then
    assert_pass "bootstrap(fallback): no manifest → plugin hooks hand-wired, nothing enabled"
else
    assert_fail "bootstrap(fallback): plugin hooks=$(plugin_hooks_in "$S7") (want ≥4) or spuriously enabled"
fi

# control-gate (P2 Gate) is wired under 3 PreToolUse matchers — the merge dedup
# must be matcher-scoped or Write/Edit collapse into Bash (P2 safety regression).
control_gate_matchers() {  # sorted, space-joined matchers whose block wires control-gate
    python3 - "$1" <<'PYX'
import json, sys
d = json.load(open(sys.argv[1]))
ms = sorted({b.get("matcher","") for b in d.get("hooks", {}).get("PreToolUse", [])
             if any("control-gate-hook.sh" in h.get("command","") for h in b.get("hooks", []))})
print(" ".join(ms))
PYX
}
# 8. preferred path preserves all 3 matchers
if [ "$(control_gate_matchers "$S5")" = "Bash Edit Write" ]; then
    assert_pass "bootstrap(preferred): control-gate wired under all 3 matchers (Bash/Edit/Write)"
else
    assert_fail "bootstrap(preferred): control-gate matchers = '$(control_gate_matchers "$S5")' (want 'Bash Edit Write')"
fi
# 9. fallback path preserves all 3 matchers
if [ "$(control_gate_matchers "$S7")" = "Bash Edit Write" ]; then
    assert_pass "bootstrap(fallback): control-gate wired under all 3 matchers (Bash/Edit/Write)"
else
    assert_fail "bootstrap(fallback): control-gate matchers = '$(control_gate_matchers "$S7")' (want 'Bash Edit Write')"
fi

# 10. BSTACK_NO_PLUGIN=1 with a manifest present + plugin already enabled →
# bootstrap turns the plugin OFF and hand-wires (no double-fire).
H10="$TMP/h10"; make_home_with_plugin "$H10"
HOME="$H10" bstack_enable_plugin >/dev/null   # pre-enable it
W10="$TMP/w10"; mkdir -p "$W10"
HOME="$H10" BROOMVA_WORKSPACE="$W10" BSTACK_NO_PLUGIN=1 BSTACK_SKIP_SKILLS=1 BSTACK_SKIP_RCS=1 \
    bash "$BOOTSTRAP" >/dev/null 2>&1
S10="$W10/.claude/settings.json"
_enabled_after="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["enabledPlugins"]["bstack@skills-dir"])' "$H10/.claude/settings.json" 2>/dev/null || echo ERR)"
if [ "$(plugin_hooks_in "$S10")" -ge 4 ] && [ "$_enabled_after" = "False" ]; then
    assert_pass "bootstrap(BSTACK_NO_PLUGIN): plugin disabled + hooks hand-wired (no double-fire)"
else
    assert_fail "bootstrap(BSTACK_NO_PLUGIN): hooks=$(plugin_hooks_in "$S10") (want ≥4), enabled_after='$_enabled_after' (want False)"
fi

# 11. base() must not truncate on a hyphen in the DIRECTORY path (a space-then-dash
# path like ".../my repo - v2/ws" collapsed sibling Stop hooks under the old regex).
H11="$TMP/h11"; mkdir -p "$H11"; W11="$TMP/my repo - v2/ws"; mkdir -p "$W11"
run_bootstrap "$H11" "$W11"
S11="$W11/.claude/settings.json"
if grep -q 'conversation-bridge-hook.sh' "$S11" \
   && grep -q 'knowledge-catalog-refresh-hook.sh' "$S11" \
   && grep -q 'skill-freshness-hook.sh' "$S11"; then
    assert_pass "bootstrap(space-dash path): sibling Stop/SessionStart hooks all survive"
else
    assert_fail "bootstrap(space-dash path): a non-plugin hook was truncated/lost by base()"
fi

echo ""
echo "plugin-preference: $PASS passed, $FAIL failed"
if [ "$FAIL" -gt 0 ]; then
    printf '  FAILED: %s\n' "${FAILED_TESTS[@]}"
    exit 1
fi
exit 0
