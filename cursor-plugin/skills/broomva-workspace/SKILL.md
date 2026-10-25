---
name: broomva-workspace
description: >-
  Use at the start of any substantive broomva/workspace task, /checkit, project
  recommendation, repo edit, or when unsure what matters now — Snapshot,
  current-work route, lenses, policy gates.
---

# broomva-workspace — startup route

Scale ceremony to task size. Bounded read-only review: report unavailable state; do not refresh indexes.

## Always (substantive work)

1. **Resolve checkout** — Prefer the Mac checkout at `BROOMVA_ROOT=/Volumes/Broomva/home/broomva` (or `~/broomva` when that symlink is the checkout). Read it with `git -C "$BROOMVA_ROOT" --no-optional-locks` so a status read does not take the index lock. Otherwise read `broomva/workspace` through the host's GitHub tools.
2. **Snapshot (P15)** — branch, clean/dirty, ahead/behind, relevant open PRs and tickets. Missing means unknown, never green.
3. **Current work** — `docs/references/current-work.md`, then `.control/initiatives` on **origin/main**. Old knowledge-graph popularity does not set priority.
4. **Route** — `roles/_index.md`, then the lens. Knowledge: [kg](../kg/SKILL.md) with `--terms`. Capture: [bookkeeping](../bookkeeping/SKILL.md).
5. **Before writes** — Dep-Chain (P14); `.control/policy.yaml` hard gates; `make control-audit` before structural changes.

## Delivery

Branch and open a PR. [dogfood](../dogfood/SKILL.md) before claiming done. [cross-review](../cross-review/SKILL.md) before a substantive or governance merge. [autonomous](../autonomous/SKILL.md) when the owner says go. Done means merged plus verified receipts. Human-only paths stay human. New projects: Bun, Biome, Better Auth. No private CRM or client material in public artifacts.

## Hosts without a control-gate hook

The Claude Code plugin blocks force-push, `git reset --hard`, and human-only merges in PreToolUse. A host that did not load those hooks does not. Honor the gates explicitly, and install the reminder with [setup-bstack](../setup-bstack/SKILL.md).
