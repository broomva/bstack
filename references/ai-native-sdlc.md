# AI-native SDLC — the playbook's twelve plays on the twenty primitives

Source: Anthropic, *The AI-native SDLC playbook*, a Claude Academy course
<https://academy.claude.com/courses/ai-native-sdlc-playbook/introduction>. Anthropic also
published a blog post of the same name: <https://claude.com/blog/the-ai-native-sdlc-playbook>
(2026-08-21).

This file maps each play onto bstack and names the mechanism that holds it. **No new
primitive is added.** Every play lands as a reflex or a command under an existing one, so the
count stays at twenty.

## The organizing rule

> "A stage ends by committing an artifact with the commit initiating the next stage."

```
intent.md → spec.md → plan.md → diff + tests → PR + findings → incident → next intent.md
```

The chain of commits is the audit trail. Tickets (P3) and Pipeline (P4) already carry most of
it. This reference adds what they lacked:
- an artifact **upstream** of the ticket (`intent.md`)
- a link from the ticket **back** to the merge SHA
- a machine check that the diff still matches the plan

## Crosswalk

| Stage | Play | Primitive | bstack mechanism |
|---|---|---|---|
| Plan | Capture `intent.md` | Tickets (P3) | `bstack intent new\|lint\|set-status`; `references/templates/intent.md`; an `intent/` home in the repo |
| Design | Requirements + design → `spec.md` | Audience (P18), Tickets (P3) | `docs/specs/`; `workflows/intent-to-spec.yml` fires on an accepted intent (off by default) |
| Build | Plan mode → `plan.md` | Dep-Chain (P14), Pipeline (P4) | `references/templates/plan.md`; `bstack plan-drift` |
| Build | `CLAUDE.md` | Crystallize (P16) | kept; see divergences |
| Build | Skills as institutional knowledge | Lens (P17), Crystallize (P16) | skills monorepo; trigger evals (`role-x eval`) |
| Build | Parallel sessions + subagents | Fanout (P5), Hygiene (P10) | worktrees, `bstack wave`, `bstack fleet` |
| Test | Give Claude a feedback loop | Empirical (P11) | `bstack test-lock` + its PreToolUse hook (plugin) + `verify` in CI |
| Test | Continuous evals in CI | Empirical (P11) | `bstack evals run\|validate --prove\|baseline`; `workflows/agent-evals.yml` |
| Deploy | AI in the PR review loop | Cross-Review (P20) | `references/templates/REVIEW.md`; P20 strata |
| Deploy | Hooks as approval gates | Gate (P2) | `control-gate-hook.sh`; `bstack managed-hooks`; `managed-settings.example.json` |
| Deploy | CI/CD integration | Pipeline (P4), Wait (P9) | `workflows/ci-triage.yml` (read-only `claude -p`); `workflows/linear-backlink.yml` |
| Maintain | Closing the loop on metrics | Empirical (P11), Tickets (P3) | `bstack bands check\|intent\|series\|diagnose-cmd`; `workflows/bands.yml` |

## Principles, each with its enforcement

1. **The committed artifact is the trigger and the record.** An accepted `intent.md` starts
   the design pass. An approved spec starts plan mode. A breached band writes the next
   `intent.md`. *Held by* `bstack intent`, and by the `intent-to-spec` and `bands` templates.
2. **Link both directions.** Commits carry the ticket ID forward. After merge, the PR URL and
   merge SHA go back onto the ticket. A one-way link is not linkage. *Held by* the P3 reflex
   and `workflows/linear-backlink.yml`.
3. **Plan before code; keep the plan in sync.** When implementation departs from the plan, the
   plan is updated in the same commit. *Held by* `bstack plan-drift` (advisory; `--strict`
   to gate).
4. **Protect the feedback loop from the agent it constrains.** For a bug fix, commit the
   failing test first under a `Test-Lock:` trailer that records the test's sha256, then fix
   the code, not the test. *Held by* `bstack test-lock`. The hook blocks edits to the locked
   test. `verify` in CI is the gate: it fails when the test's content differs from what the
   lock pinned, when a lock carries no hash, and when the lock commit's content no longer
   matches its trailer (this catches `commit --amend -a`); it exits 3 on any `Test-Unlock:`
   trailer until a human accepts that commit. That makes the lock tamper-evident against an
   agent taking the shortcut of weakening the test. It is not a security boundary: an agent
   that forges git objects with your credentials (`commit-tree` with a recomputed trailer)
   or drops the lock commit is visible only in review.
