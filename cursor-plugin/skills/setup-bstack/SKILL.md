---
name: setup-bstack
description: >-
  Write ~/.cursor/rules/bstack.mdc so hard gates stay visible on hosts without
  Claude PreToolUse hooks. Use for /setup-bstack, "install bstack rules", or
  when a Cursor session should remember no force-push, no reset --hard, and
  human-only merges.
---

# Setup bstack

Write `~/.cursor/rules/bstack.mdc`, an always-applied reminder of the hard gates. This skill does not choose models or reasoning budgets.

The Claude Code plugin already enforces the same gates with PreToolUse hooks. This rule is for hosts that did not load those hooks. The copy shipped in the plugin, `cursor-plugin/rules/bstack-hard-gates.mdc`, stays `alwaysApply: false` so installing the plugin does not inject it into every chat.

## Steps

1. If `~/.cursor/rules/bstack.mdc` already exists, read it and show the current body. Overwrite only after the user confirms. With no file, write it.
2. Create `~/.cursor/rules/` when that directory is missing.
3. Prefer the plugin rule as the source. Read `cursor-plugin/rules/bstack-hard-gates.mdc` from the bstack checkout (or the installed plugin root) and write the same body with `alwaysApply: true`. When that file is not on disk, write the fallback below exactly.
4. Tell the user to reload the window so the rule loads.

## Fallback rule

```markdown
---
description: bstack hard gates. No force-push to main or master, no git reset --hard, human-only merges stay human.
alwaysApply: true
---

# bstack hard gates

The Claude Code plugin enforces these in PreToolUse. This rule is the reminder on hosts that did not load those hooks.

- Do not force-push `main` or `master` (`git push --force`, `git push -f`, and `--force-with-lease`).
- Do not run `git reset --hard`.
- Do not merge human-only paths. Leave the merge for a human when the diff touches any of:
  - `.control/policy.yaml`
  - settings (`.claude/settings.json`, `.cursor/mcp.json`, `.vscode/settings.json`)
  - merge-gate or L3 workflows
  - control hooks (`hooks/`, `scripts/*-hook.sh`)
  - nested `CLAUDE.md` or `AGENTS.md`
  - RCS parameters (`.control/rcs-parameters.toml`)
```
