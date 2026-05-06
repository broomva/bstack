# bstack — The Broomva Stack

**A portable harness metalayer for AI-native development.** Eleven irreducible primitives plus 28 curated agent skills that turn any agent-driven workspace into a self-operating system.

```bash
npx skills add broomva/bstack
```

This installs the meta-skill that bootstraps the full stack — primitive contract, governance scaffolding, hooks, and skill roster — into your project. Works with Claude Code, Codex, Gemini CLI, OpenCode, and the [50+ agent CLIs the skills ecosystem supports](https://github.com/vercel-labs/skills).

## The eleven primitives

Each primitive closes one specific failure mode that drifts into entropy in unsupervised agent sessions.

| # | Primitive | Closes |
|---|---|---|
| **P1** | Conversation Bridge | session amnesia |
| **P2** | Control Gate | destructive ops the model didn't authorize |
| **P3** | Linear Tickets | invisible work |
| **P4** | PR Pipeline | merging unreviewed code |
| **P5** | Parallel Agents | sequential bottleneck on independent tasks |
| **P6** | Knowledge Bookkeeping | knowledge graph rot |
| **P7** | CI Watcher + Productive Wait | sleep-on-CI dead time |
| **P8** | Skill Freshness Check | silent rot of `npx skills add` snapshots |
| **P9** | Branch + Worktree Janitor | squash-merged branches and dead worktrees accumulating |
| **P10** | Worktree Hygiene Discipline | dirty trees and orphan worktrees compounding across sessions |
| **P11** | Empirical Feedback Loop | shipping code that compiles but doesn't actually work when exercised |

Full reference with reflexive trigger rules, invariants, and cohesion narrative: **[references/primitives.md](references/primitives.md)**.

P6, P7, P10, and P11 are *reasoning-enforced* — they bind every agent through reflexive trigger rules in `AGENTS.md` rather than through hooks. The other primitives are mechanism-enforced through hooks, scripts, or CI gates.

## Stack layers (28 skills)

| Layer | Skills | Purpose |
|-------|--------|---------|
| **Foundation** | [agentic-control-kernel](https://skills.sh/broomva/agentic-control-kernel), [control-metalayer-loop](https://skills.sh/broomva/control-metalayer-loop), [harness-engineering-playbook](https://skills.sh/broomva/harness-engineering-playbook), [p9](https://skills.sh/broomva/p9) | Safety shields, governance, deterministic workflow, CI watcher + productive-wait |
| **Memory & Consciousness** | [agent-consciousness](https://skills.sh/broomva/agent-consciousness), [knowledge-graph-memory](https://skills.sh/broomva/knowledge-graph-memory), [prompt-library](https://skills.sh/broomva/prompt-library) | Three-substrate consciousness, persistent context |
| **Orchestration** | [symphony](https://skills.sh/broomva/symphony), [symphony-forge](https://skills.sh/broomva/symphony-forge), [autoany](https://skills.sh/broomva/autoany) | Agent dispatch, scaffold CLI, EGRI self-improvement |
| **Research & Intelligence** | [deep-dive-research-orchestrator](https://skills.sh/broomva/deep-dive-research-orchestrator), [skills](https://skills.sh/broomva/skills), [skills-showcase](https://skills.sh/broomva/skills-showcase) | Multi-dimensional research, skills inventory |
| **Design & Implementation** | [arcan-glass](https://skills.sh/broomva/arcan-glass), [next-forge](https://skills.sh/broomva/next-forge) | BroomVA design system, Next.js production templates |
| **Platform Specialties** | [alkosto-wait-optimizer](https://skills.sh/broomva/alkosto-wait-optimizer), [content-creation](https://skills.sh/broomva/content-creation), [finance-substrate](https://skills.sh/broomva/finance-substrate), seo-llmeo, brand-icons | Decision optimizer, content pipeline, finance, SEO/LLMEO, brand assets |
| **Strategy & Decision Intel** | [pre-mortem](https://skills.sh/broomva/strategy-skills), [braindump](https://skills.sh/broomva/strategy-skills), [morning-briefing](https://skills.sh/broomva/strategy-skills), [drift-check](https://skills.sh/broomva/strategy-skills), [strategy-critique](https://skills.sh/broomva/strategy-skills), [stakeholder-update](https://skills.sh/broomva/strategy-skills), [decision-log](https://skills.sh/broomva/strategy-skills), [weekly-review](https://skills.sh/broomva/strategy-skills) | Strategic thinking, decision intelligence, personal productivity |

## Commands

Once installed, the skill exposes six commands:

- **`bootstrap`** — install all 28 skills + scaffold governance (CLAUDE.md, AGENTS.md, `.control/policy.yaml`) + wire hooks + run doctor
- **`doctor`** — verify primitive contract compliance (always exits 0 by default; `--strict` for CI)
- **`repair`** — apply targeted fixes for gaps the doctor surfaces
- **`status`** — show installed-vs-missing skills + harness health
- **`validate`** — check skill SKILL.md frontmatter health
- **`revamp`** — full reconfiguration: force-reinstall + rewire + re-doctor

## Governance & stability

bstack's governance layer (`CLAUDE.md` + `AGENTS.md` + `.control/policy.yaml`) is the **Level 3 controller** in a [Recursive Controlled Systems hierarchy](https://broomva.tech/writing/recursive-controlled-systems) with formal stability proofs. The L3 stability margin is narrow on purpose — governance changes consume budget, so the contract evolves slowly and deliberately.

| Level | System | Controller | Stability λ |
|---|---|---|---|
| L0 | External plant | Arcan agent loop | 1.455 |
| L1 | Agent internal | Autonomic homeostasis controller | 0.411 |
| L2 | Meta-control | EGRI loop engine | 0.069 |
| **L3** | **Governance** | **CLAUDE.md + AGENTS.md + policy.yaml** | **0.006** |

Composite stability: λᵢ > 0 at all levels ⟹ exponentially stable.

## Browse the full catalog

Interactive catalog with descriptions, install commands, and layer diagrams:

**[broomva.tech/skills](https://broomva.tech/skills)**

The narrative on what bstack is, why it exists, and what the eleven primitives buy you in measured throughput is at:

**[broomva.tech/writing/bstack-portable-harness-metalayer](https://broomva.tech/writing/bstack-portable-harness-metalayer)**

## License

[MIT](LICENSE)
