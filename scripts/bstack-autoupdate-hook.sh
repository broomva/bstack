#!/usr/bin/env bash
# bstack-autoupdate-hook.sh — SessionStart auto-upgrade for bstack.
#
# Composes with the existing P7 freshness hook: P7 nudges weekly when a
# refresh is overdue; this hook actually performs the refresh when a
# newer release is available.
#
# Behavior:
#   1. Call bin/bstack-update-check (cached, fast).
#   2. If UPGRADE_AVAILABLE and auto-upgrade is not disabled:
#        a. Git installs → run `git stash && git fetch && git reset --hard
#           origin/main` in the background. Next session sees JUST_UPGRADED.
#        b. Vendored installs (no .git) → print guidance, never auto-write
#           (destructive mv+clone path would silently overwrite local edits).
#   3. Always exits 0. Never blocks a session.
#
# Opt-out:
#   BSTACK_AUTO_UPGRADE=0     (env, this session only)
#   bstack-config set auto_upgrade false   (persistent)
#
# Empty `auto_upgrade` config is treated as `true` — this is the new
# default starting in 0.3.0.
set -euo pipefail

_BSTACK_ROOT=""
[ -d "$HOME/.claude/skills/bstack" ] && _BSTACK_ROOT="$HOME/.claude/skills/bstack"
[ -z "$_BSTACK_ROOT" ] && [ -d "$HOME/.agents/skills/bstack" ] && _BSTACK_ROOT="$HOME/.agents/skills/bstack"
[ -z "$_BSTACK_ROOT" ] && exit 0

# Honor opt-out (env wins, then config; empty config = enabled).
if [ "${BSTACK_AUTO_UPGRADE:-}" = "0" ]; then
    exit 0
fi
_CFG=$("$_BSTACK_ROOT/bin/bstack-config" get auto_upgrade 2>/dev/null || true)
if [ "$_CFG" = "false" ]; then
    exit 0
fi

# Fast check (≤ 5s curl, cached).
_UPD=$("$_BSTACK_ROOT/bin/bstack-update-check" 2>/dev/null || true)
case "$_UPD" in
    UPGRADE_AVAILABLE*) ;;
    *) exit 0 ;;
esac

OLD=$(echo "$_UPD" | awk '{print $2}')
NEW=$(echo "$_UPD" | awk '{print $3}')

# Git installs only — vendored upgrade is destructive without user confirm.
if [ ! -d "$_BSTACK_ROOT/.git" ]; then
    echo "[bstack] v$NEW available (you're on v$OLD). Vendored install — run /bstack-upgrade or:"
    echo "         npx skills add -g broomva/bstack"
    exit 0
fi

# Background the upgrade — SessionStart timeout (10s) is tight.
mkdir -p "$HOME/.bstack"
LOG="$HOME/.bstack/auto-upgrade.log"
(
    cd "$_BSTACK_ROOT"
    {
        echo "=== $(date -u +%FT%TZ) auto-upgrade v$OLD → v$NEW ==="
        git stash push -u -m "bstack-autoupdate $(date +%s)" 2>&1 || true
        git fetch origin 2>&1
        git reset --hard origin/main 2>&1
        chmod +x bin/* scripts/* 2>/dev/null || true
        echo "$OLD" > "$HOME/.bstack/just-upgraded-from"
        rm -f "$HOME/.bstack/last-update-check" "$HOME/.bstack/update-snoozed"
        echo "=== done ==="
    } >> "$LOG" 2>&1
) </dev/null >/dev/null 2>&1 &
disown 2>/dev/null || true

echo "[bstack] Auto-upgrading v$OLD → v$NEW in background. Log: $LOG"
echo "         Restart Claude (or open a new session) to pick up the new release."
exit 0
