# bstack Skills Roster

27 curated skills across 7 layers. The Broomva Stack.

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
| 6 | `prompt-library` | `npx skills add broomva/prompt-library` | Shared knowledge surface across agents. Versioned prompts + an evaluation engine: every pull/completion writes a typed `prompt_invocation` row with source attribution (`web\|cli\|skill\|api`), latency, tokens, cost, and feedback. The `broomva` Rust CLI (`broomva prompts pull/list/complete/feedback`) is the runtime; the broomva-cli skill carries the auto-tracing mandate. See [prompts-integration.md](prompts-integration.md). |

## Orchestration

| # | Skill | Install | Description |
|---|-------|---------|-------------|
| 7 | `symphony` | `npx skills add broomva/symphony` | Rust orchestration engine for coding agents. Daemon mode, Linear/GitHub tracker integration, lifecycle hooks. |
| 8 | `symphony-forge` | `npx skills add broomva/symphony-forge` | CLI scaffolder with composable control metalayer. Bootstraps projects with agent governance built in. |
| 9 | `autoany` | `npx skills add broomva/autoany` | EGRI self-improvement framework. Turns ambiguous goals into safe, measurable, rollback-capable recursive improvement loops. |

## Research & Intelligence

| # | Skill | Install | Description |
|---|-------|---------|-------------|
| 10 | `deep-dive-research-orchestrator` | `npx skills add broomva/deep-dive-research-skill` | Multi-dimensional research with coordinated AI specialists. 10+ source synthesis with citations. |
| 11 | `skills` | `npx skills add broomva/skills` | Canonical reference inventory of 83 agent skills across 15 domains. Browsable catalog. |
| 12 | `skills-showcase` | `npx skills add broomva/skills` | Remotion video + X thread generator for the skills inventory. Animated showcase content. |

## Design & Implementation

| # | Skill | Install | Description |
|---|-------|---------|-------------|
| 13 | `arcan-glass` | `npx skills add broomva/arcan-glass` | BroomVA web design system. Glass/frosted effects, dark-first themes, AI Blue brand tokens. |
| 14 | `next-forge` | `npx skills add broomva/symphony-forge` | Production Next.js SaaS template via symphony-forge. Turborepo, auth, payments, observability. |

## Platform

| # | Skill | Install | Description |
|---|-------|---------|-------------|
| 15 | `alkosto-wait-optimizer` | `npx skills add broomva/alkosto-wait-optimizer-skill` | Probability-based decision tool for optimal waiting times. Bayesian estimation with uncertainty. |
| 16 | `content-creation` | (bundled with bstack) | Full content pipeline: blog posts, social threads, video scripts, SEO optimization. |
| 17 | `finance-substrate` | `npx skills add broomva/finance-substrate` | Personal finance & Colombian tax management. Bank CSV import, TRM rates, DIAN tax projection, withholdings, e-invoicing. Zero paid deps. |
| 18 | `seo-llmeo` | `npx skills add aaron-he-zhu/seo-geo-claude-skills@technical-seo-checker` | Technical SEO audit: robots.txt, sitemap, meta tags, structured data, canonical URLs, llms.txt validation. |
| 19 | `brand-icons` | (bundled with content-creation) | AI-generated logo/icon pipeline via nano-banana. Generates multi-size icons (favicon, PWA, Apple) from a single AI prompt. |

## Strategy & Decision Intelligence

| # | Skill | Install | Description |
|---|-------|---------|-------------|
| 20 | `pre-mortem` | `npx skills add broomva/strategy-skills` | Assumes project failure, works backward to identify top causes, scores by likelihood × impact, outputs mitigation plan. |
| 21 | `braindump` | `npx skills add broomva/strategy-skills` | Takes raw unstructured thoughts or transcripts, auto-files into vault folders with tags and backlinks. |
| 22 | `morning-briefing` | `npx skills add broomva/strategy-skills` | Reads action items, priorities, and vault updates → produces a focused daily brief. |
| 23 | `drift-check` | `npx skills add broomva/strategy-skills` | Compares stated priorities vs actual effort (git log + vault) → strategy drift report. |
| 24 | `strategy-critique` | `npx skills add broomva/strategy-skills` | Red-team critique of a strategy doc with gaps, risks, and missing assumptions. |
| 25 | `stakeholder-update` | `npx skills add broomva/strategy-skills` | Takes one set of facts → generates 3 versions: technical, business, customer-facing. |
| 26 | `decision-log` | `npx skills add broomva/strategy-skills` | Captures decisions with context, alternatives, rationale → links to project doc in vault. |
| 27 | `weekly-review` | `npx skills add broomva/strategy-skills` | Scans vault for weekly changes, surfaces what changed, flags what needs attention. |