5. **Configuration is code, so regression-test it by behavior.** A change to `CLAUDE.md`,
   `AGENTS.md`, skills or hooks runs 20–50 real tasks through `claude -p`, and every
   production incident becomes an eval. *Held by* `bstack evals`.
   - Each eval runs in a standalone scratch repo that hides the evals.
   - Planted git config there is neutralized.
   - The agent under test sees only the built-in tools its `allowed_tools` names (`--tools`)
     and no MCP servers (`--strict-mcp-config`), and nothing unapproved runs (`dontAsk`).
     Within an available tool, a settings-file allow rule or hook can still approve a call
     that `allowed_tools` does not list.
   - An eval must *discriminate*. `validate --prove` requires its reference solution to pass,
     each named `violations` arm (a plausible wrong behaviour) to fail a check, and a no-op
     agent that only replies "I have completed the task." to fail.
6. **The review policy is a committed file, and the writer never approves.** `REVIEW.md` names
   the passes (bugs · security · compliance against `spec.md` + `plan.md`), defines Important,
   and caps nits. *Held by* P20.
7. **Detection is deterministic, and the model acts only in tiers.** At 1σ the detector logs.
   At 2σ a read-only diagnosis runs. At 3σ the model may only propose, through a PR or a
   pre-approved runbook. The detector is a unit-tested program with no model in it. *Held by*
   `bstack bands`:
   - The model returns text; a program writes the file.
   - The diagnosis runs in a throwaway clone that holds no credentials. It has Read, Grep and
     Glob only: no shell (`--restricted`) and no MCP servers. The workflow pre-fetches the
     data it reads and appends its reply to the intent.
   - The series drops the incomplete current day.
   - An undersized baseline reports `insufficient_baseline`, never `none`.
8. **Governance hooks live on a surface the organization trusts.** Under
   `allowManagedHooksOnly`, user, project and local hooks are blocked. Only managed hooks, SDK
   hooks, and hooks from plugins force-enabled in managed `enabledPlugins` run, and `/goal`
   cannot run. *Held by* `bstack managed-hooks --fail-on-critical` and the example managed
   settings.
9. **The agent acts up to the production gate and cannot pass it.** Branch protection,
   per-environment tiers, a hook on production deploys, and a rehearsed rollback. *Held by*
   Gate (P2); `ci-triage.yml` is read-only.
10. **Every play carries a measurement readable from git.** See below.

## Deliberate divergences

| Course | bstack keeps | Why |
|---|---|---|
| Keep `CLAUDE.md` under a page; add a correction when Claude "makes a mistake twice" | `CLAUDE.md` holds invariants and `AGENTS.md` holds operations. Corrections go to memory at first sighting and crystallize into `AGENTS.md` at ≥3 (Crystallize, P16). | The L3 stability budget: governance files change rarely. A rule-of-two edit cadence on the invariants file violates it. |
| Findings never approve or block; a code owner approves | A P20 verdict ≥7/10 gates auto-merge; the gates are the trust | Both keep separation of duties: the writing model cannot be the sole judge |
| "An adversarial reviewing agent" as the Stage-6 gate | Cross-model where available (Stratum A), fresh-context otherwise (Stratum B) | A reviewer from the same model family shares the writer's blind spots |
| Gate merges on the eval pass rate | Advisory until a baseline is recorded (`bstack evals baseline`), then `--gate` | A gate with no baseline has no setpoint |

## Measurements (git is the store)

| Play | Leading | Lagging |
|---|---|---|
| Intent | first conversation → committed `intent.md` | share of intents accepted; intent edits after the first `spec.md` commit |
| Design | `intent.md` commit → `spec.md` commit | `spec.md` commits after the first `plan.md` commit |
| Plan | first-pass merge rate | `plan-drift` `match_ratio` on merged PRs |
| Feedback loop | first-pass CI success | review time per PR |
| Evals | pass rate per run; incident → eval latency | regressions caught in CI vs in production |
| PR review | time to first review | defects caught before merge vs escaped |
| Hooks | wait time per gate | gate violations reaching production |
| Closing the loop | band breach → `intent.md` | share of findings that become merged fixes |

## Adopting it in a workspace

1. `bstack intent new <slug>` → `intent/`; accepting an intent means merging it with
   `Status: accepted`.
2. Copy `references/templates/REVIEW.md` to the repo root and edit the passes.
3. Add `evals/agent/*.json` with a `reference` on each; `bstack evals validate --prove`.
4. Copy the files from `references/templates/workflows/` into `.github/workflows/`. Each one
   passes values through `env:`, never `${{ }}` inside `run:`.
5. Add `.control/bands/<metric>.yaml` from `references/templates/bands.example.yaml`.
6. Run `bstack managed-hooks` and see which governance hooks an enterprise baseline would
   switch off. `references/templates/managed-settings.example.json` force-enables
   `bstack@bstack`, the ID of a **marketplace** install. A plugin found by the
   `~/.claude/skills/` scan carries a different ID (`bstack@skills-dir`). Force-enabling
   matches on the full ID, and `strictKnownMarketplaces` turns that scan off unless it lists
   `{ "source": "skills-dir" }`. So an organization that wants the plugin's hooks to survive
   should install bstack from its marketplace. Force-enabling a `skills-dir` ID has not been
   verified.
