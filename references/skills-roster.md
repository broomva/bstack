# bstack Skills Roster

28 curated skills across 7 layers. The Broomva Stack.

## Foundation — Control & Governance

| # | Skill | Install | Description |
|---|-------|---------|-------------|
| 1 | `agentic-control-kernel` | `npx skills add broomva/agentic-control-kernel` | LLM-as-controller with safety shields, typed plant/action/trace schemas, multi-rate loop hierarchy. The governance backbone. |
| 2 | `control-metalayer-loop` | `npx skills add broomva/control-metalayer` | Control primitives: setpoints, sensors, actuators, stability gates, policy profiles. Bootstraps `.control/policy.yaml`. |
| 3 | `harness-engineering-playbook` | `npx skills add broomva/harness-engineering-skill` | Agent-first workflow: AGENTS.md, PLANS.md, smoke/test/lint/typecheck harness, entropy control checks. |

## Memory & Consciousness

| # | Skill | Install | Description |
|---|-------|---------|-------------|
| 4 | `agent-consciousness` | `npx skills add broomva/control-metalayer` | Three-substrate persistence: governance + knowledge graph + episodic memory. Progressive crystallization pathway. |
| 5 | `knowledge-graph-memory` | `npx skills add broomva/control-metalayer` | Conversation logs to Obsidian knowledge graph bridge. Generates per-session docs with frontmatter and wikilinks. |
| 6 | `kg` | `npx skills add broomva/kg` | LLM-as-index loader for `research/entities/`. Two-tier scoring (catalog tier-1, body-grep tier-2 fallback) routes a topic to top-N entity bodies the agent reasons over. The runtime form of "the LLM **is** the index" — substrate canonical, one projection (catalog) routes, agent IS the query engine. Pairs with bookkeeping's `cmd_index` and the workspace's `knowledge-catalog-refresh-hook.sh` Stop hook (P6). |
| 7 | `prompt-library` | `npx skills add broomva/prompt-library` | Shared knowledge surface across agents. Versioned prompts + an evaluation engine: every pull/completion writes a typed `prompt_invocation` row with source attribution (`web\|cli\|skill\|api`), latency, tokens, cost, and feedback. The `broomva` Rust CLI (`broomva prompts pull/list/complete/feedback`) is the runtime; the broomva-cli skill carries the auto-tracing mandate. See [prompts-integration.md](prompts-integration.md). |

## Orchestration

| # | Skill | Install | Description |
|---|-------|---------|-------------|
| 8 | `symphony` | `npx skills add broomva/symphony` | Rust orchestration engine for coding agents. Daemon mode, Linear/GitHub tracker integration, lifecycle hooks. |
| 9 | `symphony-forge` | `npx skills add broomva/symphony-forge` | CLI scaffolder with composable control metalayer. Bootstraps projects with agent governance built in. |
| 10 | `autoany` | `npx skills add broomva/autoany` | EGRI self-improvement framework. Turns ambiguous goals into safe, measurable, rollback-capable recursive improvement loops. |

## Research & Intelligence

| # | Skill | Install | Description |
|---|-------|---------|-------------|
| 11 | `deep-dive-research-orchestrator` | `npx skills add broomva/deep-dive-research-skill` | Multi-dimensional research with coordinated AI specialists. 10+ source synthesis with citations. |
| 12 | `skills` | `npx skills add broomva/skills` | Canonical reference inventory of 83 agent skills across 15 domains. Browsable catalog. |
| 13 | `skills-showcase` | `npx skills add broomva/skills` | Remotion video + X thread generator for the skills inventory. Animated showcase content. |

## Design & Implementation

| # | Skill | Install | Description |
|---|-------|---------|-------------|
| 14 | `arcan-glass` | `npx skills add broomva/arcan-glass` | BroomVA web design system. Glass/frosted effects, dark-first themes, AI Blue brand tokens. |
| 15 | `next-forge` | `npx skills add broomva/symphony-forge` | Production Next.js SaaS template via symphony-forge. Turborepo, auth, payments, observability. |

## Platform

