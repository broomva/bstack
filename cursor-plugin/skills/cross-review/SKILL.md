---
name: cross-review
description: >-
  Use before substantive merges or governance PRs (P20): independent adversarial
  review — writer cannot self-certify; strata A/B/C and pass score bound to
  reviewed SHA.
---

# cross-review — P20 adversarial gate

**The writer cannot be the final judge.** Fire before a substantive merge (more than about 200 lines, a public API, a multi-file change, or a governance-class path).

## Policy (broomva `.control/policy.yaml`)

- Pass score at least 7 for code, with the strata the change requires. Governance prose needs strata A, B, and C, plus a stability check and the L3 rate budget.
- Human-only merge for `policy.yaml`, settings, merge-gate and L3 workflows, control hooks, nested `CLAUDE.md` / `AGENTS.md`, and RCS parameters. Do not auto-merge those.
- Record the verdict bound to the **reviewed SHA**. A self-attested marker is not proof that a review ran. Still run the strata.

## Strata

| Stratum | Mechanism |
|---|---|
| **A** | A different evaluator when one is available (another model, another vendor's CLI, or a teammate). Strongest. |
| **B** | A fresh subagent under a devil's-advocate brief on the diff. Blocking. Do not end the turn waiting on it. |
| **C** | Composed adversarial passes (critique, pre-mortem, constructive dissent) on the code tier. |

Default for code: A when available, otherwise B, plus C. Knowledge-only diffs: one stratum (A, otherwise B). Specs and plans: use the design rubric (reversal cost, alternatives, non-goals, trade-offs, legibility), not the code anti-slop score alone.

## Procedure

1. Snapshot the head SHA. Gather the diff with git (`git --no-optional-locks` on the Mac volume) or the host's GitHub diff tools.
2. Dispatch an independent reviewer. The brief is to find flaws, not to rubber-stamp.
3. Reproduce material findings. Log PASS or FAIL with the score and the SHA in the PR body.
4. For a full governance review, honor the STOP and round budgets in the installed skill at `~/.agents/skills/cross-review`. Read that `SKILL.md` for the ledger format when the stakes are high.

Never merge failing checks or unresolved threads. Done means reviewed and verified, not merely authored.
