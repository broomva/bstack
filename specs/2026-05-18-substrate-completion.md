---
title: bstack — substrate completion spec
date: 2026-05-18
status: draft
target_versions: v0.4.0 → v1.0.0
authors:
  - Carlos D. Escobar-Valbuena (broomva)
  - Claude Opus 4.7 (1M context)
supersedes: none
ratifies: implicit conventions in v0.3.1
companion: references/substrate-completion-overview.md
---

# bstack — substrate completion spec

> This spec defines the architectural contracts the bstack substrate provides and consumes, catalogues the gaps remaining as of v0.3.1, and lays out a 9-phase closure plan from v0.4.0 to v1.0.0. It is the governance-class document that future bstack releases reference for "what does done look like".

## Table of contents

1. [What "substrate" means precisely](#1-what-substrate-means-precisely)
2. [Current state inventory (v0.3.1)](#2-current-state-inventory-v031)
3. [Architectural contracts](#3-architectural-contracts)
4. [Gap catalog](#4-gap-catalog)
5. [Target architecture — the closed substrate](#5-target-architecture--the-closed-substrate)
6. [Closure phases (work breakdown)](#6-closure-phases-work-breakdown)
7. [Test plan](#7-test-plan)
8. [Risks and mitigations](#8-risks-and-mitigations)
9. [Out of scope](#9-out-of-scope)
10. [Open questions](#10-open-questions)
11. [Glossary](#11-glossary)

---

## 1. What "substrate" means precisely

Bstack is **the substrate** for agent-driven workspaces in the same sense that POSIX is the substrate for Unix programs: a small, versioned, file-shaped contract that downstream consumers depend on. The substrate is not a daemon, not a service, not a hosted system. It is:

- **Four governance files** committed to a user's repo (`CLAUDE.md`, `AGENTS.md`, `METALAYER.md`, `.control/policy.yaml`).
- **One hook table** wired into the user's `.claude/settings.json` (SessionStart, UserPromptSubmit, PreToolUse, Stop, Notification).
- **One CLI surface** installed as a skill (`bin/bstack` dispatcher + `bin/bstack-config`, `bin/bstack-update-check`, `bin/bstack-wave`).
- **One template set** that scaffolds the four governance files (`assets/templates/*`).
- **One release pipeline** that auto-tags + auto-publishes on merge (`.github/workflows/{ci,validate-release,release}.yml`).
- **One primitive taxonomy** (P1-P20) crystallized from observed patterns, with explicit promotion criteria (rule-of-three + concrete mechanism + stated invariant + stated failure mode).

The substrate provides this composition under the L3 stability budget (λ₃ ≈ 0.006). Anything the substrate ships becomes load-bearing for every downstream install; therefore the rule that governs the substrate is: **every change must be additive, every breaking change must bump minor (pre-1.0) or major (post-1.0), and every primitive promotion must clear the rule-of-three gate**.

### What the substrate is NOT

- It is **not** the agent. Bstack does not run the agent loop. The agent (Claude, Codex, etc.) executes; bstack provides the rule text the agent reads.
- It is **not** the application. A user's repo is the application. Bstack scaffolds the metalayer into the application's filesystem; it does not own the application's code.
- It is **not** an observability platform. Bstack defines setpoints in `policy.yaml` but does not (yet) operate the measurement pipeline that computes them.
- It is **not** a managed service. There is no bstack server, no hosted state, no SaaS layer. Everything bstack provides is local + filesystem + git-versioned.

This minimalism is deliberate. The substrate's job is to **make the agent + user-repo interaction reliably governed**, not to centralize control.

---

## 2. Current state inventory (v0.3.1)

What ships today, grounded in actual files in the repo at commit `e4e9186`.

### 2.1 Governance files (4)

| File | Purpose | Size | Edited by |
|---|---|---|---|
| `assets/templates/CLAUDE.md.template` | Invariants — primitive table, plugin precedence, hooks index | ~350 lines | Substrate only (P16 promotions) |
| `assets/templates/AGENTS.md.template` | Operational rules — primitive sections, reflexive trigger rules | ~600+ lines | Substrate only (P16 promotions) |
| `assets/templates/METALAYER.md.template` | (Currently workspace-only; see Gap 4.1.4) | n/a | n/a |
| `assets/templates/policy.yaml.template` | Setpoints, gates, profiles, ci_watch/ci_heal/auto_merge blocks | 208 lines | Substrate (schema), user (workspace-specific tunings) |
| `assets/templates/settings.json.snippet` | Hook wiring | ~80 lines | Substrate only |

### 2.2 Active hooks (6)

| Event | Script | Primitive | Source |
|---|---|---|---|
| SessionStart | `bstack-autoupdate-hook.sh` | P7 | bstack-shipped (v0.3.0+) |
| SessionStart | `skill-freshness-hook.sh` | P7 | workspace-supplied |
| SessionStart | `role-x-coverage-hook.sh` | P17 | `broomva/role-x` skill |
| UserPromptSubmit | `role-x-intake-hook.sh` | P17 | `broomva/role-x` skill |
| PreToolUse (Bash/Write/Edit) | `control-gate-hook.sh` | P2 | workspace-supplied |
| Stop + Notification | `conversation-bridge-hook.sh` | P1 | workspace-supplied |

### 2.3 CLI surface (4 bins + 9 scripts)

| Binary / script | Purpose |
|---|---|
| `bin/bstack` | Top-level dispatcher (v0.2.2+) |
| `bin/bstack-config` | `~/.bstack/config.yaml` reader/writer |
| `bin/bstack-update-check` | GitHub Releases API polling, cached |
| `bin/bstack-wave` | P19 parallel sub-phase dispatch |
| `scripts/bootstrap.sh` | Wires hooks + scaffolds governance files |
| `scripts/doctor.sh` | Primitive-contract compliance lint |
| `scripts/repair.sh` | Idempotent gap-fixer (governance files + policy blocks + hook merge in 0.2.3+) |
| `scripts/validate.sh` | Skill frontmatter health |
| `scripts/revamp.sh` | Full reconfiguration |
| `scripts/onboard.sh` | 4-question wizard |
| `scripts/postinstall.sh` | Post-install setup |
| `scripts/bstack-autoupdate-hook.sh` | SessionStart auto-upgrade |
| `scripts/statusline-command.sh` | Statusline integration |

### 2.4 Release pipeline (3 workflows)

| Workflow | Trigger | Action |
|---|---|---|
| `.github/workflows/ci.yml` | PR + push:main | shellcheck + JSON validate + doctor lint + vetted test suite |
| `.github/workflows/validate-release.yml` | PR (paths: VERSION, CHANGELOG.md) | VERSION ↔ CHANGELOG match + monotonic version |
| `.github/workflows/release.yml` | push:main (paths: VERSION) | Auto-tag `vX.Y.Z` + GitHub Release with CHANGELOG section as notes |

### 2.5 Primitive taxonomy (20)

Per `CLAUDE.md` §Bstack Core Automation Primitives. Categorized by enforcement mode:

**Mechanically enforced (7)**: P1 (Bridge, hook), P2 (Gate, hook + policy.yaml), P6 (Bookkeeping, CLI), P7 (Freshness, hook), P8 (Janitor, CLI), P9 (Wait, CLI), P17 (Lens, hook).

**Reflexive (13)**: P3 (Tickets), P4 (Pipeline), P5 (Fanout), P10 (Hygiene), P11 (Empirical), P12 (Persist), P13 (Dream), P14 (Dep-Chain), P15 (Snapshot), P16 (Crystallize), P18 (Audience), P19 (Orchestrate), P20 (Cross-Review).

### 2.6 Policy schema (current shape)

`.control/policy.yaml`'s shape:

```yaml
version: "1.0"
profile: governed  # baseline | governed | autonomous
setpoints:
  - id: S1..S15
    name: <string>
    target: <number|null>
    alert_below|alert_above: <number>
    measurement: <human-readable string>
    severity: blocking | informational
gates:
  hard:
    - id: G1..G11
      rule: <string>
      pattern: <regex>  # for hook-enforced gates
      measurement: <string>
  soft:
    - id: G7..G10  # advisory
profiles:
  baseline: { ... }
  governed: { ... }
  autonomous: { ... }
ci_watch: { ... }
ci_heal: { ... }
auto_merge: { ... }
```

Currently informal — no JSON Schema asserts this shape. Gap 4.4.2.

### 2.7 Companion skill roster (27)

`SKILL.md` preamble ROSTER array: `autonomous`, `cross-review`, `agentic-control-kernel`, `control-metalayer-loop`, `harness-engineering-playbook`, `p9`, `agent-consciousness`, `knowledge-graph-memory`, `prompt-library`, `symphony`, `symphony-forge`, `autoany`, `deep-dive-research-orchestrator`, `skills`, `skills-showcase`, `arcan-glass`, `next-forge`, `alkosto-wait-optimizer`, `content-creation`, `finance-substrate`, `seo-llmeo`, `brand-icons`, `pre-mortem`, `braindump`, `morning-briefing`, `drift-check`, `strategy-critique`, `stakeholder-update`, `decision-log`, `weekly-review`, `role-x`.

(Note: count drifted from `Twenty-seven` to 31 in the array — Gap 4.4.4.)

Checked by `SKILL.md` preamble (`bstack-check` Makefile target) but not auto-installed. Gap 4.3.1.

---

## 3. Architectural contracts

The substrate is the **composition of eight contracts**. Each contract specifies WHAT it is, WHO provides it, WHO consumes it, and WHAT INVARIANTS must hold. A bstack release that violates any contract is broken; a downstream install that fails to satisfy any contract is misconfigured.

### 3.1 Plant Contract

The user's workspace IS the controlled plant. The plant contract specifies what the user repo must provide for bstack to govern it.

**Provider**: user repo
**Consumer**: substrate (`bstack doctor`, hooks, agent's reading of governance files)

**Invariants** (a valid plant satisfies all):

| ID | Invariant | Verified by |
|---|---|---|
| PC-1 | `CLAUDE.md` exists at repo root and contains the primitive table | `bstack doctor` §1 |
| PC-2 | `AGENTS.md` exists at repo root and contains each primitive section | `bstack doctor` §2 |
| PC-3 | `.control/policy.yaml` exists and contains required blocks (ci_watch, ci_heal, auto_merge) | `bstack doctor` §4 |
| PC-4 | `.claude/settings.json` exists and wires the expected primitive scripts | `bstack doctor` §5 |
| PC-5 | Each primitive's mechanism (script/CLI) is reachable on disk | `bstack doctor` §6 |
| PC-6 | The plant is a git repo with a remote (so PR pipeline can fire) | (not currently checked — Gap) |

### 3.2 Controller Contract

The agent IS the L1 controller. The substrate doesn't run the controller — it provides the rule text the controller reads.

**Provider**: substrate (governance file text)
**Consumer**: agent (any LLM-driven CLI: Claude Code, Codex, Cursor, etc.)

**Invariants**:

| ID | Invariant | Verified by |
|---|---|---|
| CC-1 | Agent loads `CLAUDE.md` + `AGENTS.md` at session start | (assumed by host CLI; not bstack-verifiable) |
| CC-2 | Agent applies reflexive primitives (P10-P20) on every substantial response | sampling `docs/conversations/` (Gap 4.2.2) |
| CC-3 | Agent honors blocking gates from `.control/policy.yaml` | PreToolUse hook enforces; agent must not work around |
| CC-4 | Agent uses `Name (Pn)` form when referencing primitives | `doctor.sh` §9 (added 0.2.0) — checks rule presence in files |

### 3.3 Setpoint Contract

Every setpoint declared in `policy.yaml` must conform to the setpoint schema and have a measurement path.

**Provider**: substrate (`policy.yaml.template`) + user (workspace-specific overrides)
**Consumer**: `bstack metrics collect` (Phase 1 — Gap 4.1.1), `bstack status` (Phase 2 — Gap 4.1.2)

**Schema (target — formalized in Phase 3)**:

```yaml
- id: S<n>           # required, unique, format `S\d+`
  name: <snake_case_string>  # required, kebab/snake case
  target:            # required
    value: <number|null>
    unit: <string>   # ratio | seconds | count | bool | …
  alert:             # at least one of below/above required
    below: <number>
    above: <number>
  measurement:
    type: command | function | file_check | otel_gauge
    spec: <string>   # e.g., "make control-audit" or "bstack-doctor:section_5"
  severity: blocking | informational | advisory
  owner: substrate | user | agent
  introduced_in: vX.Y.Z
```

**Invariants**:

| ID | Invariant | Verified by |
|---|---|---|
| SC-1 | Every setpoint has a non-null measurement.spec | Phase 1 lint |
| SC-2 | Every blocking setpoint's measurement is mechanically computable | Phase 1 lint |
| SC-3 | Setpoint values bounded within sane physical ranges (e.g. rates ∈ [0,1]) | Schema validator (Phase 3) |
| SC-4 | Changes to a setpoint's target require a CHANGELOG entry | `validate-release.yml` extension (Phase 3) |

### 3.4 Gate Contract

Every gate declared in `policy.yaml` must be either hook-enforceable (with a pattern or runtime_check) or explicitly advisory.

**Provider**: substrate + user
**Consumer**: `control-gate-hook.sh` (PreToolUse), `regression-gate-hook.sh` (commit), agent reasoning (for advisory gates)

**Schema (target)**:

```yaml
- id: G<n>
  rule: <english_statement>
  enforcement:
    type: pattern | runtime_check | advisory
    spec: <regex | function_name | null>
  severity: blocking | informational | advisory
  measurement: <string>
  introduced_in: vX.Y.Z
  bypass_audit: <bool>  # if true, log + alert on bypass attempt
```

**Invariants**:

| ID | Invariant |
|---|---|
| GC-1 | Every blocking gate has `enforcement.type != advisory` |
| GC-2 | Every pattern compiles as a valid regex |
| GC-3 | Every runtime_check function name resolves to an actual script |
| GC-4 | Bypass attempts (e.g. `--no-verify` flags) are logged when `bypass_audit: true` |

### 3.5 Primitive Contract

Every P-numbered primitive in the table must satisfy the four promotion conditions. This is the most load-bearing contract because primitive proliferation erodes the L3 budget.

**Provider**: substrate
**Consumer**: agent reading `AGENTS.md` + `bstack doctor` lint

**Schema (target — already loosely enforced in `references/primitives.md`)**:

```yaml
- id: P<n>
  short_name: <Title Case>  # used in Name (Pn) prose form
  category: lifecycle | knowledge | safety | execution | meta
  mechanism:
    type: hook | script | reflex | skill | composed
    spec: <path or skill name>
  invariant: <english_statement>
  failure_mode: <english_statement>
  rule_of_three:
    - citation: <path/file.md#anchor>
      summary: <string>
    - citation: ...
    - citation: ...
  introduced_in: vX.Y.Z
```

**Invariants**:

| ID | Invariant |
|---|---|
| PC-1 (primitive) | Each Pn appears in CLAUDE.md primitives table + AGENTS.md section + `references/primitives.md` |
| PC-2 (primitive) | Each Pn's mechanism is reachable on disk |
| PC-3 (primitive) | Each promoted primitive has ≥3 citations in `research/entities/pattern/bstack-engine.md` (workspace level) or its bstack-vendored substitute |
| PC-4 (primitive) | Pn numbering is stable — renumbering requires CHANGELOG migration entry |

### 3.6 Hook Contract

Every hook wired in `settings.json.snippet` must satisfy the hook contract so Claude Code (and other hook-aware CLIs) can invoke it safely.

**Provider**: substrate
**Consumer**: Claude Code hook runner

**Invariants**:

| ID | Invariant |
|---|---|
| HC-1 | Exit code 0 on success — never block a session |
| HC-2 | Respect timeout field (default 5s, max 30s) |
| HC-3 | Diagnostics to stderr, structured output (one-line) to stdout |
| HC-4 | Accept `BSTACK_*` env overrides for testability |
| HC-5 | Idempotent — running the hook twice produces the same effect as once |
| HC-6 | Self-locate `BSTACK_DIR` if not provided in env |
| HC-7 | Degrade gracefully when optional dependencies (curl, jq, python3) absent |

### 3.7 Companion Skill Contract

Every skill in the SKILL.md ROSTER must conform to the skill packaging spec.

**Provider**: skill repo (one per skill)
**Consumer**: `bstack validate`, `npx skills add`, host CLI's skill loader

**Invariants** (already partly enforced by `scripts/validate.sh`):

| ID | Invariant |
|---|---|
| SC2-1 | Skill repo root has `SKILL.md` with valid frontmatter (name, version, description, allowed-tools) |
| SC2-2 | `description` ≤ 1024 chars (per Agent Skill spec, enforced by PR #26) |
| SC2-3 | Skill declares its bstack primitive (if any) via frontmatter `bstack_primitive: P<n>` |
| SC2-4 | Skill is installable via `npx skills add broomva/<name>` |
| SC2-5 | Skill passes `bstack validate` |
| SC2-6 | Skill's version is semver-compatible with the bstack version it requires |

### 3.8 Release Contract

Every release must conform to the release process.

**Provider**: bstack maintainers + release.yml
**Consumer**: downstream installs (via `bstack-update-check`)

**Invariants** (largely already enforced by `validate-release.yml` + `release.yml`):

| ID | Invariant |
|---|---|
| RC-1 | VERSION is semver `X.Y.Z` |
| RC-2 | VERSION monotonically increases on each release |
| RC-3 | Every release has a matching `## X.Y.Z — YYYY-MM-DD` section in `CHANGELOG.md` |
| RC-4 | Every release is tagged `vX.Y.Z` and has a matching GitHub Release |
| RC-5 | Release notes equal the CHANGELOG section verbatim (one source of truth) |
| RC-6 | Breaking changes pre-1.0 bump minor; post-1.0 bump major |
| RC-7 | Every release includes a tarball + sha256 (target — Phase 6, Gap 4.3.2) |

---

## 4. Gap catalog

Categorized list of gaps remaining as of v0.3.1. Each gap has: severity (blocker / major / minor), category, current state, target state, depends-on.

### 4.1 Measurement gaps

| ID | Severity | Current | Target | Depends on |
|---|---|---|---|---|
| 4.1.1 | Major | S1, S4, S6, S7, S8, S9 declared in policy.yaml but no measurement pipeline | Each setpoint has an executable `measurement.spec` that produces a numeric value | Phase 1 |
| 4.1.2 | Major | No `bstack status` command — current setpoint values invisible | `bstack status` renders all setpoints with current vs target, color-coded | Phase 2 (after 4.1.1) |
| 4.1.3 | Minor | No setpoint history / time series | Optional: `bstack status --history` reads `~/.bstack/metrics/log.jsonl` | Phase 2 (optional) |
| 4.1.4 | Minor | `METALAYER.md` is workspace-only — no bstack template | `assets/templates/METALAYER.md.template` shipped + scaffolded by bootstrap | Phase 3 |
| 4.1.5 | Major | RCS parameters (λᵢ) measured at workspace level but no substrate-level integration | bstack exposes L3 λ₃ measurement via `bstack status --rcs` | Phase 1+ |

### 4.2 Enforcement gaps

| ID | Severity | Current | Target | Depends on |
|---|---|---|---|---|
| 4.2.1 | Minor | Soft gates G5-G10 advisory without enforcement | Either promoted to hard (with pattern/runtime_check) or explicitly tagged `advisory: true` and audited | Phase 5 |
| 4.2.2 | Major | Reflexive primitives P10-P20 (minus P17) have no doctor lint | Doctor samples recent `docs/conversations/` entries and checks for reflexive-rule presence | Phase 5 |
| 4.2.3 | Major | Cross-Review (P20) not enforced by default — substantive PRs can merge without it | `auto_merge` block in policy.yaml requires `p20_score >= 7` for substantive PRs | Phase 5 |
| 4.2.4 | Minor | No bypass-attempt audit log when `--no-verify` is used | Gate audit log at `~/.bstack/gate-audit.jsonl` | Phase 5 |
| 4.2.5 | Major | `control-gate-hook.sh` is workspace-supplied, not bstack-shipped | Move to `scripts/control-gate-hook.sh` in bstack, ship in templates | Phase 5 |

### 4.3 Installation gaps

| ID | Severity | Current | Target | Depends on |
|---|---|---|---|---|
| 4.3.1 | Major | 27+ companion skills in ROSTER checked but not auto-installed | `bstack skills install` resolves roster + installs missing | Phase 4 |
| 4.3.2 | Major | Vendored installs cannot auto-upgrade (only nudge) | `bstack upgrade --self` downloads release tarball + sha256 + signature, swaps atomically | Phase 6 |
| 4.3.3 | Minor | No bootstrap reproducibility check — fresh install vs upgraded install can diverge | `bstack reproduce` runs bootstrap in sandbox + diffs against current | Phase 6 |
| 4.3.4 | Minor | First-time onboarding wizard is 4 questions in a skill; no programmatic equivalent | `bstack onboard --json <answers>` accepts pre-filled answers | Phase 4 |
| 4.3.5 | Minor | `npx skills add` produces vendored installs without `.git` — auto-upgrade is degraded | Document an opt-in `--git` flag pattern at the npx-skills level, or ship a `bstack convert-to-git` migration | Phase 6 |

### 4.4 Evolution gaps

| ID | Severity | Current | Target | Depends on |
|---|---|---|---|---|
| 4.4.1 | Major | P16 crystallization manual — no candidate detection from conversation logs | `bstack crystallize candidates` scans `docs/conversations/` for rule-of-three patterns | Phase 7 |
| 4.4.2 | Major | No policy.yaml schema versioning — implicit "version: 1.0" but no schema file | `schemas/policy.v1.json` (JSON Schema) + validate-release.yml checks | Phase 3 |
| 4.4.3 | Minor | No deprecation path for retired primitives (only renumbering, see 0.2.0) | `deprecated_in` + `retired_in` fields on primitive metadata, migration scripts | Phase 3 |
| 4.4.4 | Minor | SKILL.md ROSTER drifted from "27" descriptor to 31 entries | Single source of truth + auto-derive count in templates | Phase 4 |
| 4.4.5 | Minor | `references/primitives.md` not auto-generated from a structured source | Generate from a single YAML / TOML structure | Phase 3 |

### 4.5 Federation gaps

| ID | Severity | Current | Target | Depends on |
|---|---|---|---|---|
| 4.5.1 | Minor | Multi-workspace coordination not supported (each workspace independent) | Optional `~/.broomva/global/` shared state for cross-workspace queries | Phase 8 |
| 4.5.2 | Minor | Knowledge graph at `research/entities/` is per-workspace; no federation | Optional global knowledge graph index | Phase 8 |
| 4.5.3 | Major | Setpoint thresholds are workspace-local; no aggregate view | `bstack status --aggregate` rolls up across registered workspaces | Phase 8 |

### 4.6 Stability + contract gaps

| ID | Severity | Current | Target | Depends on |
|---|---|---|---|---|
| 4.6.1 | Blocker for v1.0 | Contracts in §3 not formalized — exist as prose, not enforceable schema | Each contract has a schema (JSON Schema for declarative, prose + test for behavioral) | Phase 0 (this spec) + Phase 3 |
| 4.6.2 | Blocker for v1.0 | No `bstack-canary` test suite proving substrate invariants on fresh installs | `tests/canary/*.test.sh` simulates fresh install + verifies every contract | Phase 6 |
| 4.6.3 | Major | Test suite excludes pre-existing failures (`template_lockstep`, `onboard`) — vetted allowlist | Pre-existing tests fixed; tests/*.test.sh runs in full | Phase 5 |
| 4.6.4 | Major | No SLO declarations for substrate itself (hook latency, doctor runtime, upgrade duration) | `SLOs.md` published with per-operation latency budgets, CI gate | Phase 5 |

---

## 5. Target architecture — the closed substrate

This section describes what the substrate looks like when all gaps in §4 are closed.

### 5.1 Closed-substrate invariants

A fresh `npx skills add -g broomva/bstack` on a clean machine, followed by `/bstack` in a new repo, produces a workspace that satisfies:

1. **Plant Contract** — all 4 governance files present, hooks wired, mechanisms reachable on disk.
2. **Setpoint Contract** — `bstack status` computes every declared setpoint and shows current vs target; every blocking setpoint is mechanically computable.
3. **Gate Contract** — every blocking gate has either a pattern or a runtime_check; bypass attempts logged when `bypass_audit: true`.
4. **Primitive Contract** — every Pn in the table has its mechanism on disk, ≥3 citations, doctor lint passing.
5. **Hook Contract** — every hook in settings.json exits 0 within timeout, idempotent, env-overridable.
6. **Companion Skill Contract** — ROSTER skills installable via `bstack skills install`; every skill validates.
7. **Release Contract** — version bump → CHANGELOG → tag → GitHub Release → tarball + sha256 → downstream auto-upgrade.

### 5.2 Visible artifacts after closure

A user running `bstack status` in a closed-substrate workspace sees something like:

```
bstack v1.0.0 — broomva-workspace [governed]
─────────────────────────────────────────────
Plant            ✓  4/4 governance files, 6/6 hooks wired
Setpoints        ⚠ 13/15 in target  (S4 shield_intervention 0.07 > 0.05)
Gates            ✓  11/11 reachable, 0 bypass attempts last 24h
Primitives       ✓  20/20 mechanisms on disk
Companion skills ✓  31/31 installed
Bridge           ✓  freshness 42s (target <120s)
RCS stability    ✓  λ₃ = 0.006 STABLE
Last upgrade     v0.9.2 → v1.0.0 (auto, 2h ago)
─────────────────────────────────────────────
1 informational alert. No blocking violations.
```

### 5.3 The four control loops in the closed substrate

| Loop | Period | Mechanism | Closes on |
|---|---|---|---|
| **L0 — Plant per-action** | seconds | PreToolUse hook + control-gate-hook.sh | Gate violations |
| **L1 — Agent per-turn** | per session-turn | CLAUDE.md + AGENTS.md reflexive primitives | Quality-bar (P14/P15) checks in response |
| **L2 — Meta per-release** | hours-days | CI workflows + release.yml + EGRI integration | Setpoint thresholds (S8, S9), test pass rates (S1) |
| **L3 — Governance per-quarter** | weeks | Rule-of-three crystallization + spec ratification | Primitive promotions, contract amendments |

### 5.4 Data flow

Closed substrate data flow (when complete):

```
plant signals → bstack metrics collect → ~/.bstack/metrics/latest.json
                                       → otel exporter (optional)
                                       
~/.bstack/metrics/latest.json → bstack status → human display
                              → bstack status --json → external tools / CI

docs/conversations/*.md → bookkeeping pipeline → research/entities/
                       → bstack crystallize candidates → P16 promotion proposals

PR opens → ci.yml (lint, test) → validate-release.yml (version+changelog) → reviewer
PR merges (push:main) → release.yml (tag + GH release + tarball + sha256)
Next session → bstack-autoupdate-hook.sh → git pull OR vendored tarball swap
            → bstack repair (merges any new hooks)
            → bstack doctor (validates closed-substrate invariants)
```

---

## 6. Closure phases (work breakdown)

Phases ordered by dependency. Each phase is a single PR (or small PR sequence) and bumps the version per the release contract.

### Phase 0 — Architectural Contracts (target: this PR, ratifies into v0.4.0)

**Scope**: this spec + reference doc + SKILL.md link.

**Deliverables**:
- `specs/2026-05-18-substrate-completion.md` (this file)
- `references/substrate-completion-overview.md` (one-page agent-readable summary)
- `SKILL.md` updated with roadmap section linking spec

**Tests**: markdown parses, internal links resolve, every gap cites a real file/feature.

**Out of scope**: any implementation.

**Version impact**: docs PR — no VERSION bump; lands on main without auto-release. Next implementation PR (Phase 1) starts the v0.4.0 cycle.

### Phase 1 — Setpoint measurement pipeline (target: v0.4.0)

**Scope**: make every declared setpoint computable.

**Deliverables**:
- `bin/bstack metrics collect` — runs every setpoint's `measurement.spec`, writes `~/.bstack/metrics/latest.json`
- `bin/bstack metrics observe <S-id>` — single-setpoint value
- `scripts/metrics/measure-S<n>.sh` — one script per setpoint (or a generic dispatcher reading policy.yaml)
- `tests/metrics-pipeline.test.sh` — fixtures + per-setpoint smoke
- CHANGELOG entry

**Tests**: every setpoint in `policy.yaml` produces a value; every blocking setpoint's value is numeric; cache TTL respected.

**Risks**: latency — measure ≤500ms p99. Mitigation: parallelize measurements; cache; per-setpoint TTL.

### Phase 2 — Status surface (target: v0.5.0, depends on Phase 1)

**Scope**: surface measurement results.

**Deliverables**:
- `bin/bstack status` — colored text output
- `bin/bstack status --json` — machine-readable
- `bin/bstack status --setpoint S<n>` — single setpoint
- `bin/bstack status --aggregate` — placeholder for federation (Phase 8); errors if not configured
- CHANGELOG entry

**Tests**: parses metrics JSON, formats correctly, color codes per alert thresholds, JSON mode validates against output schema.

### Phase 3 — Schema versioning + migration (target: v0.6.0)

**Scope**: formalize the policy.yaml schema + add migration support.

**Deliverables**:
- `schemas/policy.v1.json` — JSON Schema for current shape
- `schemas/primitives.v1.json` — JSON Schema for primitive metadata
- `schemas/setpoint.v1.json`, `schemas/gate.v1.json` — granular schemas
- `scripts/migrate.sh` — applies schema migrations on upgrade (initial: v1 only, no-op)
- `validate-release.yml` extension — policy.yaml schema check
- `references/primitives.md` auto-generation from a canonical YAML/TOML source
- METALAYER.md template scaffolding (closing Gap 4.1.4)
- CHANGELOG entry

**Tests**: every shipped template validates against its schema; migration is no-op for v1 → v1; schema versioning honored.

### Phase 4 — Companion skill auto-install (target: v0.7.0)

**Scope**: close the install-the-roster gap.

**Deliverables**:
- `bin/bstack skills install` — resolves ROSTER, installs missing via npx
- `bin/bstack skills status` — installed vs roster, per-skill version
- `bin/bstack skills install --interactive` — per-skill prompt
- ROSTER moved out of SKILL.md into `references/companion-skills.yaml` (single source of truth)
- bootstrap.sh calls `bstack skills install --suggest` for first-time setup (with user consent)
- `bstack onboard --json <answers>` accepts pre-filled answers (closing Gap 4.3.4)
- CHANGELOG entry

**Tests**: install on clean machine produces all 27+ skills; idempotent re-run; status correctly reflects state.

**Risks**: skill installation latency, network failures. Mitigation: parallelize, retry with backoff, per-skill timeout.

### Phase 5 — Doctor extensions + enforcement upgrades (target: v0.8.0)

**Scope**: extend doctor to lint everything specified in §3 contracts; promote soft gates; close pre-existing test failures.

**Deliverables**:
- `scripts/doctor.sh` extended sections:
  - §10 Setpoint measurement reachability
  - §11 Gate enforcement type validation
  - §12 Reflexive-primitive compliance sampling (from `docs/conversations/`)
  - §13 Skill roster completeness
- `scripts/control-gate-hook.sh` moved into bstack from workspace (closing Gap 4.2.5)
- `~/.bstack/gate-audit.jsonl` — bypass attempt audit log
- Pre-existing test fixes:
  - `tests/template_lockstep.test.sh` — update "twenty irreducible primitives" assertion
  - `tests/onboard.test.sh` — add `--non-interactive` flag to `scripts/onboard.sh`
- CI tests job runs full `tests/*.test.sh` (no allowlist)
- `SLOs.md` published with per-operation latency budgets
- CHANGELOG entry

**Tests**: doctor passes on the bstack repo + on a fresh-install fixture; pre-existing tests no longer fail; SLOs not regressed.

### Phase 6 — Vendored upgrade path + canary suite (target: v0.9.0)

**Scope**: close the vendored install gap; add reproducibility canary.

**Deliverables**:
- `bin/bstack upgrade --self` — downloads release tarball + sha256, verifies signature, swaps atomically
- `release.yml` extended to publish tarball + sha256 + cosign signature
- `bin/bstack reproduce` — runs bootstrap in sandbox + diffs against current
- `tests/canary/*.test.sh` — simulates fresh install + verifies every contract (closing Gap 4.6.2)
- CHANGELOG entry

**Tests**: vendored upgrade on a simulated v0.5.0 install lands on v0.9.0 atomically; canary suite passes on every release.

**Risks**: supply-chain — fake tarballs. Mitigation: sha256 + cosign signature verification.

### Phase 7 — Crystallization detection (target: v0.9.5)

**Scope**: machine-assist the P16 manual loop.

**Deliverables**:
- `bin/bstack crystallize candidates` — scans `docs/conversations/*.md` for rule-of-three patterns
- Heuristic: pattern recurs ≥3 times across distinct sessions, has explicit failure mode mention, has agent acknowledgement of repetition
- Output: candidate list with citations to surface to user + agent
- `bin/bstack crystallize promote <slug>` — drafts the primitive PR (does not auto-merge)
- CHANGELOG entry

**Tests**: fixture-based — known rule-of-three patterns in fixture conversations should surface; non-recurring patterns should not.

**Risks**: false-positive ritual detection. Mitigation: candidates surfaced for human approval, never auto-promoted.

### Phase 8 — Multi-workspace federation (target: v0.10.0, optional)

**Scope**: optional shared state across registered workspaces.

**Deliverables**:
- `~/.broomva/global/registry.yaml` — list of registered workspaces
- `bin/bstack workspace register` / `bstack workspace list`
- `bstack status --aggregate` rolls up across registered workspaces
- Optional knowledge graph federation: `~/.broomva/global/entities/` aggregated symlink set
- CHANGELOG entry

**Tests**: register multiple workspaces, aggregate status produces consistent rollup; deregistration cleans state.

**Risks**: shared state corruption. Mitigation: each workspace owns its own state; global is read-only aggregation.

### Phase 9 — v1.0.0 stability pact (target: v1.0.0)

**Scope**: declare the substrate's first stable contract version.

**Deliverables**:
- `schemas/*.v1.json` frozen — breaking changes require v2 schemas + migration
- API stability declared: dispatcher subcommands, env vars, file layouts, hook contracts all stable
- `MIGRATIONS.md` documents every 0.x → 1.0 migration path
- 1.0.0 release notes
- Public spec (this doc) ratified

**Tests**: every prior release (v0.2.0 → v0.9.x) upgrade-tests cleanly to v1.0.0.

---

## 7. Test plan

Per-phase test plans live in §6. Cross-cutting test infrastructure:

### 7.1 Existing test surfaces (v0.3.1)

- `.github/workflows/ci.yml` — shellcheck + JSON validate + doctor lint + vetted tests
- `.github/workflows/validate-release.yml` — VERSION + CHANGELOG match
- `.github/workflows/release.yml` — tag + release
- `tests/*.test.sh` — currently vetted allowlist: only `tests/repair-merge-hooks.test.sh`

### 7.2 Closed-substrate test surfaces (v1.0.0)

- All workflows above
- `tests/canary/*.test.sh` — substrate invariant verification on fresh installs
- `tests/contracts/*.test.sh` — each §3 contract has at least one test
- `tests/metrics/*.test.sh` — per-setpoint measurement smoke
- `tests/upgrade/*.test.sh` — pairwise upgrade paths (v0.5 → v1.0, v0.8 → v1.0, etc.)
- `bstack-canary` CI job blocks merge if any canary fails

### 7.3 SLO targets (introduced Phase 5)

| Operation | p50 | p99 | Failure mode |
|---|---|---|---|
| `bstack doctor --quiet` | < 1s | < 3s | report only |
| `bstack repair --apply-all` | < 5s | < 15s | partial-failure rollback |
| `bstack-autoupdate-hook.sh` (cached) | < 200ms | < 1s | exit silently |
| `bstack-autoupdate-hook.sh` (cold) | < 3s | < 5s | exit silently |
| `bstack-update-check --force` | < 3s | < 5s | fallback to raw VERSION |
| `bstack metrics collect` (full) | < 2s | < 5s | per-setpoint timeout |
| `bstack status` | < 500ms | < 1s | render from cache |
| `bstack upgrade --self` | < 30s | < 60s | atomic rollback |
| PreToolUse gate evaluation | < 50ms | < 100ms | fail-open with audit log |
| `release.yml` workflow | < 60s | < 120s | manual `bstack release tag` fallback |

---

## 8. Risks and mitigations

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Metrics pipeline adds session latency | Medium | Major | On-demand + cached; parallelize; per-setpoint timeout |
| Auto-install creates supply-chain risk | Medium | Major | Skill signatures + sha256 + cosign |
| Schema versioning forks user state | Low | Major | Dry-run migrations; backup before apply; never overwrite |
| Crystallization detection creates false-positives | High | Minor | Candidates surfaced for human approval, never auto-promoted |
| Vendored upgrade tarball tampering | Low | Critical | sha256 + cosign signature mandatory |
| Doctor lint becomes too strict and blocks legitimate workspaces | Medium | Major | Severity levels (informational vs blocking); profile-aware lint |
| Federation creates cross-workspace corruption | Low | Major | Strict read-only on global state; workspaces own their state |
| v1.0.0 freeze blocks legitimate evolution | Medium | Minor | Major-version bumps allowed; clearly-bounded "stability" promise |
| Substrate complexity exceeds reflexive-rule capacity (governance bloat) | Medium | Major | L3 stability budget (λ₃) enforced; rule-of-three discipline; doctor lints rule count |

---

## 9. Out of scope

The following are explicitly **not** part of the substrate completion:

- **Hosted bstack control plane / SaaS.** The substrate remains local + filesystem + git.
- **Multi-tenant authentication / authorization.** Each user owns their own substrate.
- **Agent-vs-agent coordination at scale.** P5 fanout + P19 wave dispatch cover the in-workspace case; cross-network agent coordination is a separate architecture (Life Spaces / Symphony).
- **Formal verification of every primitive.** The L3 stability budget formalism is the rigor floor; per-primitive formal proof is out of scope.
- **Custom LLM hosting.** Bstack is host-agnostic — works with Claude Code, Codex, Cursor, etc.
- **GUI / desktop app.** CLI + filesystem only.
- **Vendored install via curl-pipe-bash.** Vendored installs use `npx skills add`; bstack does not ship a curl-pipe-bash installer.

---

## 10. Open questions

1. **Setpoint history?** Should `~/.bstack/metrics/log.jsonl` retain time-series, or is the latest snapshot sufficient? Argues for history: P16 crystallization needs trends. Argues against: storage + privacy.
2. **Companion skill version locking?** Should bstack pin each skill to a specific version, or use ranges? Pinning enables reproducibility; ranges enable independent skill evolution.
3. **Federation default?** Opt-in (safer) or opt-out (more value by default)? Opt-in is the conservative choice.
4. **Substrate ships its own evaluator?** EGRI requires an evaluator for trial scoring. Should bstack ship a baseline evaluator, or always require the workspace to provide one?
5. **Substrate language coverage?** Currently shell + Python. Should we add a Rust crate (`bstack-core`) for performance-sensitive paths (e.g., metric collection)? Probably v1.x+, not v1.0.
6. **Telemetry?** Should bstack send anonymized usage telemetry (which primitives fire, gate hit rate)? Strong privacy bias says no; observability bias says yes-with-consent.
7. **Cross-LLM compatibility tests?** Should the canary suite run on Claude Code AND Codex AND Cursor to verify portability? Yes, but as v1.x.
8. **Public release notes feed?** RSS / Atom feed of bstack releases for downstream subscribers? Probably yes — minor effort, large user value.

---

## 11. Glossary

- **Substrate** — the bstack-shipped governance kit (4 files + 6 hooks + CLI + templates + release pipeline).
- **Plant** — the user's workspace; the controlled system.
- **Controller** — the agent (LLM) executing in the plant; reads governance files, applies primitives.
- **Setpoint** — a target metric the substrate tracks (Sn); declared in policy.yaml.
- **Gate** — an enforceable rule on agent behavior (Gn); declared in policy.yaml.
- **Primitive** — a composable behavior pattern (Pn); declared in CLAUDE.md + AGENTS.md.
- **Reflexive primitive** — a primitive enforced by the agent reading + applying rule text (no hook).
- **Mechanical primitive** — a primitive enforced by a hook, script, or CLI command.
- **Hook** — a script invoked by the host CLI (Claude Code) on a lifecycle event.
- **Companion skill** — a separately-installable bstack skill (e.g. `broomva/p9`, `broomva/bookkeeping`).
- **Profile** — a named subset of gates + setpoints + ci_watch + auto_merge config (baseline | governed | autonomous).
- **L3 stability budget** — λ₃ ≈ 0.006; the rate at which governance can change without destabilizing the recursive control system.
- **Rule-of-three** — promotion criterion requiring ≥3 independent instances of a failure mode before adding a new primitive.
- **EGRI** — Evolutionary Generation, Refinement, Improvement; the L2 meta-controller (`broomva/autoany`).
- **RCS** — Recursive Controlled Systems; the formal framework with stability proofs in `research/rcs/papers/`.

---

## Appendix A — Implementation order summary

```
Phase 0 (this PR)                     → docs, no version bump
Phase 1 setpoint measurement pipeline → v0.4.0
Phase 2 bstack status                  → v0.5.0
Phase 3 schema versioning + migration  → v0.6.0
Phase 4 companion skill auto-install   → v0.7.0
Phase 5 doctor extensions + soft gates → v0.8.0
Phase 6 vendored upgrade + canary      → v0.9.0
Phase 7 crystallization detection      → v0.9.5
Phase 8 federation (optional)          → v0.10.0
Phase 9 v1.0.0 stability pact          → v1.0.0
```

## Appendix B — File creations by phase (concrete)

| Phase | New files |
|---|---|
| 0 | `specs/2026-05-18-substrate-completion.md`, `references/substrate-completion-overview.md` |
| 1 | `bin/bstack-metrics`, `scripts/metrics/`, `tests/metrics-pipeline.test.sh` |
| 2 | (extends bin/bstack with `status` subcommand) |
| 3 | `schemas/policy.v1.json`, `schemas/primitives.v1.json`, `schemas/setpoint.v1.json`, `schemas/gate.v1.json`, `scripts/migrate.sh`, `assets/templates/METALAYER.md.template` |
| 4 | `references/companion-skills.yaml` (canonical), `bin/bstack` extended with `skills` subcommand |
| 5 | `scripts/control-gate-hook.sh` (moved into bstack), `SLOs.md` |
| 6 | `tests/canary/*.test.sh`, release.yml extended for tarball + sha256 |
| 7 | (extends bin/bstack with `crystallize` subcommand) |
| 8 | (extends bin/bstack with `workspace` subcommand) |
| 9 | `MIGRATIONS.md` |

## Appendix C — Naming registry (reserved CLI surface)

To avoid naming conflicts as the dispatcher grows, this is the reserved subcommand registry:

| Subcommand | Status | Owner |
|---|---|---|
| `doctor` | shipped | substrate (validation) |
| `validate` | shipped | substrate (validation) |
| `repair` | shipped | substrate (lifecycle) |
| `bootstrap` | shipped | substrate (lifecycle) |
| `onboard` | shipped | substrate (lifecycle) |
| `revamp` | shipped | substrate (lifecycle) |
| `upgrade` | shipped | substrate (lifecycle) |
| `config` | shipped | substrate (state) |
| `update-check` | shipped | substrate (state) |
| `version` | shipped | substrate (info) |
| `wave` | shipped | orchestration (P19) |
| `release` | shipped | maintainers |
| `metrics` | Phase 1 | substrate (observability) |
| `status` | Phase 2 | substrate (observability) |
| `skills` | Phase 4 | substrate (lifecycle) |
| `crystallize` | Phase 7 | substrate (evolution) |
| `reproduce` | Phase 6 | substrate (verification) |
| `workspace` | Phase 8 | substrate (federation) |

Subcommands SHALL NOT be added outside this registry without a CHANGELOG entry referencing this spec.

---

## Closing notes

This spec is the canonical answer to the question: "what does done look like for the bstack substrate?"

It is deliberately verbose because the substrate is load-bearing for every downstream workspace that adopts it. The L3 stability budget (λ₃ = 0.006) demands that governance changes be rare and deliberate; the corollary is that when we *do* change governance, the change must be specified with enough rigor that it does not require re-specification on the next pass.

Every phase in §6 is a discrete shipping unit. None are speculative — each addresses a gap with a concrete cited file in v0.3.1. The closure plan is conservative: 9 phases, ~8 minor releases, one major release. That cadence (≈ 1 release every 1-2 weeks) keeps the L3 margin intact while making steady progress toward a substrate that any user can adopt with confidence.

The substrate is the foundation. Everything built on top of it inherits its reliability. That is the standard this spec holds itself to.
