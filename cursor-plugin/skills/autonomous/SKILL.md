---
name: autonomous
description: >-
  Use when the owner says go/proceed/ship it/be autonomous or has accepted a
  plan and wants full bstack discipline without repeating the ritual sentence.
---

# autonomous — full-discipline mode

When invoked, run the work with bstack reflexes and without further ritual prompts. The owner picks the plan. You execute.

## Always load first

[broomva-workspace](../broomva-workspace/SKILL.md): Snapshot, current-work and initiatives, lens, policy.

## Pipeline (scale to task size — no busywork)

1. **Plan bound** — assumptions, Dep-Chain (P14), success defined as an observable interaction.
2. **Branch** — never commit straight to main. Hygiene (P10): ownership is clear.
3. **Implement surgically** — the smallest useful change.
4. **Empirical (P11)** — [dogfood](../dogfood/SKILL.md) plus the project's own checks. Keep receipts.
5. **PR and pipeline (P4)** — push, open the PR, address review threads. Wait with event watchers, not sleep-poll (P9).
6. **Cross-review (P20)** — [cross-review](../cross-review/SKILL.md) before a substantive or governance merge.
7. **Merge only through policy gates** — never force-merge a human-only path, and never merge red checks.
8. **Bookkeeping (P6)** — [bookkeeping](../bookkeeping/SKILL.md), or a substrate edit, for graph-worthy findings. Do not ask first.
9. **Janitor** — after merge, dry-run cleanup. Do not delete other people's work.
10. **Handback** — what shipped, the SHA or PR, receipts, unknowns, and the next action.

## Anti-rationalization

Skipping docs, dogfood, or review in order to ship faster is forbidden. If blocked on the owner, preserve the ask and continue independent lanes.

Hosts without Claude PreToolUse hooks do not block hard gates mechanically. Honor them explicitly ([setup-bstack](../setup-bstack/SKILL.md)): no force-push to `main` or `master`, no `git reset --hard`, no merge of human-only paths, no merge on red checks.
