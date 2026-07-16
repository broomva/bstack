#!/usr/bin/env bash
# plugin-preference.sh — shared bstack-plugin detection/enable helpers (BRO-1929).
#
# WHY: bstack ships as a Claude Code skills-directory plugin (BRO-1926, v0.35.0+):
# a `.claude-plugin/plugin.json` + `hooks/hooks.json` inside the bstack install.
# When bstack lives under a Claude-scanned skills dir (~/.agents/skills/bstack or
# ~/.claude/skills/bstack), Claude Code auto-loads it as `bstack@skills-dir` and
# its six governance hooks fire globally (guarded to no-op outside a governed
# workspace). Those same six hooks were ALSO hand-wired into each workspace's
# `.claude/settings.json` by bootstrap/onboard/repair — so once the plugin is
# enabled, the workspace copies DOUBLE-FIRE (two leverage-sensor writers race;
# l3-stability double-counts the budget).
#
# This lib is the single source of truth that lets the installers PREFER the
# plugin: detect it, enable it, and skip (or migrate away) the six hand-wired
# copies. The plugin path is installer-immune — a `${CLAUDE_PLUGIN_ROOT}`-rooted
# hook survives a skills-CLI reinstall that would clobber a hand-wired absolute
# path.
#
# Usage:
#   source "$BSTACK_DIR/scripts/lib/plugin-preference.sh"
#   if bstack_plugin_preferred; then bstack_enable_plugin; fi
#   # the RCS installers then self-skip the plugin's hooks via bstack_plugin_enabled
#   # (persisted enable-state is the single source of truth — no env coupling)

# Canonical plugin identifier: <name-from-plugin.json>@skills-dir.
BSTACK_PLUGIN_ID="bstack@skills-dir"

# Canonical basenames of the hooks that hooks/hooks.json provides. Kept in sync
# with the bstack repo's hooks/hooks.json — when the plugin is enabled these must
# NOT also be wired into a workspace settings.json. NON-plugin bstack hooks
# (control-gate, conversation-bridge, knowledge-catalog-refresh, skill-freshness,
# auth-preflight, role-x-*) are deliberately absent here — they stay hand-wired.
BSTACK_PLUGIN_HOOK_BASENAMES="bstack-autoupdate-hook.sh knowledge-wakeup-hook.sh autonomous-posture-hook.sh arc-continuation-hook.sh leverage-sensor.py l3-stability-pretool-hook.sh"

# Echo the plugin manifest path if bstack is installed at a Claude-scanned
# skills dir with a plugin manifest; return 1 otherwise. Honors $BSTACK_HOME
# first (the CLI's own resolution root) so an install at a non-standard path
# that IS the loaded skills dir is still found.
bstack_plugin_manifest_path() {
  local d
  for d in "${BSTACK_HOME:-}" "${HOME}/.agents/skills/bstack" "${HOME}/.claude/skills/bstack"; do
    [ -n "$d" ] || continue
    if [ -f "$d/.claude-plugin/plugin.json" ]; then
      printf '%s\n' "$d/.claude-plugin/plugin.json"
      return 0
    fi
  done
  return 1
}

# True when the installers should PREFER the plugin: a manifest is present AND
# the operator hasn't forced the legacy hand-wire path. BSTACK_NO_PLUGIN=1 is the
# escape hatch (old Claude Code < 2.1.154 that ignores defaultEnabled, or a host
# that deliberately hand-wires).
bstack_plugin_preferred() {
  [ "${BSTACK_NO_PLUGIN:-0}" = "1" ] && return 1
  bstack_plugin_manifest_path >/dev/null 2>&1
}

