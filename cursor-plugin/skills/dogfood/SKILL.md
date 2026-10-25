---
name: dogfood
description: >-
  Use before claiming user-visible work is done (P11): dogfood plan, exercise
  the change by interacting, and keep a receipt — not reasoning-only validation.
---

# dogfood — P11 empirical feedback

Before claiming done on user-visible work: interact with the running thing and keep a receipt. Reasoning is not validation.

## Sequence

1. **Detect the stack** from repo signals (or an override): tauri-sidecar, nextjs, expo-rn, rust-cli, rest-api, mcp-server, unknown.
2. **Plan** (six rows): entry surface, driver, evidence, smoke, end-to-end, receipt anchor.
3. **Execute** the named surfaces (browser, curl, CLI, or simulator, whichever fits). Do not skip a row because it should work.
4. **Receipt**: binary pass or fail per row, with paths, commands, and exit codes. Report failures and unavailable checks. Never certify a check that did not run.

## Mac helpers

```bash
export BROOMVA_ROOT=/Volumes/Broomva/home/broomva
# Prefer installed skill scripts when present:
"$HOME/.agents/skills/dogfood/scripts/dogfood.sh" detect
# Cookbook (deep patterns): $BROOMVA_ROOT/bstack/references/dogfood-patterns.md
```

The cookbook in this repo is [`references/dogfood-patterns.md`](../../../references/dogfood-patterns.md).

## Minimum surfaces (if the cookbook is unreachable)

| Stack | Surfaces |
|---|---|
| nextjs | curl health, browser click-path, console errors |
| rest-api | curl plus jq contract, auth negative |
| rust-cli | `--help`, a golden command, exit codes |
| mcp-server | tool list, one live tool call |
| tauri / expo | a real UI interaction plus a screenshot receipt |

Compose with [broomva-workspace](../broomva-workspace/SKILL.md) completion checks (`make check` and the rest). Dogfood is interaction evidence. It does not replace CI.
