# bstack Skills Roster

28 curated skills across 7 layers. The Broomva Stack.

> **Canonical *installable* roster:** [`references/companion-skills.yaml`](companion-skills.yaml) — the 61 bstack-native skills, all in the **broomva/skills** monorepo (validated by `tests/roster-monorepo-sync.test.sh`). Install path-independently: `npx skills add broomva/skills --skill <name>`. **This doc is a broader human catalog**: it also documents ecosystem *products* (e.g. symphony, autoany, next-forge) that are not installable bstack skills, so it intentionally lists more than the YAML. For what is installable, the YAML is authoritative.

## Publishing standard (skill layout) — installability + consolidation

A skill is a **folder**, per the [Agent Skills standard](https://agentskills.io) — `SKILL.md` + optional `scripts/` / `references/` / `assets/`. A top-level `SKILL.md` at a repo root is **standard-valid** (both the spec and the skills.sh README list the repo root as a discovery location). The defect is *not* the layout — it's an **open upstream CLI bug**.

| Layout | Remote `npx skills add` | |
|---|---|---|
| `skills/<name>/{SKILL.md, scripts/…}` (subdir) | installs the **full** skill | ✅ installs clean |
| top-level `SKILL.md` + sibling `scripts/` at repo root | installs **only `SKILL.md`** — siblings dropped → non-functional | ⚠️ standard-valid layout, but hits [skills#1523](https://github.com/vercel-labs/skills/issues/1523) |

**Why:** the vercel-labs/skills CLI special-cases a repo-root `SKILL.md` and copies only that file ([#1523](https://github.com/vercel-labs/skills/issues/1523) / #1517 — **open, unfixed**). *Local-path* install copies everything; only *remote* repo-root install drops siblings. A `skills/<name>/` subdir is a clean folder and is fully copied, so it sidesteps the bug.

**Canonical fix — consolidate into the `broomva/skills` monorepo (BRO-1561):** rather than push each standalone repo into an awkward `skills/<name>/`-inside-a-same-named-repo workaround, skills vendor into **`broomva/skills/skills/<name>/`** and install via `npx skills add broomva/skills --skill <name>`. The subdir is non-redundant there, the monorepo has generic lint + per-skill tests, and standalone repos become **deprecated redirect stubs** (6-month window) pointing at the monorepo command. Tier-2 graduation in `broomva/skills` CONTRIBUTING is the path.

**Validation:** `--list` is **necessary but not sufficient** — it only parses frontmatter, never the file-copy path, so it passes even when the install drops `scripts/`. The real gate is a **clean-room install that yields a *runnable* skill** (bundled files land + the skill's own test passes). The `skillify` skill enforces frontmatter/skills.sh-parseability at step 1 (deterministic, required) and **advises** (step 1b, WARN) when a repo-root-with-bundled-dirs layout would hit #1523 — it no longer *rejects* a standard-valid layout. Full proof + root cause: `research/entities/tool/skills-sh.md`.

## Foundation — Control & Governance

| # | Skill | Install | Description |
|---|-------|---------|-------------|
| 1 | `agentic-control-kernel` | `npx skills add broomva/skills --skill agentic-control-kernel` | LLM-as-controller with safety shields, typed plant/action/trace schemas, multi-rate loop hierarchy. The governance backbone. |
| 2 | `control-metalayer-loop` | *(consolidated → `agentic-control-kernel`)* | Control primitives: setpoints, sensors, actuators, stability gates, policy profiles. Bootstraps `.control/policy.yaml`. |
| 3 | `harness-engineering-playbook` | `npx skills add broomva/skills --skill harness-engineering-playbook` | Agent-first workflow: AGENTS.md, smoke/test/lint/typecheck harness, entropy-control checks. (Migrated 2026-05-25 from `broomva/harness-engineering-skill` — Phase 4c.) |

## Memory & Consciousness

| # | Skill | Install | Description |
|---|-------|---------|-------------|
| 4 | `agent-consciousness` | *(consolidated → `agentic-control-kernel`)* | Three-substrate persistence: governance + knowledge graph + episodic memory. Progressive crystallization pathway. |
| 5 | `knowledge-graph-memory` | *(consolidated → `agentic-control-kernel`)* | Conversation logs to Obsidian knowledge graph bridge. Generates per-session docs with frontmatter and wikilinks. |
| 6 | `kg` | `npx skills add broomva/skills --skill kg` | LLM-as-index loader for `research/entities/`. Two-tier scoring (catalog tier-1, body-grep tier-2 fallback) routes a topic to top-N entity bodies the agent reasons over. The runtime form of "the LLM **is** the index" — substrate canonical, one projection (catalog) routes, agent IS the query engine. Pairs with bookkeeping's `cmd_index` and the workspace's `knowledge-catalog-refresh-hook.sh` Stop hook (P6). |
| 7 | `prompt-library` | `npx skills add broomva/skills --skill prompt-library` | Shared knowledge surface across agents. Versioned prompts + an evaluation engine: every pull/completion writes a typed `prompt_invocation` row with source attribution (`web\|cli\|skill\|api`), latency, tokens, cost, and feedback. The `broomva` Rust CLI (`broomva prompts pull/list/complete/feedback`) is the runtime; the broomva-cli skill carries the auto-tracing mandate. See [prompts-integration.md](prompts-integration.md). |

## Orchestration

| # | Skill | Install | Description |
|---|-------|---------|-------------|
| 8 | `symphony` | *(ecosystem product — not a portable skill in the monorepo; lives at `broomva/symphony`)* | Rust orchestration engine for coding agents. Daemon mode, Linear/GitHub tracker integration, lifecycle hooks. |
| 9 | `symphony-forge` | *(ecosystem product — not a portable skill in the monorepo; lives at `broomva/symphony-forge`)* | CLI scaffolder with composable control metalayer. Bootstraps projects with agent governance built in. |
| 10 | `autoany` | *(ecosystem product — not a portable skill in the monorepo; lives at `broomva/autoany`)* | EGRI self-improvement framework. Turns ambiguous goals into safe, measurable, rollback-capable recursive improvement loops. |

## Research & Intelligence

| # | Skill | Install | Description |
|---|-------|---------|-------------|
| 11 | `deep-dive-research-orchestrator` | `npx skills add broomva/skills --skill deep-dive-research-orchestrator` | Multi-dimensional research with coordinated AI specialists. 10+ source synthesis with citations. (Migrated 2026-05-25 from `broomva/deep-dive-research-skill` — Phase 4c.) |
| 11a | `social-intelligence` | `npx skills add broomva/skills --skill social-intelligence` | Autonomous social engagement + knowledge extraction loop for Moltbook and X — compounds with `blog-post` and `content-creation`. (Migrated 2026-05-25 from `broomva/social-intelligence` — Phase 4c.) |
| 12 | `skills` | `npx skills add broomva/skills` | Canonical reference inventory of 83 agent skills across 15 domains. Browsable catalog. |
| 13 | `skills-showcase` | *(repo-root Remotion tool in broomva/skills — not an installable skill)* | Remotion video + X thread generator for the skills inventory. Animated showcase content. |

## Design & Implementation

| # | Skill | Install | Description |
|---|-------|---------|-------------|
| 14 | `arcan-glass` | `npx skills add broomva/skills --skill arcan-glass` | BroomVA web design system. Glass/frosted effects, dark-first themes, AI Blue brand tokens. |
| 15 | `next-forge` | *(ecosystem product — not a portable skill in the monorepo; lives at `broomva/symphony-forge`)* | Production Next.js SaaS template via symphony-forge. Turborepo, auth, payments, observability. |

## Platform

| # | Skill | Install | Description |
|---|-------|---------|-------------|
| 16 | `alkosto-wait-optimizer` | `npx skills add broomva/skills --skill alkosto-wait-optimizer` | Probability-based decision tool for optimal waiting times. Bayesian estimation with uncertainty. |
| 17 | `content-creation` | `npx skills add broomva/skills --skill content-creation` | Full-stack content pipeline — research → narrative → visual assets → video → social → deploy; ships bstack-launch + open-source-stack example campaigns. (Migrated 2026-05-25 from `broomva/content-creation` — Phase 4b.) |
| 17a | `content-engine` | `npx skills add broomva/skills --skill content-engine` | Full-stack AI content studio — visual DNA compiler, cinematic generation, browser autopilot, content loop; bundles 4 sub-skills. (Migrated 2026-05-25 from `broomva/content-engine` — Phase 4b.) |
| 17b | `launch-video` | `npx skills add broomva/skills --skill launch-video` | Liquid Glass product launch video — dark void, 3D floating panels, spring animations via Remotion. (Migrated 2026-05-25 from `broomva/launch-video` — Phase 4b.) |
| 17c | `ltx-video` | `npx skills add broomva/skills --skill ltx-video` | LTX-2.3 video generation — setup, inference, prompting, ComfyUI integration for Lightricks 22B DiT audio-video model. (Migrated 2026-05-25 from `broomva/ltx-video` — Phase 4b.) |
| 17d | `creative-review` | `npx skills add broomva/skills --skill creative-review` | Meta creative review — style adherence scoring, feedback loops, self-improving creative pipeline. (Migrated 2026-05-25 from `broomva/creative-review` — Phase 4b.) |
| 17e | `brainrot-for-good` | `npx skills add broomva/skills --skill brainrot-for-good` | High-retention video production using dopamine-aware editing for genuinely valuable content. (Migrated 2026-05-25 from `broomva/brainrot-for-good` — Phase 4b.) |
| 18 | `finance-substrate` | `npx skills add broomva/skills --skill finance-substrate` | Personal finance & Colombian tax management. Bank CSV import, TRM rates, DIAN tax projection, withholdings, e-invoicing. Zero paid deps. *(Pending Tier-1 vs Tier-2 lock-in.)* |
| 18a | `investment-management` | `npx skills add broomva/skills --skill investment-management` | Portfolio construction, factor models, backtesting, multi-platform execution (Alpaca, Coinbase, Polymarket). (Migrated 2026-05-25 from `broomva/investment-management` — Phase 4c.) |
| 18b | `wealth-management` | `npx skills add broomva/skills --skill wealth-management` | Wealth planning + Monte Carlo simulations + tax-optimized allocation + net worth forecasting. (Migrated 2026-05-25 from `broomva/wealth-management` — Phase 4c.) |
| 18c | `haima` | `npx skills add broomva/skills --skill haima` | Agent guide for x402 machine-to-machine payments, secp256k1 wallets, per-task billing, on-chain USDC settlement. (Migrated 2026-05-25 from `broomva/haima-skill` — Phase 4c; renamed to drop `-skill` suffix. Runtime crate stays at `broomva/haima`.) |
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