| # | Skill | Install | Description |
|---|-------|---------|-------------|
| 16 | `alkosto-wait-optimizer` | `npx skills add broomva/alkosto-wait-optimizer-skill` | Probability-based decision tool for optimal waiting times. Bayesian estimation with uncertainty. |
| 17 | `content-creation` | `npx skills add broomva/skills --skill content-creation` | Full-stack content pipeline — research → narrative → visual assets → video → social → deploy; ships bstack-launch + open-source-stack example campaigns. (Migrated 2026-05-25 from `broomva/content-creation` — Phase 4b.) |
| 17a | `content-engine` | `npx skills add broomva/skills --skill content-engine` | Full-stack AI content studio — visual DNA compiler, cinematic generation, browser autopilot, content loop; bundles 4 sub-skills. (Migrated 2026-05-25 from `broomva/content-engine` — Phase 4b.) |
| 17b | `launch-video` | `npx skills add broomva/skills --skill launch-video` | Liquid Glass product launch video — dark void, 3D floating panels, spring animations via Remotion. (Migrated 2026-05-25 from `broomva/launch-video` — Phase 4b.) |
| 17c | `ltx-video` | `npx skills add broomva/skills --skill ltx-video` | LTX-2.3 video generation — setup, inference, prompting, ComfyUI integration for Lightricks 22B DiT audio-video model. (Migrated 2026-05-25 from `broomva/ltx-video` — Phase 4b.) |
| 17d | `creative-review` | `npx skills add broomva/skills --skill creative-review` | Meta creative review — style adherence scoring, feedback loops, self-improving creative pipeline. (Migrated 2026-05-25 from `broomva/creative-review` — Phase 4b.) |
| 17e | `brainrot-for-good` | `npx skills add broomva/skills --skill brainrot-for-good` | High-retention video production using dopamine-aware editing for genuinely valuable content. (Migrated 2026-05-25 from `broomva/brainrot-for-good` — Phase 4b.) |
| 18 | `finance-substrate` | `npx skills add broomva/finance-substrate` | Personal finance & Colombian tax management. Bank CSV import, TRM rates, DIAN tax projection, withholdings, e-invoicing. Zero paid deps. |
| 19 | `seo-llmeo` | `npx skills add broomva/skills --skill seo-llmeo` | SEO and LLM Engine Optimization — audits, meta tags, structured data (JSON-LD), llms.txt generation. (Migrated 2026-05-25 from `broomva/seo-llmeo` — Phase 4a.) |
| 20 | `brand-icons` | `npx skills add broomva/skills --skill brand-icons` | Brand icon and visual identity asset generation — favicons, app icons, OG images, social avatars. (Migrated 2026-05-25 from `broomva/brand-icons` — Phase 4a.) |
| 20b | `blog-post` | `npx skills add broomva/skills --skill blog-post` | Full-stack blog post production — research → angle → draft → multi-platform distribution (X, LinkedIn, Instagram, Substack). (Migrated 2026-05-25 from `broomva/blog-post` — Phase 4a.) |

## Strategy & Decision Intelligence

> Migrated 2026-05-25 to `broomva/skills` Tier-2 monorepo (was bundled in `broomva/strategy-skills`; bundle remains backward-compatible for 6-month deprecation window until 2026-11-25). 9 individual skills now installable separately.

| # | Skill | Install | Description |
|---|-------|---------|-------------|
| 21 | `pre-mortem` | `npx skills add broomva/skills --skill pre-mortem` | 4-category failure-mode analysis (likelihood × impact) with mitigation plan. |
| 22 | `premortem` | `npx skills add broomva/skills --skill premortem` | Klein/Kahneman premortem with parallel sub-agent deep-dives + HTML report. |
| 23 | `braindump` | `npx skills add broomva/skills --skill braindump` | Raw thoughts → Obsidian vault with auto-categorization, tags, and backlinks. |
| 24 | `morning-briefing` | `npx skills add broomva/skills --skill morning-briefing` | Daily focused brief from vault priorities + action items + updates. |
| 25 | `drift-check` | `npx skills add broomva/skills --skill drift-check` | Priority drift report — stated priorities vs actual effort (git log + vault). |
| 26 | `strategy-critique` | `npx skills add broomva/skills --skill strategy-critique` | Red-team critique of strategy documents with gaps, risks, missing assumptions. |
| 27 | `stakeholder-update` | `npx skills add broomva/skills --skill stakeholder-update` | One fact set → 3 audience versions (technical / business / customer). |
| 28 | `decision-log` | `npx skills add broomva/skills --skill decision-log` | Structured decision capture with context, alternatives, rationale → vault. |
| 29 | `weekly-review` | `npx skills add broomva/skills --skill weekly-review` | Weekly vault change scan + attention flags. |

## Workflow & Lifecycle (Tier-2 monorepo)

> Graduated from workspace-local prototypes 2026-05-25 (`broomva/skills` PR #2).

| # | Skill | Install | Description |
|---|-------|---------|-------------|
| 30 | `handoff` | `npx skills add broomva/skills --skill handoff` | Fresh-session handoff doc drafting — compress an arc into a resumable doc for the next agent context. |
| 31 | `make-spec` | `npx skills add broomva/skills --skill make-spec` | Native-HTML design-doc scaffold (spec / plan / ADR / report / pr-explainer) using the canonical Broomva dark theme — implements P18 Category-C. |
