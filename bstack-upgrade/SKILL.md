---
name: bstack-upgrade
version: 1.0.0
description: |
  Upgrade bstack to the latest version. Detects global vs vendored install,
  runs the upgrade, and shows what's new.
allowed-tools:
  - Bash
  - Read
  - Write
  - AskUserQuestion
---

# /bstack-upgrade

Upgrade bstack to the latest version and show what's new.

## Inline upgrade flow

This section is referenced by the bstack SKILL.md preamble when it detects `UPGRADE_AVAILABLE`.

### Step 1: Ask the user (or auto-upgrade)

First, check if auto-upgrade is enabled:
```bash
_BSTACK_ROOT="${BSTACK_DIR:-$HOME/.claude/skills/bstack}"
[ ! -x "$_BSTACK_ROOT/bin/bstack-config" ] && _BSTACK_ROOT="$HOME/.agents/skills/bstack"
_AUTO=""
[ "${BSTACK_AUTO_UPGRADE:-}" = "1" ] && _AUTO="true"
[ -z "$_AUTO" ] && _AUTO=$("$_BSTACK_ROOT/bin/bstack-config" get auto_upgrade 2>/dev/null || true)
echo "AUTO_UPGRADE=$_AUTO"
```

**If `AUTO_UPGRADE=true` or `AUTO_UPGRADE=1`:** Skip AskUserQuestion. Log "Auto-upgrading bstack v{old} → v{new}..." and proceed directly to Step 2.

**Otherwise**, use AskUserQuestion:
- Question: "bstack **v{new}** is available (you're on v{old}). Upgrade now?"
- Options: ["Yes, upgrade now", "Always keep me up to date", "Not now", "Never ask again"]

**If "Yes, upgrade now":** Proceed to Step 2.

**If "Always keep me up to date":**
```bash
"$_BSTACK_ROOT/bin/bstack-config" set auto_upgrade true
```
Tell user: "Auto-upgrade enabled. Future updates will install automatically." Then proceed to Step 2.

**If "Not now":** Write snooze state with escalating backoff (first snooze = 24h, second = 48h, third+ = 1 week), then continue with the current skill.
```bash
_SNOOZE_FILE=~/.bstack/update-snoozed
_REMOTE_VER="{new}"
_CUR_LEVEL=0
if [ -f "$_SNOOZE_FILE" ]; then
  _SNOOZED_VER=$(awk '{print $1}' "$_SNOOZE_FILE")
  if [ "$_SNOOZED_VER" = "$_REMOTE_VER" ]; then
    _CUR_LEVEL=$(awk '{print $2}' "$_SNOOZE_FILE")
    case "$_CUR_LEVEL" in *[!0-9]*) _CUR_LEVEL=0 ;; esac
  fi
fi
_NEW_LEVEL=$((_CUR_LEVEL + 1))
[ "$_NEW_LEVEL" -gt 3 ] && _NEW_LEVEL=3
echo "$_REMOTE_VER $_NEW_LEVEL $(date +%s)" > "$_SNOOZE_FILE"
```
Note: `{new}` is the remote version from the `UPGRADE_AVAILABLE` output — substitute it from the update check result.

Tell user the snooze duration: "Next reminder in 24h" (or 48h or 1 week, depending on level).

**If "Never ask again":**
```bash
"$_BSTACK_ROOT/bin/bstack-config" set update_check false
```
Tell user: "Update checks disabled. Run `bstack-config set update_check true` to re-enable."
Continue with the current skill.

### Step 2: Detect install type

```bash
_BSTACK_ROOT=""
if [ -d "$HOME/.claude/skills/bstack/.git" ]; then
  INSTALL_TYPE="global-git"
  _BSTACK_ROOT="$HOME/.claude/skills/bstack"
elif [ -d "$HOME/.agents/skills/bstack/.git" ]; then
  INSTALL_TYPE="agents-git"
  _BSTACK_ROOT="$HOME/.agents/skills/bstack"
elif [ -d ".claude/skills/bstack/.git" ]; then
  INSTALL_TYPE="local-git"
  _BSTACK_ROOT=".claude/skills/bstack"
elif [ -d "$HOME/.claude/skills/bstack" ]; then
  INSTALL_TYPE="vendored-global"
  _BSTACK_ROOT="$HOME/.claude/skills/bstack"
elif [ -d "$HOME/.agents/skills/bstack" ]; then
  INSTALL_TYPE="vendored-agents"
  _BSTACK_ROOT="$HOME/.agents/skills/bstack"
else
  echo "ERROR: bstack not found"
  exit 1
fi
echo "Install type: $INSTALL_TYPE at $_BSTACK_ROOT"
```

### Step 3: Save old version

```bash
OLD_VERSION=$(cat "$_BSTACK_ROOT/VERSION" 2>/dev/null || echo "unknown")
```

### Step 4: Upgrade

**For git installs** (global-git, agents-git, local-git):
```bash
cd "$_BSTACK_ROOT"
STASH_OUTPUT=$(git stash 2>&1)
git fetch origin
git reset --hard origin/main
chmod +x bin/* scripts/* 2>/dev/null || true
```
If `$STASH_OUTPUT` contains "Saved working directory", warn the user.

**For vendored installs:**
```bash
PARENT=$(dirname "$_BSTACK_ROOT")
TMP_DIR=$(mktemp -d)
git clone --depth 1 https://github.com/broomva/bstack.git "$TMP_DIR/bstack"
mv "$_BSTACK_ROOT" "$_BSTACK_ROOT.bak"
mv "$TMP_DIR/bstack" "$_BSTACK_ROOT"
chmod +x "$_BSTACK_ROOT/bin/"* "$_BSTACK_ROOT/scripts/"* 2>/dev/null || true
rm -rf "$_BSTACK_ROOT.bak" "$TMP_DIR"
```

### Step 5: Write marker + clear cache

```bash
mkdir -p ~/.bstack
echo "$OLD_VERSION" > ~/.bstack/just-upgraded-from
rm -f ~/.bstack/last-update-check
rm -f ~/.bstack/update-snoozed
```

### Step 6: Show What's New

Read `$_BSTACK_ROOT/CHANGELOG.md` if it exists. Otherwise check `git log --oneline $OLD_VERSION..HEAD` for changes. Summarize as 3-5 bullets. Format:

```
bstack v{new} — upgraded from v{old}!

What's new:
- [bullet 1]
- [bullet 2]
- ...
```

### Step 7: Continue

After showing What's New, continue with whatever skill the user originally invoked.

---

## Standalone usage

When invoked directly as `/bstack-upgrade` (not from a preamble):

1. Force a fresh update check:
```bash
~/.claude/skills/bstack/bin/bstack-update-check --force 2>/dev/null || ~/.agents/skills/bstack/bin/bstack-update-check --force 2>/dev/null || true
```

2. If `UPGRADE_AVAILABLE <old> <new>`: follow Steps 2-6 above.
3. If no output: tell the user "You're already on the latest version."
