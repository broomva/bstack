# Substrate completion — overview

> Agent-readable summary of `specs/2026-05-18-substrate-completion.md`. Read the canonical spec for the full reasoning; this page is for grep / quick recall.

## What this is

The architectural roadmap for closing the bstack substrate from **v0.3.1 → v1.0.0**. It defines:

1. **What "substrate" means** — the 4 governance files + 6 hooks + CLI + templates + release pipeline that bstack ships and downstream workspaces depend on.
2. **8 architectural contracts** — Plant, Controller, Setpoint, Gate, Primitive, Hook, Companion Skill, Release. Each has explicit invariants.
3. **5 categories of gaps** in v0.3.1 — measurement, enforcement, installation, evolution, federation. Each gap cites a concrete file or behavior.
4. **9 closure phases**, ordered by dependency: docs (this PR) → metrics → status → schema → skills → doctor → upgrade → crystallize → federation → v1.0.

## Phase summary

| Phase | Version | Theme | Key deliverable |
|---|---|---|---|
| 0 | docs only | Architectural Contracts | This spec + this overview |
| 1 | v0.4.0 | Measurement | `bstack metrics collect` + per-setpoint scripts |
| 2 | v0.5.0 | Status surface | `bstack status` (text + JSON) |
| 3 | v0.6.0 | Schema versioning | `schemas/*.v1.json` + migration script |
| 4 | v0.7.0 | Companion skills | `bstack skills install` + canonical roster YAML |
| 5 | v0.8.0 | Doctor extensions | Reflexive-primitive lint + gate audit log + SLOs.md |
| 6 | v0.9.0 | Vendored upgrade | `bstack upgrade --self` + tarball + canary suite |
| 7 | v0.9.5 | Crystallization | `bstack crystallize candidates` (rule-of-three detection) |
| 8 | v0.10.0 | Federation (optional) | `bstack workspace` + global registry |
| 9 | v1.0.0 | Stability pact | Schema freeze + MIGRATIONS.md + canary green on every prior release |

## Contract index (quick reference)

| Contract | Provided by | Consumed by | Where it lives |
|---|---|---|---|
| Plant | user repo | substrate (doctor) | CLAUDE.md + AGENTS.md + .control/policy.yaml + .claude/settings.json |
| Controller | substrate (rule text) | agent (LLM) | governance file text loaded into context |
| Setpoint | substrate + user | metrics + status | `.control/policy.yaml` setpoints + `schemas/setpoint.v1.json` (Phase 3) |
| Gate | substrate + user | control-gate-hook + agent | `.control/policy.yaml` gates + `schemas/gate.v1.json` (Phase 3) |
| Primitive | substrate | agent + doctor | CLAUDE.md table + AGENTS.md sections + `references/primitives.md` |
| Hook | substrate | host CLI (Claude Code) | `assets/templates/settings.json.snippet` + scripts |
| Companion skill | skill repos | `bstack validate` + `bstack skills install` (Phase 4) | skill repos + `references/companion-skills.yaml` (Phase 4) |
| Release | bstack maintainers + release.yml | downstream installs via bstack-update-check | `.github/workflows/release.yml` + tag + GH release |

## Gap severity at a glance

| Category | Blockers for v1.0 | Major | Minor |
|---|---|---|---|
| Measurement | 0 | 2 (4.1.1, 4.1.2) | 3 |
| Enforcement | 0 | 3 (4.2.2, 4.2.3, 4.2.5) | 2 |
| Installation | 0 | 2 (4.3.1, 4.3.2) | 3 |
| Evolution | 0 | 2 (4.4.1, 4.4.2) | 3 |
| Federation | 0 | 1 (4.5.3) | 2 |
| Stability + contract | 2 (4.6.1, 4.6.2) | 2 (4.6.3, 4.6.4) | 0 |

Total: **2 v1.0 blockers, 12 major, 13 minor**.

## What this PR is NOT

This PR is **Phase 0 — docs only**. No code changes. No VERSION bump. No release. The next PR (Phase 1) starts the v0.4.0 cycle by shipping the metrics pipeline.

## Where to read next

- Full spec: `specs/2026-05-18-substrate-completion.md`
- Current invariants: `CLAUDE.md` + `AGENTS.md` + `references/primitives.md`
- Current policy: `assets/templates/policy.yaml.template`
- Release process: `RELEASE.md` (v0.2.2+)
- Contribution guide: `CONTRIBUTING.md` (v0.2.2+)