# True when the plugin is actually ENABLED (manifest present AND
# enabledPlugins["bstack@skills-dir"]==true in the personal ~/.claude/settings.json).
# doctor uses THIS (not _preferred): a present-but-disabled plugin does not fire,
# so its hooks are genuinely missing and should still gap. Prints nothing.
# Honors BSTACK_NO_PLUGIN=1 so the legacy-forced path is consistent across the
# installers (bootstrap disables the real plugin under that flag — see
# bstack_disable_plugin — so "not enabled" is then also the ground truth).
bstack_plugin_enabled() {
  [ "${BSTACK_NO_PLUGIN:-0}" = "1" ] && return 1
  bstack_plugin_manifest_path >/dev/null 2>&1 || return 1
  command -v python3 >/dev/null 2>&1 || return 1
  python3 - "$HOME/.claude/settings.json" "$BSTACK_PLUGIN_ID" <<'PYEOF'
import json, sys
from pathlib import Path
settings_path, plugin_id = Path(sys.argv[1]), sys.argv[2]
try:
    data = json.loads(settings_path.read_text())
except (OSError, ValueError):
    sys.exit(1)
sys.exit(0 if data.get("enabledPlugins", {}).get(plugin_id) is True else 1)
PYEOF
}

# True if $1 (a hook command string or bare basename) is provided by the plugin.
# Snippet commands are direct-path form ("/path/to/hook.sh [--flag ...]"). Strip a
# trailing " --flag" tail (mirrors the installers' python base()); splitting on
# " --" (not " -") keeps a hyphen anywhere in the directory path intact.
bstack_plugin_provides_hook() {
  local first base
  first="${1%% --*}"
  base="$(basename "$first")"
  case " $BSTACK_PLUGIN_HOOK_BASENAMES " in
    *" $base "*) return 0 ;;
  esac
  return 1
}

# Idempotently enable bstack@skills-dir in the PERSONAL ~/.claude/settings.json
# (host scope — distinct from a WORKSPACE .claude/settings.json). Additive: only
# touches the enabledPlugins map, preserves everything else, and is a no-op if
# already enabled. Prints a one-line receipt. Returns non-zero only on a hard
# failure (no python3 / unwritable), never on "already enabled".
bstack_enable_plugin() {
  if ! command -v python3 >/dev/null 2>&1; then
    echo "  [skip] plugin enable — python3 not available (enable manually: claude plugin enable $BSTACK_PLUGIN_ID)"
    return 1
  fi
  python3 - "$HOME/.claude/settings.json" "$BSTACK_PLUGIN_ID" <<'PYEOF'
import json, sys
from pathlib import Path
settings_path, plugin_id = Path(sys.argv[1]), sys.argv[2]
if settings_path.exists():
    try:
        data = json.loads(settings_path.read_text())
    except ValueError:
        print(f"  [skip] plugin enable — {settings_path} is not valid JSON; leaving alone")
        sys.exit(1)
else:
    data = {}
enabled = data.setdefault("enabledPlugins", {})
if enabled.get(plugin_id) is True:
    print(f"  [ok]   plugin {plugin_id} already enabled")
    sys.exit(0)
enabled[plugin_id] = True
settings_path.parent.mkdir(parents=True, exist_ok=True)
settings_path.write_text(json.dumps(data, indent=2) + "\n")
print(f"  [enable] {plugin_id} in {settings_path} (host-scope; hooks now fire via the plugin)")
PYEOF
}

# Idempotently DISABLE bstack@skills-dir (explicit enabledPlugins=false, which
# overrides defaultEnabled even on old Claude Code that auto-enables). Used when
# BSTACK_NO_PLUGIN=1 forces the legacy hand-wire path: leaving the plugin enabled
# while hand-wiring its hooks would double-fire — the exact hazard this all
# prevents. No-op if the manifest/settings/python3 are absent or it's already off.
bstack_disable_plugin() {
  command -v python3 >/dev/null 2>&1 || return 0
  [ -f "$HOME/.claude/settings.json" ] || return 0
  python3 - "$HOME/.claude/settings.json" "$BSTACK_PLUGIN_ID" <<'PYEOF'
import json, sys
from pathlib import Path
settings_path, plugin_id = Path(sys.argv[1]), sys.argv[2]
try:
    data = json.loads(settings_path.read_text())
except (OSError, ValueError):
    sys.exit(0)
enabled = data.get("enabledPlugins", {})
if enabled.get(plugin_id) is False or plugin_id not in enabled:
    sys.exit(0)
enabled[plugin_id] = False
settings_path.write_text(json.dumps(data, indent=2) + "\n")
print(f"  [disable] {plugin_id} in {settings_path} (BSTACK_NO_PLUGIN=1 → legacy hand-wire, plugin off)")
PYEOF
}
