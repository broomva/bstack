# Changelog

## 0.30.0 — 2026-07-04

### feat: close the self-improvement loop end-to-end across RCS L0–L3 (BRO-1696)

bstack shipped a control loop that only *looked* closed. The two audit sensors read
fields Claude Code never emits — `l0-tool-audit` keyed on `tool_result.latency_ms`
(100% null), `l1-reflex-audit` on `tool_call_count` (100% zero, then it grepped the
agent's own prose, so the sensor was correlated with the controller it graded). The
SessionStart wire called `bookkeeping wakeup`, a subcommand that never existed, so the
actuation channel emitted **0 bytes every session**. And `doctor` §23 certified the loop
as "wired + running + closing" by checking only that an audit file *existed* and was
recent — so a 100%-null sensor passed. Sensor faked, wire absent, checker blind: an
**open loop wearing a closed loop's clothes**.

This replaces all three with a real, transcript-derived loop whose verifier is causally
independent of what it grades (`h ⟂ U`).

### Added

- **`scripts/leverage-sensor.py`** — the real sensor `h`. Reads raw session transcripts
  (`message.content[].type`, `tool_result.is_error` — facts the agent cannot fake) and
  derives 6 metrics, each tagged by RCS recursion level: **L0** tool-error-rate /
  read-before-edit-rate / permission-bypass · **L1** continue-nudges-per-session · **L2**
  kg-load-rate · **L3** meta-work-session-ratio (the anti-capture governor). Workspace and
  Claude Code transcript dir are *derived*, not hardcoded, so it runs in any workspace.
  `--closure` emits a machine-readable per-level verdict and exits non-zero if the loop is
  open (CI/doctor gate). `--brief`/`--cached`/`--throttle` power the fast hook paths.
- **`scripts/knowledge-wakeup-hook.sh`** — the actuation wire `U`. SessionStart injects the
  worst-gap metric + its named corrective actuator (and any not-closed / unsigned-reference
  warning) into the new context. Replaces the phantom `bookkeeping wakeup` call.
- **`assets/templates/leverage-setpoints.yaml`** — the reference `r`, one setpoint per
  metric with `level:`, `direction:`, `target`/`alert`, and a named `actuator`. Ships
  `authored_by: bstack-default` — until a human signs `r0`, the sensor + doctor report
  `reference_authored: false` (endogenous-reference-contamination: a reference the agent
  authored makes "converged" and "drifted" the same event; signing r0 restores causal priority).
- **`tests/loop-closure.test.sh`** — CI regression: a fixture with a live signal at every
  RCS level proves closure PASSES (sensor_live + levels_closed L0–L3) and a 0-session dead
  sensor proves it FAILS (`--closure` exits non-zero) — the un-blinding, machine-checked.

### Changed

- **`scripts/doctor.sh` §23** is now **content-aware**. It reads the sensor's own per-level
  closure verdict instead of checking file existence: a dead sensor (all metrics null over 0
  sessions) is a **gap**, a level with no live metric is a **gap**, and a `bstack-default`
  reference raises a causal-priority warning. Legacy `L0-audit`/`L1-audit` wiring is detected
  and flagged for migration.
- **`assets/templates/settings.json.multi-layer-hooks.snippet`** wires the real `loop-sensor`
  at Stop (throttled ≤6h) instead of the fake PostToolUse/Stop audit hooks;
  **`settings.json.snippet`** adds the `loop-wire` at SessionStart.
- **`scripts/install-rcs-stability.sh`** substitutes `${BROOMVA_WORKSPACE}`/`${BROOMVA_HOME}`
  (not just `$BSTACK_REPO`) when merging the snippet.
- **`tests/canary/01-fresh-bootstrap.test.sh`** asserts the new `loop-sensor` + `loop-wire`
  markers; **`references/new-workspace-flow.md`** documents the real sensor, wire, and §23.

### Notes

- The fake `scripts/l0-tool-audit-hook.sh` / `l1-reflex-audit-hook.sh` remain on disk (no
  longer wired) for backward compatibility with workspaces that already reference them; they
  are superseded and a future release removes them. `compute-lambda`/§16–§18 still read the
  legacy audit logs and degrade to "no data" on new installs — repointing them at the leverage
  sensor is a tracked follow-up.
- Dogfooded (P11): sensor verified live against real `~/broomva` transcripts (all 4 levels
  live); §23 verified to PASS a real loop and FAIL a dead sensor; shellcheck + JSON-validate +
  full `tests/*.test.sh` green. Primitive count unchanged (**20**). `VERSION` 0.29.2 → 0.30.0.

## 0.29.2 — 2026-07-01

### fix: self-warning banner makes the skills.sh partial-install self-diagnosing (BRO-1633)

BRO-1632 documented around the skills.sh footgun but didn't eliminate it: `npx skills add broomva/bstack` still reports "✓ Installed 1 skill" while landing **only** the root `SKILL.md` and dropping `bin/`, `scripts/`, `schemas/`, and templates (the repo-root-`SKILL.md` case of vercel-labs/skills#1523) — a deceptive success with **no working CLI**.

### Fixed

- **`SKILL.md` — self-warning banner.** The one file that survives the partial install now opens with a `[!CAUTION]` banner: if this file is all you have, your install is broken, here is the clone + `bstack bootstrap` fix (and the `--skill bstack` primer path for agents who only want the primitives). Converts the silent-deceptive install into a self-correcting one — the moment an agent loads the lone `SKILL.md`, it's told it's broken and how to fix it. (Relocating the root `SKILL.md` to a subdir was the alternative — rejected: it breaks in-repo `/bstack` loading and fails without telling the user what to do.)

### Added

- **`tests/skill-selfwarn-banner.test.sh`** — asserts the banner (marker + broken-install warning + clone-install fix command) stays near the top of `SKILL.md`.

### Notes

- Primitive count unchanged (**20**). `VERSION` 0.29.1 → 0.29.2. Full `tests/*.test.sh` green.

## 0.29.1 — 2026-07-01

### fix: fresh-VPS install — kill 404s from deleted repos, correct self-install, status workspace resolution (BRO-1632)

A fresh-VPS dogfood surfaced three breakages, all downstream of the BRO-1602 standalone-repo consolidation + bstack being treated as a skill.

### Fixed

- **`scripts/bootstrap.sh` — 404s on every fresh-host bootstrap.** The phase-1 skill install used a **hardcoded `SKILL_REPOS` map** pointing at ~18 standalone repos that no longer exist (`broomva/agentic-control-kernel`, `broomva/p9`, `broomva/persist`, `broomva/prompt-library`, `broomva/finance-substrate`, `broomva/deep-dive-research-skill`, …) — deleted in BRO-1602 — plus wrong entries (`content-creation`/`brand-icons` → `broomva/bstack`). Replaced the map + install loop with **delegation to `bin/bstack-skills install`** (roster-driven from `references/companion-skills.yaml`, inheriting the BRO-1588 `--skill <name> -g` fixes). The roster is now the single source of truth; there is no second skill→repo declaration to drift.
- **`README.md` — bstack half-installed via `npx skills add broomva/bstack`.** bstack is a **CLI + governance substrate** (root `SKILL.md` beside `bin/scripts/schemas/assets/`), so skills.sh hit the repo-root-`SKILL.md` case of [vercel-labs/skills#1523](https://github.com/vercel-labs/skills/issues/1523) and dropped everything but `SKILL.md`. Self-install is now documented as **`git clone` + `bstack bootstrap`**; the primitives *primer* is available separately as `npx skills add broomva/skills --skill bstack`.
- **`bstack status` — false blocking violations.** `bin/bstack-status` resolved the workspace as `${BROOMVA_WORKSPACE:-$PWD}` and never consulted the config `workspace:` key, so an unset env var + a cwd outside the workspace read the wrong `.control/` tree. New shared resolver `scripts/lib/resolve-workspace.sh` (`$BROOMVA_WORKSPACE` → `bstack-config get workspace` → `$PWD`); `bstack-status` resolves + **exports** `BROOMVA_WORKSPACE` so every collector it spawns inherits the correct root.
- **`bin/bstack-skills` — silent zero-install on a PyYAML-absent fresh host (found by P20 review — the exact BRO-1632 target scenario).** `parse_roster` required PyYAML; a fresh Ubuntu VPS has `python3` but not `python3-yaml`, so the import failed → 0 rows → `bstack bootstrap` reported success while installing **nothing**. Added a dependency-free awk fallback parser (byte-identical output on the roster) + a 0-row guard in `cmd_install` (fail loud when a non-empty roster parses to zero rows).
- **`scripts/revamp.sh` — a SECOND hardcoded map (found by P20 review).** `bstack revamp` (a shipped command) had its own `declare -A REPOS` over the same deleted repos → 404'd every invocation. Delegated to `bstack-skills install --all`.
- **Dead install references swept** — `scripts/doctor.sh`, `SKILL.md` (both the quick-start block and the bootstrap description), `references/primitives.md`, `references/quickstart.md`, `references/new-workspace-flow.md`, `RELEASE.md`, `scripts/postinstall.sh`, and the **README "Stack layers" table** (every `https://skills.sh/broomva/<deleted>` link 404'd) → all now point at the monorepo `--skill <name>` form or the clone-install. Stale skill counts (31/30/27) corrected.
- **`resolve-workspace.sh`** — added a `$PWD`-is-a-workspace tier (a cwd carrying `.control/policy.yaml` wins over the config default, so `cd other-ws && bstack status` reports the workspace you're in) and made `bstack-status` source the lib **fail-soft** (falls back to inline resolution instead of aborting under `set -e` if the lib is absent).

### Added

- **`tests/bootstrap-roster-source.test.sh`** — install-mechanism drift guard across **all** installer scripts (bin/ + scripts/): no literal standalone-repo install, no variable-indirected `npx skills add "$repo"` loop (the pattern the maps fed), no hardcoded `declare -A` skill→repo map, and both `bootstrap.sh` + `revamp.sh` delegate to `bstack-skills`. (The first version only scanned bootstrap.sh with a literal-match grep — it would have missed the revamp.sh map + variable indirection the P20 review caught.)
- **`tests/resolve-workspace.test.sh`** — 4-tier resolution (env → cwd-is-workspace → config → PWD).

### Notes

- Primitive count unchanged (**20**). Install-correctness fixes. `VERSION` 0.29.0 → 0.29.1. Full `tests/*.test.sh`: 27/27. **P20 cross-model review (3 lenses) ran on the first cut and found the PyYAML silent-no-op, the revamp.sh second map, an inadequate drift guard, and 6 more dead-ref surfaces — all fixed in this same version.** bstack stays a standalone product (not folded into broomva/skills — a CLI isn't skill-shaped); companion primer skill ships separately in the monorepo.

## 0.29.0 — 2026-06-29

### feat: Tier-2 `permissions:` + `trust_tiers:` policy schema — path-based capability model, never-auto-granted set, signed grants approval flow (BRO-1600)

Lands the schema half of the indirect-prompt-injection defense: a declarative capability layer in `assets/templates/policy.yaml.template` (template internal version `1.0` → `1.1`) that makes an agent's authority over the filesystem an explicit, reviewable surface instead of an implicit consequence of which tools are wired. Schema-first by design — the runtime hooks that enforce these blocks are sequenced follow-ups, matching the precedent set by `write_gate` (declared before its checker shipped).

### Added

- **`permissions:` block (path-based capability model).** Declares per-path-glob capabilities (read / write / execute) so authority is granted by location, not inherited globally. A `never_auto_granted:` set names capabilities that an agent can **never** self-grant in-loop (the dangerous-by-default surface — credential paths, governance files, the grant ledger itself); acquiring them requires the explicit approval flow below, never an in-session decision.
- **`trust_tiers:` block (T0–T4).** Five named trust tiers that scope what a given actor/context is permitted to do, from untrusted input (T0) up to operator-authorized (T4). Capabilities and paths bind to a minimum tier; requests below tier are refused.
- **Signed-grant approval flow (`grants.jsonl`).** An append-only, signed grant ledger: escalations to a `never_auto_granted` capability are recorded as signed grant entries, so every elevation is attributable and auditable after the fact rather than silently assumed.
- **`tests/policy-template-schema.test.sh`** — schema guard: asserts the template carries internal version `1.1` and that the `permissions:`, `never_auto_granted:`, `trust_tiers:` (T0–T4), and `grants.jsonl` flow are all present and well-formed, so the schema can't silently regress.
- **`references/security-primitives.md`** — documents the capability model, the T0–T4 tiers, the never-auto-granted set, and the signed-grant flow; cross-links `specs/2026-05-15-indirect-prompt-injection-defense.md §5`.

### Notes

- **Schema-first, enforcement-sequenced.** This ships the declarative contract only; the PreToolUse runtime hooks that enforce `permissions:` / `trust_tiers:` are the sequenced follow-ups per `specs/2026-05-15-indirect-prompt-injection-defense.md §5` — the same land-schema-then-wire-checker order `write_gate` used.
- Primitive count unchanged (**20**). This is a policy-schema addition, not a new P-row.
- `VERSION` 0.28.1 → 0.29.0 (minor: additive schema feature, backward-compatible).

## 0.28.1 — 2026-06-28

### fix: `bstack-skills install` lands every skill, in the dir `status` reads (BRO-1588)

Caught by a clean-room end-to-end dogfood (P11) of the merged skills-monorepo setup: every guard test and the mocked install test passed, but the **real** `bstack-skills install` only installs the FIRST roster skill, then silently stops (`Installed: 1`). A second, compounding defect: even that one skill lands where `status` can't see it.

### Fixed

- **`bin/bstack-skills` `cmd_install` — stdin drain (the truncation).** The loop reads the roster via `done < <(parse_roster)`, so the loop body's stdin **is** that pipe. The per-skill `$NPX_CMD …` call had no stdin redirect, so the real `npx skills add` subprocess drained the remaining roster rows off the pipe and the enclosing `read` hit EOF after skill #1. Fix: `… </dev/null >/dev/null 2>&1`. Empirically: exact pre-fix code installs 1/13 required; with `</dev/null`, 13/13. Pre-existing — predates the BRO-1584 `--skill` change; any stdin-reading subprocess in that loop drains it.
- **`bin/bstack-skills` `cmd_install` — global-install landing (the mismatch).** Production `add_args` had no `-g`, so the skills CLI installed **cwd-relative** (`./.agents/skills`), while `status` only checks `~/.agents/skills` + `~/.claude/skills` → an install↔status mismatch. Fix: append `-g`. Verified `-g` lands in the global `~/.agents/skills` (plus the per-agent `~/.claude/skills` when that agent's home dir is present at install time), so `status` reports `installed`; and it exits 0 even when exotic agents (Eve/PromptScript) reject global install. (Caveat: if `~/.claude` does not yet exist at install time the skill lands only in `~/.agents/skills`; `bstack skills install` is normally run from inside a Claude Code session where `~/.claude` exists, so the per-agent entry is created.)
- **`bin/bstack-skills` `cmd_install` — interactive prompt (latent, same root cause).** The `--interactive` `read -r -p … reply` read roster rows as the y/N answer; now reads from `</dev/tty`.

### Added

- **`tests/skills-install-no-stdin-drain.test.sh`** — regression guard: mocks `NPX_CMD` with a subprocess that **drains stdin** (like the real CLI) and asserts every roster skill is attempted (count == N, not 1). The existing `skills-install-uses-skill-flag.test.sh` mock does not read stdin, so it could not catch this — proven red (buggy) → green (fixed).
- **`tests/skills-install-uses-skill-flag.test.sh`** — added a `-g` assertion (every `broomva/skills` install carries `-g`, so it lands status-visible).

### Notes

- Primitive count unchanged (**20**). Installer correctness fix, not a new P-row.
- `VERSION` 0.28.0 → 0.28.1.

## 0.28.0 — 2026-06-11

### feat: Pattern H — non-code dogfood (knowledge-vault) + detection (BRO-1482)

Surfaced by dogfooding bstack into a non-code repo (a research vault): the full governance + control plane install language-agnostically and the P2 gate fires, but the dogfood cookbook had **no non-code entry** (only tauri/nextjs/expo/rust/rest/mcp/trading), so detection fell through to "Pattern Z — Unknown" and the agent had to author the validation predicate from scratch.

### Added

- **`references/dogfood-patterns.md` — Pattern H: Knowledge vault / Document repo (NON-CODE).** The dogfood pattern for repos with no build/test suite (research vaults, Obsidian graphs, ADR/decisions folders, spec libraries, manuscripts, contract repos). Ships a reference predicate (`vault-check.py` — required frontmatter + every `[[wikilink]]` resolves, wired as `make check`; the non-code analog of a test suite, `h ⟂ U`), a five-tier predicate menu (schema / cross-reference / checklist / rubric-judge / human-sign-off), the stack=knowledge-vault Dogfood Plan, and gotchas (well-formed ≠ correct; keep the predicate independent of the authoring agent).
- **Detection branch (`scripts/doctor.sh §13` + `scripts/onboard.sh`):** a markdown-dominant repo with no code build manifest (`entities/`/`.obsidian/`/`vault/` dir or ≥5 `.md` files) → `knowledge-vault`. Added a correct `CODE_MANIFEST` flag (a per-file loop — `ls a b c` returns non-zero if ANY one is missing, so it can't answer "are they all absent"). Checked **last before the Pattern Z fallback** so every code pattern gets first refusal; `no_code_manifest` keeps it from firing on a real codebase.
- Detection-algorithm pseudocode + the inline detection summary in the cookbook updated to include Pattern H.

### Notes

- Primitive count unchanged (**20**). This widens the P11 cookbook + detection, not a new P-row.
- Validation: dogfooded on a real non-code repo (research vault → `knowledge-vault`; vault-check RED→GREEN) and confirmed a manifest-less code repo does not misfire. Provenance: the live non-code dogfood (bootstrap landed identically, P2 gate blocked force-push/secret exit 2).
- `VERSION` 0.27.1 → 0.28.0.

## 0.27.1 — 2026-06-08

### fix: L3 rate gate counts mutations, not creations — unblocks the initial bootstrap commit (BRO-1435)

Found via live dogfooding (P11) of the v0.27.0 autonomous-loop demo: `bstack bootstrap` activates the L3 rate gate, then the user's very first `git commit` of the freshly-*scaffolded* governance is blocked — the gate counted the 5 newly-**created** L3 files as 5 L3 mutations (`5/1 EXCEEDED`). Creation is not mutation: there is no prior governance state to destabilize.

### Fixed

- **`scripts/l3-rate-gate.sh` — staged count:** a staged L3 path counts as a mutation only if it already exists at `HEAD` (`git cat-file -e HEAD:<path>`). Newly-created L3 files (and the first-ever commit, where `HEAD` is absent) are exempt.
- **`scripts/l3-rate-gate.sh` — committed count:** uses `git log --diff-filter=M` so commits that *added* L3 files (e.g. the bootstrap scaffold) don't consume the per-window budget; only commits that *modified* them do. Also switched the counter from `wc -l` on a `format:` stream to `grep -c .`, fixing a latent off-by-one (a single committed L3 mutation previously counted as 0).
- The pre-commit template calls the script (no duplicated logic), so this fixes both `bstack bootstrap` Day-1 UX and every deployed `.githooks/pre-commit`.

### Added

- **`tests/l3-rate-gate.test.sh`** — 4 hermetic cases: (A) creating 5 L3 files is exempt, (B) 1 modification is within budget, (C) a 2nd modification in the same window is blocked, (D) non-governance changes are ignored. 4/4 pass.

### Notes

- The gate's actual purpose is preserved: governance *modifications* are still rate-limited to one per `τ_a₃` window (verified by case C). Only *creation* is exempt.
- Primitive count unchanged (**20**). `VERSION` 0.27.0 → 0.27.1.

## 0.27.0 — 2026-06-08

### fix: ship + deploy the dangling P1/P2/P6/P7 hook scripts; scaffold METALAYER + schemas; document the two-flow bootstrap (BRO-1431)

Surfaced by building a from-scratch RCS/RSI template (rcs-template) as a gap-discovery probe for bstack's own bootstrap. Found a real safety bug plus two scaffold omissions, and the absence of a generative flow.

### Fixed

- **Dangling hook scripts (safety bug).** `settings.json.snippet` wired the P1 (conversation-bridge), P2 (control-gate), P6 (knowledge-catalog-refresh), and P7 (skill-freshness) hooks at `${BROOMVA_WORKSPACE}/scripts/*.sh`, but bstack shipped none of those scripts and bootstrap never copied them. Result: every workspace bootstrapped anywhere but the bstack origin had a **non-functional control gate (P2)** — the safety shield silently no-op'd because Claude Code invoked a script that did not exist. `doctor §7` detected the gap but nothing closed it. Now bstack **ships** all four (`scripts/{control-gate,skill-freshness,conversation-bridge,knowledge-catalog-refresh}-hook.sh`, self-contained + `$CLAUDE_PROJECT_DIR`-portable) and **deploys** them into `$WORKSPACE/scripts/` — `bootstrap` Phase 3.1 and `repair`'s `deploy_workspace_hooks` (idempotent, never overwrites). Dogfood proof: on a fresh workspace with no bstack on PATH, the deployed control-gate blocks `git push --force` (exit 2) and allows `git status` (exit 0).

### Added

- **`bootstrap` Phase 2 now scaffolds `METALAYER.md` + `schemas/{state,action,trace,evaluator,egri-event}.schema.json`** (the control-systems manifest + typed interfaces). Templates added under `assets/templates/` + `assets/templates/schemas/`. Previously only CLAUDE/AGENTS/policy/arcs scaffolded; the manifest and typed contract were omitted.
- **Two-flow bootstrap doc (SKILL.md).** Names and sequences the **structured flow** (deterministic scaffold — the lossless floor, no LLM) → **generative flow** (agent-authored, workspace-tailored setup — bespoke, to-the-ceiling) → **verify** (`bstack doctor` gates both). Mirrors the P18 Audience Category-B-projection vs Category-C-generative split, applied to workspace setup. Invariant: never wire a hook whose script isn't deployed.

### Notes

- Primitive count unchanged (**20**). This is a bootstrap correctness + completeness fix, not a new P-row.
- Hook-resolution is now consistent for the workspace-resolved hooks (all four deployed into `$WORKSPACE/scripts/`, self-contained on a fresh clone); the L0/L1 audit hooks remain `$BSTACK_REPO`-referenced (already resolving).
- `VERSION` 0.26.0 → 0.27.0.

## 0.26.0 — 2026-06-05

### feat: `bstack skills audit --require-tests` — skill-script test gate (BRO-1411 slice 2)

Adds a sixth audit report and a CI-gateable correctness check. Origin: `/checkit` on Garry Tan's "skillify" essay surfaced that bstack's `skills audit` covers *hygiene* (budget / duplicate / reachability) but never *correctness* of the skill layer. This is skillify step 3 ("unit tests on the deterministic code"), built bstack-native — the correctness counterpart to the existing five hygiene reports.

### Added

- **`scripts/skill-audit.py` report 6 — "Untested deterministic code".** Flags any skill that ships deterministic code (`scripts/*.{py,sh,mjs,js,ts}` or root-level) but no test file (`test_*.py`, `*_test.py`, `*.test.*`, `test_*.sh`). Markdown-only skills are exempt (nothing to test); test files themselves don't count as code. Always shown (informational); `--json` gains an `untested` array.
- **`--require-tests` flag** — escalates the report to a hard gate: exit 1 if any skill ships untested code. Default (no flag) stays exit 0, so the report informs without breaking existing callers; CI opts into enforcement.
- **`tests/skill-audit.test.sh`** — 4 new hermetic cases (T10–T13): untested detection (code-no-tests flagged, code+tests and md-only exempt), gate exit-1 under `--require-tests`, informational exit-0 without it, human-report section presence. 14/14 pass.

### Notes

- First real run over `~/broomva/skills`: **19 skills ship deterministic code with no tests** (the bstack analog of GBrain's "6/40 dark skills" finding). Informational today; backlog candidates for test backfill.
- Primitive count unchanged (**20**). Widens the `bstack skills audit` surface — not a new P-row. The `bstack-engine` "Skill-QA discipline" ledger row (workspace KG) tracks promotion eligibility.
- `VERSION` 0.25.0 → 0.26.0.

## 0.25.0 — 2026-06-05

### feat: doctor advisory + repair backfill for the Development Philosophy section (BRO-1409)

Follow-up to 0.24.0 (BRO-1406). The scaffold's idempotent-never-overwrite policy means newly-templated *content* never reaches existing workspaces — so the Development Philosophy section shipped only to new installs. This closes that gap for the section: `bstack doctor` surfaces it and `bstack repair` backfills it.

### Changed

- **`scripts/doctor.sh`** — new §4b advisory: if `AGENTS.md` lacks `## Development Philosophy`, print an `[info]` nudge to run `bstack repair`. Informational only — a pre-0.24.0 workspace legitimately lacks it; it is **not** a GAP and does **not** fail `--strict` (mirrors the §12 Pillars / §13 dogfood convention).
- **`scripts/repair.sh`** — new `backfill_philosophy_section()`: extracts the section verbatim from the template (heading → `## Bstack Core Automation Primitives` anchor, exclusive) via files only (no shell interpolation of content), and inserts it before the target's own anchor line in both `AGENTS.md` and `CLAUDE.md`. Runs before the "fully bstack-compliant" early-exit (same pattern as the hook merge), since the advisory is not a GAP. Idempotent (skips if present) and non-destructive (insert-only; skips with a warning if the anchor is absent). Honors `--dry-run` / `--apply-all`.
- **`tests/philosophy-backfill.test.sh`** — new: backfill + position + content-integrity + idempotency + `--dry-run` + missing-anchor + doctor-advisory (13 assertions).
- **`references/new-workspace-flow.md`** — documents §4b + the repair backfill.

### Notes

- Primitive count unchanged (**20**). Governance-substrate tooling, not a P-row.
- Backfill targets *content* gaps the never-overwrite scaffold skips — a general pattern (the hook merge does the same for `.claude/settings.json`).
- `VERSION` 0.24.0 → 0.25.0.

## 0.24.0 — 2026-06-05

### feat: scaffold a Development Philosophy section into AGENTS.md/CLAUDE.md on install (BRO-1406)

`bstack bootstrap` now scaffolds a **Development Philosophy** section into a new workspace's `AGENTS.md` (full section) and `CLAUDE.md` (anchor). It states four guiding principles — Think Before Coding · Simplicity First · Surgical Changes · Goal-Driven Execution — and backs each with the primitive(s) that hold it: Think→P14+P15, Simplicity→P20, Surgical→P14+P20, Goal-Driven→P11+P19, with a note that enforcement strength varies (P14/P15 are hard predicates; P20 is a judgment gate). Previously the scaffold deployed the primitive contract without this stated intent.

### Changed

- **`assets/templates/AGENTS.md.template`** — new `## Development Philosophy` section between Self-Meta Definition and the primitives table (so the *why* frames the *how*): 4-principle → backing-primitive table, the binding Ritual-vs-Substance rule, and an explicit invitation to extend it with project-specific principles.
- **`assets/templates/CLAUDE.md.template`** — short philosophy anchor after Identity, linking to `AGENTS.md#development-philosophy`.
- **`references/new-workspace-flow.md`** — deployed-files table note updated to describe the philosophy section.

### Notes

- **No code change** in `scripts/bootstrap.sh` — Phase 2 (`scaffold_governance_file`) copies whatever the templates hold. Idempotent-never-overwrite: **new** installs get the section automatically; existing workspaces are unaffected (backfill is the deferred `bstack doctor`/`repair` next step).
- **Not a new primitive.** Primitive count stays **20**. This is governance-substrate content, not a P-row — respects the L3 stability budget (λ₃≈0.006).
- Companion KG artifact (workspace repo, not this repo): `research/entities/tool/karpathy-claude-md-guidelines.md` documents the lineage + the principle→primitive mapping.
- `VERSION` 0.23.2 → 0.24.0.
- **Deferred:** (1) a `bstack doctor` check that lints for the section (makes it contract-enforced + lets `repair` backfill existing workspaces); (2) dogfood into bstack's own root governance + `~/broomva`.

## 0.23.2 — 2026-06-04

### docs: README sync to current contract + bench surfaced + P11 cross-reference (BRO-1376)

Closes a documentation-reconciliation gap surfaced by a post-ship audit of BRO-1205 (bench MVP) + BRO-1211 (Databricks live mode). The canonical surfaces (SKILL.md, `references/provider-standards.md`, CHANGELOG, spec, KG research entities) were already current; the public `README.md` was frozen at the P11 era and violated the CLAUDE.md Self-Documenting Standards rule #3 (counts must match SKILL.md as the authoritative source). `bstack doctor` does not lint the README, so the rot was not CI-enforced.

- **CHANGED** `README.md` — synced to the current contract:
  - "Eleven irreducible primitives" → **twenty**; primitive table extended P1–P11 → full **P1–P20** (wording from SKILL.md's enforcement table).
  - "28 curated skills" → **30** (matches SKILL.md authoritative count) across intro, Stack-layers header, and bootstrap description.
  - Commands section: was six (bootstrap/doctor/repair/status/validate/revamp); now also documents **`bench`**, `wave`, `crystallize`, `metrics`, `skills` under an "Orchestration & observability" subsection. `bench` links to `references/provider-standards.md`.
  - Reasoning-enforced primitive set corrected (P6, P9–P20) vs mechanism-enforced (P1, P2, P4, P5, P7, P8); closing narrative "eleven" → "twenty".
- **CHANGED** `references/primitives.md` — P11 Empirical Feedback Loop section now lists `bstack bench` as the dedicated P11 *measurement* substrate (table row + paragraph), cross-referencing `provider-standards.md` and the bench spec.
- **NOTE** Out of scope (separate audit): the deeper skill-count reconciliation (SKILL.md says 30 curated; `companion-skills.yaml` lists 65 full roster incl. optional). README follows SKILL.md per rule #1.

No code, no behavior change. Companion KG artifact (workspace repo, not this repo): `research/entities/pattern/openai-compatible-provider-abstraction.md` documents the provider-abstraction architecture that BRO-1211 introduced.

## 0.23.1 — 2026-06-01

### docs: P6 reflex tightening — "a reflex, not a request, **and never a question**" (BRO-1288)

Closes the **permission-to-document anti-pattern**: agents asking *"do you want me to create an entry / file this into the knowledge graph?"* instead of capturing autonomously.

The P6 reflex already said *"a reflex, not a request"* but every statement framed it as *run the `bookkeeping run` pipeline unprompted* — about the pipeline **invocation**, never the **capture act**. So an agent could honor "not a request" (didn't wait to be told to run the pipeline) while still gating *creation of the artifact* behind a user yes/no. Adds the third clause — **"and never a question"** — closing the interrogative leak.

Statement-tightening of P6, **not** a new primitive (same failure-mode class; respects the L3 stability budget λ₃≈0.006; mirrors the 2026-05-29 PR-comment-resolution decision). **Primitive count stays 20.**

### Changed

- **`references/primitives.md`** — P6 Reflexive Trigger Rule: opening tightened to *"a reflex, not a request, and never a question"*; new trigger #4 (file discrete graph-worthy items proactively); a **"Never a question"** paragraph naming the permission-to-document anti-pattern. Capture stays bounded by the Nous gate (proactive ≠ indiscriminate).
- **`assets/templates/AGENTS.md.template`** — same P6 reflex tightening, so the rule propagates into every workspace `bstack` bootstraps.
- **`assets/templates/CLAUDE.md.template`** — P6 primitive-table invariant cell now states the proactive-capture rule.

### Notes

- No CLI/behavior change; documentation/governance only. Canary unaffected.
- `VERSION` 0.23.0 → 0.23.1.
- Workspace-side surfaces (`CLAUDE.md` §Ritual-vs-Substance, `AGENTS.md`, entity `pattern/proactive-documentation.md`, bstack-engine ledger) ship in the workspace repo PR for BRO-1288.

## 0.23.0 — 2026-05-29

### fix+feat: gitignore-aware, public-repo-aware, non-destructive bootstrap (issue #67)

Found dogfooding v0.22.0 on existing real repos (stimulus, broomva.tech): bootstrap is built for *fresh* workspaces and did two unsafe things + lacked one needed capability on repos with their own hooks/CI/gitignore.

### Fixed

- **`install-l3-stability.sh` no longer clobbers a TRACKED `.githooks/pre-commit`.** On a repo with a committed pre-commit (e.g. broomva.tech's multimedia-asset validator), bootstrap overwrote it with the bstack L3 rate-gate hook — moving the original to `.pre-commit.local` — **even when `core.hooksPath` ≠ `.githooks`**, so the bstack hook never even fired. Now: if the hook is git-tracked, **skip + warn** (the committed hook is authoritative; `--force` still allows the move-aside). An *untracked* hook is still preserved as `.pre-commit.local` as before.

### Added

- **`bootstrap.sh` Phase 2.6 — gitignore reconciliation + public-repo advisory.** Reconciles `.gitignore` against the committable-vs-machine-local manifest:
  - machine-local telemetry (`.control/audit/*.jsonl`) → auto-added to `.gitignore` if missing.
  - committable substrate (`.control/arcs.yaml`, `rcs-parameters.toml`, `policy.yaml`) → **warns** if gitignored (a coverage gap — the loop won't survive a fresh clone), never auto-un-ignores (publishing a maybe-private file is the human's call).
  - **public-repo advisory** (via `gh repo view --json visibility`, graceful if absent): on a PUBLIC remote, surfaces that scaffolded governance is committable-and-public so the operator reviews for secrets before pushing.
- `tests/gitignore-aware-bootstrap.test.sh` — 5 assertions covering both fixes (tracked-hook preserved, untracked-hook sidecar still works, audit-glob added, committable-ignored warning). 5/5.

### Notes

- Additive + non-blocking: Phase 2.6 skips gracefully on a non-git workspace; canary 14/14 unaffected.
- The manual handling on stimulus (#1811) + broomva.tech (broomva.tech#211) is the spec this automates.
- `VERSION` 0.22.0 → 0.23.0.

## 0.22.0 — 2026-05-28

### feat: wire the RCS control loop on `bstack bootstrap` (close the split-brain)

`onboard.sh` (the wizard) wired the control loop via `install-rcs-stability.sh`; `bootstrap.sh` (the `/bstack bootstrap` command — what the SKILL.md tells agents to run) **never did**. Freshly-bootstrapped workspaces had governance files but an **open loop** — no L0/L1 audit hooks, no `.control/audit/`, no closure-arc definitions. And `doctor` reported all of that as soft `[info]`, so the loop being open was invisible.

This release makes `/bstack bootstrap` wire the loop, scaffold the loop definitions, and report loop liveness as a first-class verdict.

### Added

- **`bootstrap.sh` Phase 3.5 — RCS loop wiring.** Bootstrap now calls `install-rcs-stability.sh` (L0 PostToolUse + L1 Stop audit hooks + `.control/audit/` + L3 gates), reusing the same idempotent installer the wizard uses. Guarded by `BSTACK_SKIP_RCS=1` (mirrors `BSTACK_SKIP_SKILLS=1`) for governance-only bootstrap. The `|| true` guard preserves bootstrap's non-blocking contract under `set -e`.
- **`bootstrap.sh` Phase 2 — `.control/arcs.yaml` scaffold.** The closure-contract arcs (the workspace's own editable loop definitions) are now scaffolded from `arcs.yaml.template` alongside CLAUDE.md / AGENTS.md / policy.yaml. (`compute-arc-status.sh` already fell back to the bundled template; scaffolding gives the workspace an editable copy.)
- **`doctor.sh` §23 — Control-loop closure verdict.** The single verdict answering "is the loop wired + connected + running?" — three states: (a) substrate absent, (b) wired-but-idle, (c) wired + running + closing. Composes W (audit hooks + dir) / R (audit logs fresh < 7d) / C (arcs + composite-ω resolvable). The W check reads **both** `settings.json` and `settings.local.json` — Claude Code merges them at runtime, and shared repos legitimately keep machine-local hook paths in the gitignored `settings.local.json` (surfaced by dogfooding on the stimulus repo, whose tracked `settings.json` uses repo-relative vendored hooks). **Soft by default** (audit logs are empty until the first hook fires; a hard default would redden every fresh bootstrap for purely temporal reasons); `BSTACK_LOOP_STRICT=1` promotes "wired-but-idle" to a hard `--strict` gap for CI lanes. §19 already hard-gates the case that matters (a wired loop genuinely diverging), so §23 doesn't double-count it.

### Changed

- `tests/canary/01-fresh-bootstrap.test.sh` — now asserts `PostToolUse` hook, `.control/audit/`, `.control/arcs.yaml`, and the L0/L1 audit markers (covers Phase 3.5 + arcs scaffold). 14/14.
- `references/new-workspace-flow.md` — documents that `bootstrap` (not only `onboard`) wires the loop; adds arcs.yaml + §20–§23 to the doctor-report summary.
- `VERSION` 0.21.10 → 0.22.0.

### Notes

- Idempotent + additive: re-running bootstrap (or bootstrap after onboard, in either order) is a no-op — hook markers (P1/P6/P2/P7/P17 vs L3-G0 vs L0-audit/L1-audit) are all distinct; `scaffold_governance_file` early-returns `[keep]` on existing files.
- `doctor.sh` resolves `WORKSPACE="${BROOMVA_WORKSPACE:-$HOME/broomva}"` — running it from a sub-repo without `BROOMVA_WORKSPACE` set measures the parent workspace, not `$PWD`. (Surfacing this as a possible future UX fix; out of scope here.)

## 0.21.10 — 2026-05-27

### revert: --full-depth no longer needed after broomva/skills restructure

Companion to [broomva/skills PR #11](https://github.com/broomva/skills/pull/11) (merge `ab2d05b`), which removed the root `SKILL.md` from `broomva/skills` to align with the [`anthropics/skills`](https://github.com/anthropics/skills) ecosystem-canonical monorepo shape (best practice per [`agentskills.io`](https://agentskills.io/specification)).

With the root `SKILL.md` gone, the `npx skills` CLI's default search descends into `skills/<name>/` automatically — `--full-depth` is no longer required.

### Reverted

- `references/skills-roster.md` — dropped `--full-depth` from 26 `--skill` install commands
- `scripts/skill-graduate.sh` — dropped `--full-depth` from the 4 generated install commands (commit msg, monorepo PR body, redirect-stub README, stub PR body). Future graduations now emit the canonical command form.
- `VERSION` 0.21.9 → 0.21.10

### Net effect across the 0.21.x line

| Release | What it did |
|---|---|
| 0.21.9 | Added `--full-depth` as a workaround for the root-SKILL.md shadowing (post-dogfood) |
| 0.21.10 | Removed `--full-depth` after the broomva/skills restructure eliminated the root cause |

The roster's `npx skills add broomva/skills --skill <name>` commands are now both **correct AND minimal** — matching the form used by all other monorepos in the ecosystem.

20/20 tests (graduate + audit) still pass.

---


## 0.21.9 — 2026-05-26

### fix(install): `--skill` commands require `--full-depth` (dogfood finding)

`/dogfood` validation of the skills-monorepo install path found that `npx skills add broomva/skills --skill <name>` returns **"Found 1 skill"** (only the root catalog) — the `npx skills` CLI short-circuits subdirectory search once it finds a root `SKILL.md`. The monorepo's `skills/<name>/` subdirs are invisible without `--full-depth`.

Empirically verified 2026-05-26: without flag → "Found 1 skill"; with `--full-depth` → "Found 53 skills" + handoff/pre-mortem install correctly (files on disk, content confirmed).

**Fix:**
- `references/skills-roster.md` — all 26 `--skill` install commands now include `--full-depth`
- `scripts/skill-graduate.sh` — the 4 generated install commands (commit msg, monorepo PR body, redirect-stub README, stub PR body) now include `--full-depth`, so future graduations produce correct stubs
- `VERSION` 0.21.8 → 0.21.9

The command was asserted ~58× across the migration arc but never run until the dogfood. Classic P11: compile-time/spec-level correctness ≠ runtime correctness.

---


## 0.21.8 — 2026-05-26

### `bstack skills audit` — skill registry audit (Phase 6c)

New subcommand `bstack skills audit` — crystallizes the "Skill Registry Audit" pattern (bstack-engine candidate ledger, 3/3 instances: Steipete's skill-cleaner + the 2026-05-25 manual inventory + P7 Freshness as a degenerate single-dimension case). Adapts Steipete's skill-cleaner (steipete/agent-scripts) algorithm for Claude Code + bstack.

Five reports:
1. **Budget** — total description token cost (`ceil(utf8_bytes / chars_per_token)`, identical to Steipete) vs a ceiling (default 20,000 = 2% of 1M). Flags over-budget.
2. **Duplicates** — same skill name across >1 distinct realpath (symlink-deduped, so the `.agents` ↔ workspace symlink case doesn't false-positive).
3. **Registry coherence** — `companion-skills.yaml` vs installed roots: registered-but-missing + installed-but-unregistered.
4. **Unused** — no invocation trace in recent Claude Code session logs (`~/.claude/projects/**/*.jsonl`, replacing Codex's `~/.codex/history.jsonl`); `--months` window; `--no-logs` to skip.
5. **Roots** — skill count per root.

`--json` for machine output. Env-overridable (`BSTACK_AUDIT_ROOTS`, `BSTACK_DIR`, `BSTACK_AUDIT_LOG_GLOB`) for hermetic testing.

### First real-run findings (2026-05-26, against the 3 broomva skill roots)

- 362 skills, 331 unique names across `~/.claude/skills` + `~/.agents/skills` + `~/broomva/skills`
- Description budget at **223% of the 2% ceiling** (44,681 tokens / 20,000) — corroborates the 2026-05-25 skill-cleaner reading (269% over the broader Codex+plugin set)
- 31 cross-root duplicates (mostly snapshot-in-.claude + source-in-workspace — expected, but worth surfacing)

### Files

- **NEW** `scripts/skill-audit.py` (~250 lines) — the auditor; pyyaml-based; env-overridable for hermetic tests
- **NEW** `tests/skill-audit.test.sh` — 9 hermetic tests (fake roots + registry + logs; realpath-dedupe, duplicate detection, registry coherence, unused detection, budget flag). All pass.
- **CHANGED** `bin/bstack-skills` — `audit)` dispatch + usage
- **VERSION** `0.21.7` → `0.21.8`

This completes the skills-monorepo meta-tooling: `graduate` (migrate skills in) + `audit` (keep the registry healthy).

---


## 0.21.7 — 2026-05-26

### `bstack skills graduate` — crystallized Tier-2 migration (Phase 6b)

New subcommand `bstack skills graduate <name>` that automates the Tier-2 skill-graduation pattern which ran **8 times manually** on 2026-05-25/26 (Phases 2–4f: strategy bundle, content ×2, research+finance, specialty, neuroscience, orcahand dedup). This is the P16 crystallization of that pattern — rule-of-three exceeded ~8× over.

What it automates:
1. Clone source repo + monorepo into temp worktrees
2. Copy canonical content into `skills/<target>/` — excluding `.git`, dot-prefixed IDE-mirror dirs, `LICENSE`, `skills-lock.json` (the exact exclusion set learned across the 8 manual runs)
3. Append a row to the monorepo README Tier-2 table
4. Commit + push + open PR on the monorepo
5. `--stub` (default ON): add a redirect-stub README to the source repo + open PR
6. `--merge` (default OFF): merge the opened PRs
7. Cleanup temp clones (trap on EXIT)

Supports `--target` (rename, e.g. drop `-skill` suffix), `--source-repo`, `--monorepo`, `--category`, `--description`, `--exclude` (repeatable), `--dry-run`.

The registry update (companion-skills.yaml + skills-roster.md + VERSION + CHANGELOG) is intentionally NOT automated — it needs a coordinated bstack version bump a human/agent reviews. The script PRINTS the exact registry entry to add (copy-paste ready).

### Files

- **NEW** `scripts/skill-graduate.sh` — the graduation engine (~230 lines). Env-overridable `BSTACK_GRADUATE_GH` / `BSTACK_GRADUATE_GIT` / `BSTACK_GRADUATE_TMPDIR` / `BSTACK_GRADUATE_DRY_RUN` for hermetic testing.
- **NEW** `tests/skill-graduate.test.sh` — 9-test offline smoke (arg-parse, dry-run, rename detection, stub-gh/git execution path with copy + exclude verification, no-SKILL.md error). All 9 pass.
- **CHANGED** `bin/bstack-skills` — adds `graduate)` dispatch + usage entry.
- **CHANGED** `SKILL.md` + `bin/bstack` — advertise the subcommand.
- **VERSION** `0.21.6` → `0.21.7`.

### Provenance

The pattern's 8 manual instances are the audit trail (broomva/skills PRs #2–#9 + broomva/bstack PRs #53–#59). Crystallization closes the loop: the next graduation is `bstack skills graduate <name>` instead of a full manual clone→copy→README→PR→stub→registry cycle.

---


## 0.21.6 — 2026-05-26

### Phase 4f orcahand migration + dedup (final Tier-2 migration)

The last Tier-2 migration. `orcahand` consolidated to broomva/skills Tier-2 monorepo (broomva/skills PR #9 merge `df4fb13`), resolving the Phase-0 contested dedup case.

`broomva/orcahand` and `broomva/orcahand-skill` were true duplicates (both 180K, identical SKILL.md + assets/ + references/ + schemas/ + scripts/, both `name: orcahand`). Per user decision: migrate the canonical (`.agents`-symlinked) `broomva/orcahand` → `skills/orcahand/`; archive BOTH source repos with redirect-stubs.

### Source repos deprecated (both, 6mo window until 2026-11-26)

- broomva/orcahand PR #1 (merge `4e637ed`)
- broomva/orcahand-skill PR #1 (merge `f753966`)

### Files changed

- `references/companion-skills.yaml` — 1 NEW entry (orcahand); 63 → 64 total
- `VERSION` — `0.21.5` → `0.21.6`
- `CHANGELOG.md` — this entry

### Migration complete

**47 skills now in broomva/skills monorepo.** All Tier-2 candidates from the inventory doc are migrated, except `broomva/mirage` (no remote on GitHub — flagged as an anomaly, not actionable). The skills-monorepo migration arc (Phases 2–4f) is complete.

---


## 0.21.5 — 2026-05-26

### Phase 4e neuroscience migration (TRIBE v2 family)

3 standalone-repo neuroscience skills migrated to broomva/skills Tier-2 monorepo (broomva/skills PR #8 merge `ca7de88`):

- `tribe-v2-agent-alignment` — cortical alignment benchmarking for AI encoders
- `tribe-v2-bci-applied` — applied BCI + neuro-informed content optimization
- `tribe-v2-neuroscience` — in-silico fMRI prediction

All NEW registry entries (none were previously registered).

### Finding: `_shared/` is demand-driven

The strategy doc anticipated a `_shared/tribe-v2/` directory. On inspection, the 3 skills have DISJOINT reference sets (arcan-integration / cortical-region-atlas / brain-regions — no file overlap). No `_shared/` extraction warranted. This validates the principle: `_shared/<category>/` is created only when concrete shared content exists, never speculatively.

### Source repos deprecated (6mo window until 2026-11-26)

- broomva/tribe-v2-agent-alignment PR #1 (merge `7bca620`)
- broomva/tribe-v2-bci-applied PR #1 (merge `1e3ea14`)
- broomva/tribe-v2-neuroscience PR #1 (merge `64165b7`)

### Files changed

- `references/companion-skills.yaml` — 3 NEW entries (60 → 63 total)
- `VERSION` — `0.21.4` → `0.21.5`
- `CHANGELOG.md` — this entry

### Cumulative monorepo state: 46 skills

---


## 0.21.4 — 2026-05-26

### Phase 4d specialty domain migration (largest batch)

17 standalone-repo skills migrated to broomva/skills Tier-2 monorepo (broomva/skills PR #7 merge `1be5fd9`). 2 renamed during migration to drop `-skill` suffix per ecosystem norm.

By sub-category:

**Hardware, robotics, physical (6):** bitnet, capx-agentic-robotics, openrocket-sim, sdr-satellite, ocean-genomics, microgrid-agent

**Compute & remote infrastructure (4):** colab-remote, remote-gpu, claude-code-channels, claude-remote-sessions

**Audio, voice, media (2, both renamed):** omnivoice (from omnivoice-skill), livecoding

**Advisory & consulting (3, one renamed):** phronesis, procurer, alkosto-wait-optimizer (from alkosto-wait-optimizer-skill)

**Health & body (2):** health, founder-mode-oncology (Phase-0 contested case lean accepted)

### Source repos deprecated (17 redirect-stubs merged 2026-05-26)

bitnet · livecoding · openrocket-sim · sdr-satellite · capx-agentic-robotics · ocean-genomics · phronesis · procurer · alkosto-wait-optimizer-skill · omnivoice-skill · microgrid-agent · claude-code-channels · claude-remote-sessions · colab-remote · remote-gpu · health · founder-mode-oncology — all archived after 6mo deprecation (2026-11-26).

### Excluded from this batch

- `broomva/mirage` — repository does not exist on GitHub (anomaly flagged in inventory § 8)
- `broomva/orcahand` — Phase-0 contested dedup decision between `broomva/orcahand` (runtime) and `broomva/orcahand-skill` (duplicate); deferred to Phase 4f

### Files changed

- `references/companion-skills.yaml` — 16 NEW entries + 1 entry rewritten (alkosto-wait-optimizer)
- `references/skills-roster.md` — install commands updated
- `VERSION` — `0.21.3` → `0.21.4`
- `CHANGELOG.md` — this entry

### Total registry size

44 → 60 entries (+16 net)

### Cumulative monorepo state

43 skills under `broomva/skills/skills/`:
- 2 workflow & lifecycle
- 9 strategy & decision intelligence
- 9 content & media
- 3 research & intelligence
- 3 finance
- 6 hardware/robotics/physical
- 4 compute & remote
- 2 audio/voice/media
- 3 advisory/consulting
- 2 health & body

---


## 0.21.3 — 2026-05-25

### Phase 4c research + finance migration

Six more skills migrated from standalone `broomva/<name>` repos into `broomva/skills` Tier-2 monorepo (broomva/skills PR #6 merge `e2bdf14`). Three renamed during migration to drop the `-skill` suffix per ecosystem norm:

| Old repo | New monorepo path | Notes |
|---|---|---|
| `broomva/deep-dive-research-skill` | `skills/deep-dive-research-orchestrator/` | Renamed to match canonical skill name in registry |
| `broomva/social-intelligence` | `skills/social-intelligence/` | NEW registry entry |
| `broomva/harness-engineering-skill` | `skills/harness-engineering-playbook/` | Renamed; preserves `required: true` |
| `broomva/investment-management` | `skills/investment-management/` | NEW registry entry |
| `broomva/wealth-management` | `skills/wealth-management/` | NEW registry entry |
| `broomva/haima-skill` | `skills/haima/` | Renamed; runtime crate stays at `broomva/haima` (separate concern) |

### Source repos deprecated (6mo window until 2026-11-25)

- broomva/deep-dive-research-skill PR #1 (merge `c39a293`)
- broomva/social-intelligence PR #3 (merge `b0d2bc5`)
- broomva/harness-engineering (renamed from -skill at GitHub level) PR #1 (merge `55edcde`)
- broomva/investment-management PR #7 (merge `557a950`)
- broomva/wealth-management PR #1 (merge `5022c12`)
- broomva/haima-skill PR #2 (merge `c8f5014`)

### Files changed

- `references/companion-skills.yaml` — 4 NEW entries (social-intelligence, investment-management, wealth-management, haima) + 2 entries rewritten (deep-dive-research-orchestrator, harness-engineering-playbook) — total 40 → 44
- `references/skills-roster.md` — install commands updated
- `VERSION` — `0.21.2` → `0.21.3`
- `CHANGELOG.md` — this entry

### Cumulative monorepo state

26 skills under `broomva/skills/skills/`:
- 2 workflow & lifecycle (handoff, make-spec)
- 9 strategy & decision intelligence
- 9 content & media
- 3 research (deep-dive-research-orchestrator, social-intelligence, harness-engineering-playbook)
- 3 finance (investment-management, wealth-management, haima)

---


## 0.21.2 — 2026-05-25

### Phase 4b content & media migration (final content batch)

Six more standalone-repo skills migrated into the broomva/skills Tier-2 monorepo (broomva/skills PR #5 merge `e932c7b`). After this release, all 9 content/media skills identified in the strategy doc inventory are in the monorepo:

- `content-creation` — full-stack content pipeline; ships 45MB of demonstration campaigns (bstack-launch, open-source-stack). Registry entry updated to monorepo path.
- `content-engine` — **NEW** registry entry. AI content studio bundling 4 sub-skills (autopilot, cinema, dna, loop) under `skills/content-engine/skills/`.
- `launch-video` — **NEW** entry. Remotion-based Liquid Glass launch video.
- `ltx-video` — **NEW** entry. LTX-2.3 video generation (Lightricks 22B DiT model).
- `creative-review` — **NEW** entry. Meta creative review with style scoring.
- `brainrot-for-good` — **NEW** entry. Dopamine-aware video editing for valuable content.

### Source repos deprecated (6mo window until 2026-11-25)

- broomva/content-creation PR #5 (merge `88c7ff7`)
- broomva/content-engine PR #2 (merge `9761756`)
- broomva/launch-video PR #1 (merge `dfe48e6`)
- broomva/ltx-video PR #1 (merge `f8962ab`)
- broomva/creative-review PR #1 (merge `bcaea5a`)
- broomva/brainrot-for-good PR #1 (merge `7f22dd6`)

### Files changed

- `references/companion-skills.yaml` — 5 new entries + 1 entry rewritten; total 35 → 40 entries
- `references/skills-roster.md` — install commands updated for 6 entries
- `VERSION` — `0.21.1` → `0.21.2` (additive patch)
- `CHANGELOG.md` — this entry

### Monorepo state after this release

20 skills installed in `broomva/skills/skills/`:
- 2 workflow & lifecycle (handoff, make-spec)
- 9 strategy & decision intelligence
- 9 content & media (this release completes the content batch)

---


## 0.21.1 — 2026-05-25

### Phase 4a content & media migration

Three more skills migrated from standalone `broomva/<name>` repos into the `broomva/skills` Tier-2 monorepo (broomva/skills PR #4 merge `2f5aec4`):

- `blog-post` — full-stack blog post production (substantial: 28KB SKILL.md + examples/ + references/ + scripts/publish.sh + templates/). **NEW** registry entry (was previously bundled / not registered separately).
- `brand-icons` — brand icon and visual identity asset generation. Registry entry updated from `repo: broomva/brand-icons` → `repo: broomva/skills, skillPath: skills/brand-icons/SKILL.md`.
- `seo-llmeo` — SEO and LLM Engine Optimization (audits, meta tags, structured data, llms.txt). Registry entry updated to monorepo path.

Each source repo carries a redirect-stub README during a 6-month deprecation window (until 2026-11-25):
- broomva/blog-post PR #1 (merge `a7d90b6`)
- broomva/brand-icons PR #1 (merge `2e20534`)
- broomva/seo-llmeo PR #1 (merge `2b635d6`)

### Files changed

- `references/companion-skills.yaml` — 2 entries rewritten (`brand-icons`, `seo-llmeo`), 1 new entry added (`blog-post`)
- `references/skills-roster.md` — install commands updated to monorepo paths
- `VERSION` — `0.21.0` → `0.21.1` (additive patch — new entries + corrected install paths)
- `CHANGELOG.md` — this entry

### Pattern note: multi-source-repo migration

Phase 3 migrated 9 sub-skills from ONE bundled source (`broomva/strategy-skills/.skills/`). Phase 4a tests the multi-source pattern — 3 separate standalone source repos, each with full skill layout (`SKILL.md` + scripts/ + references/ + assets/). The migration command is now well-rehearsed and ready to crystallize into the `bstack skill graduate` CLI (Phase 6b).

`broomva/blog-post`'s root canonical content was preserved; 24 IDE-specific dotfile-mirror dirs (`.agent/`, `.claude/`, `.continue/`, etc.) were excluded as deployment artifacts — downstream agents resolve to `skills/blog-post/SKILL.md` per the agentskills.io spec.

---

## 0.21.0 — 2026-05-25

### Schema additive bump — `skillPath` field for monorepo support

`schemas/companion-skills.v1.json` gains an optional `skillPath` field on each skill entry. When a skill lives inside a monorepo (e.g. `broomva/skills`), `skillPath` is the relative path to its `SKILL.md` within that repo, e.g. `skills/<name>/SKILL.md`. Combined with `repo`, install becomes `npx skills add <repo> --skill <name>`. The field is **purely additive** — existing entries that omit it continue to work as standalone per-skill installs. Schema version remains 1 (no breaking change).

### Strategy & decision-intelligence migration (Phase 3 of skills-packaging strategy)

Per [`broomva/workspace docs/specs/2026-05-25-skills-packaging-strategy.html`](https://github.com/broomva/workspace/blob/main/docs/specs/2026-05-25-skills-packaging-strategy.html) §8 Phase 3, the 9 strategy sub-skills migrated from the bundled `broomva/strategy-skills` repo (where they lived under `.skills/<name>/SKILL.md`) into the `broomva/skills` Tier-2 monorepo at `skills/<name>/SKILL.md`. The registry entries previously pointed at per-skill `broomva/<name>` repos that never existed as standalones (stale-registry condition). This release fixes the install paths.

Migrated entries (now using monorepo `skillPath`):

- `pre-mortem` — 4-category failure-mode analysis + mitigation plan
- `premortem` — Klein/Kahneman premortem with parallel sub-agent deep-dives (NEW — was missing from registry)
- `braindump` — raw thoughts → Obsidian with auto-categorization
- `morning-briefing` — daily brief from vault priorities + action items
- `drift-check` — priority drift report (stated vs actual effort)
- `strategy-critique` — red-team critique of strategy documents
- `stakeholder-update` — multi-audience generator (technical / business / customer)
- `decision-log` — structured decision capture → vault
- `weekly-review` — weekly vault change scan + attention flags

Install paths:
```bash
# New (per-skill from monorepo)
npx skills add broomva/skills --skill pre-mortem
npx skills add broomva/skills --skill braindump
# ... etc

# Backward-compat (bundled install via deprecated repo, 6mo window until 2026-11-25)
npx skills add broomva/strategy-skills
```

Related: `broomva/skills` PR #3 (merge `af42d83`); `broomva/strategy-skills` redirect-stub PR #1.

### Workflow & lifecycle skills registered

The two Tier-2 prototypes that graduated in `broomva/skills` PR #2 (merge `f21515e`) — `handoff` and `make-spec` — are now in the registry. Both `min_bstack_version: 0.21.0` (need `skillPath` field added by this release).

- `handoff` — fresh-session handoff doc drafting (workflow & lifecycle category)
- `make-spec` — native-HTML design-doc scaffold per P18 Category-C (design category)

### Files changed

- `schemas/companion-skills.v1.json` — adds optional `skillPath` field with pattern validation
- `references/companion-skills.yaml` — replaces 8 strategy entries with 9 monorepo entries; adds `handoff` + `make-spec`; total entries +3 net (was 32, now 35)
- `VERSION` — `0.20.0` → `0.21.0`
- `CHANGELOG.md` — this entry

### Migration notes for downstream consumers

- Workspaces with `companion-skills.yaml` `schema_version: 1` continue to validate — `skillPath` is additive.
- The 8 old strategy entries pointed at `repo: broomva/<name>` repos that never existed; downstream `bstack doctor` runs that tried to resolve those installs would have errored. After this release, those installs resolve via the monorepo path.
- `min_bstack_version: 0.21.0` is set on every new/changed entry to signal that the consumer's `bstack` must be `≥ 0.21.0` to recognize `skillPath`.

---

## 0.20.0 — 2026-05-22

### Cross-review CLI — restore the P20 mechanism (BRO-1227 Fix B)

The P20 (`broomva/cross-review`) primitive — cross-model adversarial review on substantive PRs before merge — failed reliably during the 2026-05-21 Wave 3 dispatch session: both Cato sub-agent dispatches stalled within 6-7 tool uses with path-resolution errors. The Cato agent was invoked from the miami workspace but asked to read files at `~/broomva/broomva.tech/...` — the working tree was on a different branch than the PR's head SHA, so `Read`-tool calls drifted into "let me locate the actual repo" loops and never produced output.

This release ships **Fix B**: a `bstack cross-review` CLI that reads PR contents via `gh pr diff` + `gh api repos/.../contents/<path>?ref=<sha>`. Working-tree state is eliminated as a variable — the CLI can be invoked from any cwd; only `--repo <owner/name>` + PR number matter.

### New files (3)

- **NEW** `scripts/cross-review.py` — argparse CLI. Fetches PR metadata (`gh pr view`), the diff (`gh pr diff`), and post-change file contents (`gh api …/contents/<path>?ref=<head_sha>`); skips lock files and >2000-line adds; bundles into a structured codex prompt; invokes `codex exec --sandbox read-only --model gpt-5.4 --skip-git-repo-check` with a 240s default timeout; parses JSON verdict (with fallback `try_parse_json` extractor that recovers a balanced `{…}` object from prose-wrapped output); writes structured JSON to `.bstack-cross-review/<pr>.json` + markdown to `<pr>.md`. Verdict schema: `verdict` (pass/concerns/fail/skipped) × `anti_slop_score` (0-10) × `criticality` (high/medium/low) × `findings[]` × `blind_spots_surfaced[]` × `summary`. Optional `--post-comment` posts the markdown verdict back to the PR. Exit codes: 0 pass · 10 concerns · 20 fail · 30 skipped · 2 invocation/gh failure.
- **NEW** `bin/bstack-cross-review` — thin shim mirroring `bin/bstack-wave`: dispatches to `scripts/cross-review.py`, forwards argv unchanged.
- **NEW** `tests/cross-review.test.sh` — 8-test hermetic offline smoke (dispatcher routing, argparse rejection cases, module import, `try_parse_json` recovery from prose-wrapped JSON, `exit_code_for_verdict` mapping). No network calls — end-to-end validation is the per-PR `--dry-run` against real PRs documented in the PR body.

### Changed files (3)

- **CHANGED** `bin/bstack` — adds `cross-review)` dispatch entry routing to `bin/bstack-cross-review`. Adds usage section "Review" with a one-liner pointing at `cross-review <pr-num> --repo <owner/name>` (`≥ 0.20.0`). Adds the canonical invocation to the Examples block.
- **CHANGED** `SKILL.md` — Quick start block lists `bstack cross-review` with the BRO-1227 Fix B annotation and 0.20.0 introduction marker.
- **CHANGED** `VERSION` — `0.19.0 → 0.20.0`.

### Why Fix B over Fix A or Fix C

- **Fix A** (add `--cwd` parameter to Cato dispatch) leaves the working-tree-state coupling intact — every future P20 invocation has to remember to set it, and a stale checkout silently degrades review quality. The failure mode comes back the next time someone uses Cato across repos.
- **Fix B** (always read from git, never the working tree) eliminates the failure mode by construction. The bug surface goes away.
- **Fix C** (full skill repo + agent definition + tmp-checkout pipeline) is the *complete* answer but requires writing the `~/.claude/skills/cross-review/` skill, deciding whether the Cato agent stays as the codex-exec frontend or gets re-architected, and managing the tmp-checkout cleanup contract. Larger blast radius — deferred to a follow-up once Fix B has soaked.

### Test plan executed

```
bash -n bin/bstack-cross-review                                      # syntax OK
bash -n bin/bstack                                                   # syntax OK
python3 -m py_compile scripts/cross-review.py                        # OK
bin/bstack --help | grep cross-review                                # 2 lines
bin/bstack cross-review --help | head -20                            # argparse usage
bin/bstack cross-review 195 --repo broomva/broomva.tech --dry-run    # 9 files fetched, 1 lock skipped
bash tests/cross-review.test.sh                                      # 8/8 pass
```

### What's next (not in this release)

- Apply `bstack cross-review` to the 3 PRs that merged WITHOUT P20 cross-review last session — broomva.tech#195, #196, life#1427 — and post retro-verdicts as PR comments. Out of scope for this PR (no code change needed; this PR ships the tool).
- File a follow-up for the full `~/.claude/skills/cross-review/` skill (Fix C scope) once Fix B has soaked through ≥3 P20 invocations.

### Backreferences

- BRO-1227 — P20 cross-review mechanism gap (closes via Fix B)
- 2026-05-22 session handoff — `/Users/broomva/conductor/archived-contexts/broomva/wave-3-dispatch-and-linear-updates/handoffs/2026-05-22-SESSION-HANDOFF.md` §"Queued + ready to dispatch"
- CLAUDE.md §"Cross-Review (P20)" — the discipline rule this mechanism enforces

## 0.19.0 — 2026-05-22

### Closure Contract — generalize 5-tuple from 4 RCS layers to N declared arcs

Builds on **v0.18.0** (Phase 8 federation, BRO-47) — the federation registry is the substrate that lets per-workspace arc declarations roll up via `bstack status --aggregate`. Together, v0.18.0 + v0.19.0 close the substrate-completion arc through the user-defined-arcs layer.

v0.14.0 + v0.16.0 already shipped a 5-tuple `(plant, sensor, controller, actuator, termination)` for **4 hard-coded RCS layers** via `assets/templates/rcs-parameters.toml.template` + `scripts/compute-budget-status.sh`. This release lifts the same pattern from those 4 layers to **N user-declared domain arcs** the workspace actually runs every day (PR greenflow, bookkeeping promotion quality, deploy reliability, etc.).

The closure contract: every arc declares `(id, plant_surfaces, sensor, actuator, termination, tau_a, shield_ref)`. The agent's reasoning is the universal Π (controller) — that's not declared, it's the default binding when `actuator.kind == "agent_reasoning"`. Script / mcp_tool / http actuators bind specific mechanisms while keeping the agent in the supervisory role.

Companion: point-in-time → trend monitoring for composite-ω. `compute-budget-status.sh --trend` appends one snapshot per call to `.control/audit/composite-omega-history.jsonl`, then reads the last 7 days and reports slope + verdict (`stable_flat | drift_up | drift_down | volatile`). Doctor §21 surfaces a hard gap only on `drift_down` — composite stability shrinking is the signal worth interrupting on.

### New files (4)

- **NEW** `schemas/arcs.v1.json` — JSON-schema draft-07 for `.control/arcs.yaml`. Mirrors the style of `schemas/policy.v1.json` and `schemas/workspaces.v1.json`. Required arc fields: `id` (same character class as workspace registry name), `plant_surfaces` (free-form URIs), `sensor` (enum: `exit_code | json_path | log_match | metric_threshold`), `actuator` (enum: `agent_reasoning | script | mcp_tool | http`), `termination` (enum: `predicate | wallclock | score_threshold | exit_zero`), `tau_a` (number, seconds). Optional: `shield_ref` pointing at a `policy.yaml` gate.
- **NEW** `assets/templates/arcs.yaml.template` — declarative arcs template with 2 worked examples and heavy commentary mirroring the rcs-parameters.toml.template intro cadence. Example 1: `code-pr-greenflow` (json_path sensor, agent_reasoning actuator, predicate termination, tau_a=1800s). Example 2: `bookkeeping-promotion-quality` (exit_code sensor, agent_reasoning actuator, score_threshold termination, tau_a=86400s).
- **NEW** `scripts/compute-arc-status.sh` — per-arc verdict reader; mirrors the shape of `scripts/compute-budget-status.sh` exactly. Looks at `.control/arcs.yaml` → falls back to bundled template. For each arc: runs the sensor (`bash -c` for exit_code/json_path/metric_threshold; regex against log file for log_match), reads most recent termination event from `.control/audit/arc-<id>.jsonl`, evaluates termination predicate, emits verdict `green | yellow | red | unknown`. Outputs JSON (default) or `--human` table. Exit codes: 0 all green, 1 ≥1 red, 2 config missing, 3 python3 unavailable. Ships its own inline minimal YAML parser (modeled on `scripts/workspace.py` `_yaml_minimal_parse`) — PyYAML preferred, falls back when absent, both code paths exercised by the test suite.
- **NEW** `tests/arcs-validation.test.sh` + **NEW** `tests/omega-drift-trend.test.sh` — hermetic bash test suites in the `tests/metrics-pipeline.test.sh` style. 6 + 6 tests; both GREEN under system Python (PyYAML, no tomllib path) AND homebrew Python (tomllib, no PyYAML path). Tests exercise schema rejection (`schema_version: 99`), template loading, override precedence, drift_down / drift_up / stable_flat verdicts on synthetic data, and idempotent history-line writes per `--trend` call.

### Changed files (2)

- **CHANGED** `scripts/compute-budget-status.sh` — adds `--trend` flag. Without `--trend`: existing point-in-time behavior preserved. With `--trend`: appends `{ts, omega, per_layer}` snapshot to `.control/audit/composite-omega-history.jsonl`, then reads last 7 days, computes least-squares slope, baseline (median of first day in window), deviation, volatility (CV), and verdict. Verdict heuristic prefers drift detection over volatility when there's a clear directional signal (slope sign matches relative-deviation sign with magnitude > 1%); volatility is the residual category. Trend block surfaces in `--human` as one extra line and as a top-level `trend` object in `--json`.
- **CHANGED** `scripts/doctor.sh` — adds §20 and §21. §20 reads `.control/arcs.yaml` (informational when absent), reports arc count + completeness count, surfaces last-termination-event timestamp per arc; hard gap only if `schema_version != 1`. §21 reads `composite-omega-history.jsonl`, calls `compute-budget-status.sh --trend --json`, reports last/baseline/slope/verdict; hard gap only if `verdict == drift_down`.

### Test plan executed

```
bash -n scripts/compute-arc-status.sh                            # syntax OK
bash -n scripts/compute-budget-status.sh                         # syntax OK
bash -n scripts/doctor.sh                                        # syntax OK
python3 -c "import json; json.loads(open('schemas/arcs.v1.json').read())"  # schema parses
bash scripts/compute-arc-status.sh --human                       # reads template, prints table for both arcs
bash scripts/compute-budget-status.sh --trend --human            # writes 1 history line, prints trend line
bash tests/arcs-validation.test.sh                               # 6/6 GREEN under both python envs
bash tests/omega-drift-trend.test.sh                             # 6/6 GREEN
bash scripts/doctor.sh against ~/broomva                         # §20 + §21 visible; 87/89 (2 pre-existing gaps unrelated)
```

### Honest scope caveats

- The minimal inline YAML parser inside `compute-arc-status.sh` covers exactly the shape `schemas/arcs.v1.json` declares. Workspaces that hand-write `.control/arcs.yaml` with PyYAML-only features (anchors, multi-doc, flow-style) will need PyYAML installed; otherwise stick to the block-scalar shape shown in the template.
- `arc-<id>.jsonl` audit-event writers are not shipped in this PR. Termination events are *read* by `compute-arc-status.sh` when present; for now, only `wallclock` and `exit_zero` terminations evaluate without a prior recorded event. `predicate` and `score_threshold` arcs surface `yellow` (running) until an event lands. Follow-up: add `scripts/arc-event-hook.sh` so actuators can record verdicts as they close.
- Verdict thresholds in `--trend` (1% relative deviation, 10% coefficient of variation) are heuristic and calibrated for the broomva workspace's λ range. Tighten / loosen via follow-up policy.yaml block after rule-of-three failure modes accumulate.
- The "ω is shrinking" signal in §21 fires only after ≥ 2 history points span the 7-day window. Workspaces that don't periodically invoke `--trend` (no scheduled call from `/loop` or a cron) will see only `stable_flat` regardless of underlying drift.

### Spec doc + cross-references

- Anchored arcs: prior PR (v0.16.0) shipped the 4-layer hard-coded analogue (`assets/templates/rcs-parameters.toml.template`); v0.14.0 shipped the L3 enforcement; this PR generalizes both surfaces to N user-declared arcs.
- Why not a new primitive: the Closure Contract is the *generalization* of the existing (X, U, h, Π, T) substrate that L0–L3 already use. It's a declarative surface lift, not a new reflex. **P21 "Closure Contract" promotion candidate logged** — promotion to a numbered primitive deferred until rule-of-three concrete failures are recorded (per the L3 stability budget's stability budget for governance churn). The candidate ledger lives in `research/entities/pattern/bstack-engine.md` per CLAUDE.md §Bstack Engine.

---
## 0.18.0 — 2026-05-22

### Phase 8 — Multi-workspace federation registry

Closes the substrate-completion-arc Phase 8 backlog item: introduces an opt-in
host-level registry (`~/.broomva/global/registry.yaml`) that catalogues every
bstack-governed workspace on this machine, plus a `bstack status --aggregate`
rollup that walks the registry and surfaces cross-workspace composite-ω.
Federation is **read-only aggregation** — each workspace remains the source
of truth for its own state; the registry is the index, not the database.

This release lands on top of v0.16.0 (multi-layer RCS closure) and v0.17.0
(BROOMVA_ROOT convention). Together they form the substrate that PR2
(v0.19.0, BRO-48 — Closure Contract) generalizes from 4 hard-coded RCS layers
to N user-declared domain arcs.

### New scripts + bin (3)

- **NEW** `bin/bstack-workspace` — 90-line bash dispatcher delegating to
  `scripts/workspace.py`. Subcommands `register | list | info | deregister`
  with `--json` + `--tag` + per-subcommand `--path`/`--name` filters. Exit
  codes documented in the help block: 0 ok, 2 invalid args, 3 schema/parse
  error, 4 target not found, 5 name conflict at different path.
- **NEW** `scripts/workspace.py` — 523-line Python registry manager with an
  inline minimal-YAML parser fallback (when PyYAML is absent), atomic
  writes (`.tmp` + `replace`), and schema-version checking. Honors
  `BSTACK_REGISTRY` env (default `~/.broomva/global/registry.yaml`) and
  `BSTACK_DIR` for VERSION detection. SLO: register/list p50 < 100ms.
- **NEW** `schemas/workspaces.v1.json` — JSON-schema draft-07 contract for
  the registry. `schema_version: 1` is the only valid value at v0.18.0;
  field `bstack_version` matches `^[0-9]+\.[0-9]+\.[0-9]+(-[A-Za-z0-9.-]+)?$`;
  `name` matches `^[A-Za-z0-9][A-Za-z0-9._-]*$` (1–64 chars).

### New tests (1)

- **NEW** `tests/workspace.test.sh` — 203-line hermetic bash suite, 10 cases:
  fresh register, refresh on same path, name conflict at different path (exit
  5), `info --path` reports `registered: true`, deregister by path, deregister
  miss (exit 4), schema_version=99 → exit 3, tag accumulation on refresh,
  invalid name (exit 2), `--help` block renders. Every test uses
  `BSTACK_REGISTRY=$(mktemp)` so the host registry is untouched. All 10 GREEN.

### Changed (4)

- **CHANGED** `bin/bstack` — dispatcher: new `workspace) exec
  "$BIN_DIR/bstack-workspace" "$@"` case + `Federation:` usage section
  + `status --aggregate` cross-reference under Observability.
- **CHANGED** `bin/bstack-status` — adds `--aggregate` (alias
  `--multi-workspace`) flag. Reads the registry via `bash bin/bstack-workspace
  list --json`, then for each entry attempts `bash <path>/bstack/scripts/compute-budget-status.sh --json`
  (falls back to reading `<path>/.control/audit/composite-omega.jsonl`),
  emits a table: name × bstack_version × composite_ω × last_seen × verdict.
  JSON form via `--json --aggregate`. Read-only, no writes anywhere.
- **CHANGED** `scripts/doctor.sh` — adds §20 (Workspace federation registry):
  informational when no registry present (federation opt-in); hard gap on
  schema mismatch (`schema_version != 1`); soft warning on entries with
  `last_seen_at > 30 days`. Reads `BSTACK_REGISTRY` env or default path.
- **CHANGED** `assets/templates/{SKILL,AGENTS,CLAUDE}.md.template` — light
  surface updates so freshly-bootstrapped workspaces document the
  `bstack workspace` commands. Federation is **not a new primitive** (no
  P21); it composes existing primitives (Snapshot P15 + multi-layer ω
  from v0.16.0 §19).

### Doctor section table after this release

| § | Title | Source | Hard gap when |
|---|---|---|---|
| §14 | RCS stability budget | compute-lambda.sh | any λᵢ ≤ 0 |
| §15 | L3 stability gate-flow wiring | install-l3-stability.sh | (informational only) |
| §16 | L0 plant audit | l0-tools.jsonl | >10000 events runaway |
| §17 | L1 autonomic reflex compliance | l1-reflexes.jsonl | compliance < 30% |
| §18 | L2 EGRI promotion throttle | l2-promotions.jsonl | over τ_a₂ budget |
| §19 | Multi-layer composite health | compute-budget-status.sh | any layer unstable |
| §20 | Workspace federation registry | bin/bstack-workspace list | schema_version != 1 |

### Test plan executed

- `bash -n` syntax check across all new + modified scripts → clean.
- `bash bin/bstack-workspace --help` → renders subcommand block.
- `BSTACK_REGISTRY=/tmp/test.yaml bash bin/bstack-workspace register --path /tmp --name test --json`
  → exit 0, action: registered.
- `BSTACK_REGISTRY=/tmp/test.yaml bash bin/bstack-workspace list --json` → count: 1.
- `BSTACK_REGISTRY=/tmp/test.yaml bash bin/bstack-workspace deregister --name test --json`
  → exit 0, count: 0.
- `bash tests/workspace.test.sh` → 10/10 GREEN.
- `bash scripts/doctor.sh` against worktree → 87/90 passed (baseline; §20
  fires as informational with no registry — same total).
- `bash scripts/compute-budget-status.sh --human` against ~/broomva
  → composite still stable.

### Honest scope caveats

- Federation is **local-filesystem only**. No network/IPC transport. A
  workspace on a remote host has to register itself locally; cross-host
  rollup is deferred.
- The registry is **read-only aggregation**. `bstack status --aggregate` does
  not mutate any registered workspace's state. CRDT-replicated cross-workspace
  promotion (the swarm-autoresearch-loop primitive) is a future Phase 8.5
  spec, not in this release.
- `last_seen_at` is updated on `register` (which is also "refresh"); it is
  not automatically updated by `--aggregate`. A future hook may bump
  `last_seen_at` on every successful `compute-budget-status.sh` invocation
  per workspace.

### Spec doc + cross-references

- Linear ticket: [BRO-47](https://linear.app/stimulus/issue/BRO-47)
- Prior release: v0.17.0 (BROOMVA_ROOT convention, #47, BRO-1223 follow-up)
- Next release: v0.19.0 (Closure Contract — arcs.yaml + composite-ω drift trend,
  BRO-48) builds on this substrate.
- Concept entity: `research/entities/concept/closure-contract.md` (in broomva
  workspace) — captures the 5-tuple generalization Phase 8 helps enable.

## 0.17.0 — 2026-05-22

### Crystallize `BROOMVA_ROOT` env-var convention (BRO-1223 follow-up)

Adds `references/conventions.md` — a new home for cross-cutting workspace conventions (the things every script should follow but no single primitive owns). First entry: **C1 — `BROOMVA_ROOT` env-var as the workspace-root override**, with rule-of-three earned across 6 callsites in 3 repos (broomva/kg, broomva/bookkeeping, broomva/workspace).

The convention prevents the failure mode surfaced by the haystack benchmark suite (BRO-1223): scripts that hardcode `~/broomva` silently overwrote the live workspace when invoked with non-standard layouts (e.g., isolated `/tmp/` fixtures). Documented patterns for Python + bash; documented `BROOMVA_WORKSPACE` (used by `bstack doctor`) as a historical synonym treated as semantically equivalent.

- **NEW** `references/conventions.md` — first cross-cutting workspace convention reference. Includes a candidate ledger for 4 additional patterns observed once during this session (LLM-as-index architecture, auto-compact catalog, haystack benchmark pattern, defensive post-conversion bounds check) — all awaiting rule-of-three before promotion.
- **CHANGED** `VERSION` — `0.16.0` → `0.17.0`. Additive minor bump — no behavior change, new reference doc.

### Why a new file (not extending an existing one)

`references/primitives.md` defines primitive contracts; `dogfood-patterns.md` defines per-stack validation patterns; `stack-architecture.md` describes the architectural composition. None is the right home for cross-cutting workspace conventions that span all primitives. A dedicated file keeps each reference doc's scope clean.

### Candidate ledger philosophy

The ledger at the bottom of `references/conventions.md` mirrors the workspace-side `research/entities/pattern/bstack-engine.md` candidate ledger — but listed here for bstack agents who don't have the workspace substrate available. The 4 listed candidates (LLM-as-index, auto-compact, haystack pattern, defensive bounds check) all derive from BRO-1223 work but are single-instance today; promotion to a full convention entry awaits 2 more recurrences each. Per the P16 Crystallize discipline: ≥3 instances + concrete mechanism + stated invariant + stated failure mode.

### Refs

BRO-1223 follow-up.

## 0.16.0 — 2026-05-22

### Multi-layer RCS closure — extending the control loop across L0/L1/L2

Closes the gap identified in PR #45 review: v0.14.0 wired *enforcement* only at L3 (governance), while **L0/L1/L2 had no audit, no per-layer doctor sections, no programmatic feedback into the control loop**. The receipts produced by `broomva/dogfood` were human-read artifacts but did not feed back into the multi-layer stability budget.

This release adds per-layer sensors, audit logs, doctor sections, and a composite multi-layer health report. The dogfood receipt and every tool call become the empirical sensors that calibrate the RCS hierarchy.

### New scripts (5)

- **NEW** `scripts/l0-tool-audit-hook.sh` — Claude Code PostToolUse hook. Logs every tool call (tool name, latency_ms, is_error, file_path when applicable) as one JSONL line to `.control/audit/l0-tools.jsonl`. Always exits 0; never blocks.

- **NEW** `scripts/l1-reflex-audit-hook.sh` — Claude Code Stop hook. Scans the session transcript for evidence of 21 /autonomous reflexes firing (mechanism cube, lens intake, snapshot, dep-chain, worktree decision, ticket, dogfood plan, validation, first write, empirical, PR opened, watcher, healing, cross-review, deploy verify, receipt, PR comments, auto-merge, cleanup, bridge, bookkeeping). Writes per-session compliance bitmask + anti-rationalization-line evaluation to `.control/audit/l1-reflexes.jsonl`.

- **NEW** `scripts/l2-promotion-audit-hook.sh` — L2 (EGRI / Crystallize P16) candidate-promotion sensor. Called by `bookkeeping.py promote` step with promotion metadata. Counts promotions in last τ_a₂ window (1h default); enforces budget (5 promotions/window default). Exit 2 with warning when over budget — caller SHOULD defer remaining promotions.

- **NEW** `scripts/compute-budget-status.sh` — Multi-layer health reader. Reads all four audit logs (.control/audit/l[0-3]-*.jsonl) + parameters.toml; computes per-layer observed metrics in each layer's τ_a window; emits composite verdict (stable / stable_warn / unstable) per layer + overall.

- **NEW** `scripts/install-rcs-stability.sh` — Unified multi-layer installer. Delegates L3 setup to install-l3-stability.sh (preserves v0.14.0 behavior), then merges PostToolUse (L0) + Stop (L1) hook entries into `.claude/settings.json` via `_bstack_primitive` markers. Creates `.control/audit/` directory. Idempotent.

### New template (1)

- **NEW** `assets/templates/settings.json.multi-layer-hooks.snippet` — PostToolUse + Stop hook entries. Composes additively with v0.14.0's `settings.json.l3-stability-hook.snippet` (PreToolUse) — each hook entry is uniquely identified by `_bstack_primitive` marker (`L0-audit`, `L1-audit`, `L3-G0`) so re-installation is structurally idempotent.

### Doctor extensions (4 new sections)

- **CHANGED** `scripts/doctor.sh` adds:
  - **§16 L0 plant audit** — tool-call count + latency mean + error count over last 10min (informational; hard gap only on >10000 events runaway).
  - **§17 L1 autonomic reflex compliance** — per-session mean compliance rate over last 24h + dogfood-yes count (hard gap if < 30%; soft warn 30-60%).
  - **§18 L2 EGRI promotion throttle** — promotions in last τ_a₂ window vs budget; hard gap when over budget.
  - **§19 Multi-layer composite health** — calls compute-budget-status; surfaces per-layer verdicts (`L0=stable L1=stable L2=stable L3=stable` form). Hard gap only if any layer "unstable".

### Onboard + repair (wired to install-rcs-stability)

- **CHANGED** `scripts/onboard.sh` calls `install-rcs-stability.sh` after bootstrap (replaces v0.14.0 call to `install-l3-stability.sh`). Falls back to L3-only installer if multi-layer one is absent.
- **CHANGED** `scripts/repair.sh` runs `install-rcs-stability.sh` when doctor reports any L0/L1 audit-log gap, missing G0/G1/G2, or unstable λ. Falls back to L3-only installer.

### What changes operationally

Before v0.16.0:
- L0/L1/L2 stability was uncontrolled at the audit level.
- The dogfood receipt was a PR artifact, not a control-loop sensor.

After v0.16.0:
- Every tool call logs to L0 audit. Every session ends with an L1 reflex-compliance record. Every bookkeeping promotion records to L2.
- `bstack doctor §19` and `bash scripts/compute-budget-status.sh --human` show the multi-layer health on demand.
- The dogfood receipt's anti-rationalization line is parsed by the L1 Stop hook and recorded as a binary signal — receipts auto-feed back into the control loop.

### The 4-gate flow now per-layer

| Layer | Sensor | Audit log | Doctor section | Throttle |
|---|---|---|---|---|
| L0 plant | PostToolUse hook | `l0-tools.jsonl` | §16 | informational; runaway detection |
| L1 autonomic | Stop hook (transcript scanner) | `l1-reflexes.jsonl` | §17 | hard gap if compliance < 30% |
| L2 EGRI | bookkeeping promote step | `l2-promotions.jsonl` | §18 | hard gap if over τ_a₂ budget |
| L3 governance | PreToolUse + git pre-commit + GH Actions (v0.14.0) | `l3-edits.jsonl` | §14 + §15 | hard gap if λ₃ ≤ 0 |

### Test plan executed

- `bash -n scripts/*.sh` — syntax clean on all new + modified scripts
- `compute-budget-status.sh --human` against ~/broomva → reports L0=stable L1=stable L2=stable L3=stable; composite ω = 0.006398
- L0 hook tested with synthetic JSON input → entry appended correctly to l0-tools.jsonl
- L2 hook tested with `--slug --type --score --source` → entry appended; over-budget case (5 + 1 in 1h) → exit 2 with warning
- `install-rcs-stability.sh --dry-run` on fresh workspace → reports all install steps without writing
- `install-rcs-stability.sh` real → L3 (4 files via install-l3-stability) + L0 + L1 hooks merged into settings.json + .control/audit/ created
- Re-run installer → all hooks skipped via `_bstack_primitive` markers (idempotent)
- doctor.sh against ~/broomva → §16-§18 informational (audit logs not yet wired in broomva); §19 reports L0=stable L1=stable L2=stable L3=stable composite. 89/91 passed, 2 pre-existing gaps unrelated.

### Honest scope caveats

- L0 audit-log retention is unbounded by default; rotation policy (`policy.yaml [audit_retention]`) is deferred to a follow-up.
- L2 wiring requires `bookkeeping.py promote` to call `l2-promotion-audit-hook.sh`. The hook itself ships in this PR; the bookkeeping integration is a small follow-up in the broomva/bookkeeping repo (one-line subprocess.run after the promotion write).
- L1 transcript-scanner uses heuristic substring matches against the session log. False positives possible (e.g., "interceptor" in a non-validation context counts as r10_empirical). The heuristic is intentionally permissive at v0.16.0; tightening is a follow-up after rule-of-three failure cases accumulate in the audit log.

### Spec doc + cross-references

- Spec: `~/broomva/conductor/workspaces/broomva/doha/docs/reports/2026-05-22-multi-layer-closure-spec.html`
- Prior PR (L3 closure): broomva/bstack#45 (v0.14.0, merged 2026-05-22)
- Dogfood skill: github.com/broomva/dogfood v0.1.0
- /autonomous flow record: `~/broomva/conductor/workspaces/broomva/doha/docs/reports/2026-05-22-autonomous-flow-achieved.html`

---

## 0.15.0 — 2026-05-22

### Add `broomva/kg` to managed-skill registry + catalog policy reader (BRO-1223 follow-up)

Promotes the LLM-as-index `/kg load` skill from workspace-local v1 (shipped in 0.12.0 at `~/.claude/skills/kg/`) to a managed bstack companion skill. This closes the **Future work** item explicitly anchored in the 0.12.0 CHANGELOG: *"Promote `broomva/kg` to its own GitHub repo + bstack skills-lock entry once usage exceeds rule-of-three (≥3 sessions with load-bearing `/kg load` invocations)."*

The skill is what makes the LLM-as-index architecture (BRO-1223) ergonomic in agent sessions: tier-1 (catalog-only, ~5ms) routes via `docs/knowledge-index.md` for the common case; tier-2 (body-grep fallback, ~300ms) auto-fires when tier-1 returns fewer than N matches, recovering topics whose vocabulary lives in entity prose but not the dense catalog. Empirical receipts from rule-of-three: peak per-query context drops from 29% → 4.6% of 1M (6.3× reduction); cumulative session tokens 23.8× fewer over 10 queries.

- **CHANGED** `references/companion-skills.yaml` — adds `kg` as a `required: true` knowledge-category skill under primitive **P6**, placed alongside `knowledge-graph-memory` (its sibling in the P6 family).
- **CHANGED** `scripts/doctor.sh` §7 — the `/kg load` check now (a) accepts either a managed install (`~/.claude/skills/kg/` OR `~/.agents/skills/kg/`) or a legacy workspace-local v1 install, and (b) the gap message points at `npx skills add broomva/kg`.
- **CHANGED** `references/skills-roster.md` — adds `kg` as row #6 in **Memory & Consciousness**, between `knowledge-graph-memory` and `prompt-library`. Header count 27 → 28.
- **CHANGED** `VERSION` — `0.14.0` → `0.15.0`. Minor bump because this adds a new **required** managed skill.

### `scripts/doctor.sh` §7 catalog stale threshold now reads from `.control/policy.yaml` (closes BRO-1223 I1)

§7 catalog freshness check previously hardcoded `48h`. Three components used three different "stale" values across the workspace (kg.py: 24h, doctor.sh: 48h, hook: 5-min cooldown) — BRO-1223 P20 flagged this as I1.

- **CHANGED** `scripts/doctor.sh` §7 — `_stale_h` sourced from `.control/policy.yaml` `catalog.stale_doctor_hours` (single source of truth), falling back to 48h when policy.yaml is absent/malformed/PyYAML-missing. Active threshold reported in OK message.
- **P20 round-1 defensive coding**: argv-passed key/default via `sys.argv` (avoids `SyntaxError` on single-quote-in-path); regex-validated output (`[[ "$_raw" =~ ^[0-9]+$ ]]`) — empty/non-numeric stdout falls back cleanly.

Pairs with `broomva/workspace` PR (adds the `catalog:` block to `.control/policy.yaml` + rewires `kg.py` + `knowledge-catalog-refresh-hook.sh` to read from it via the same pattern). All three consumers now sit behind a single source of truth. Also pairs with `broomva/kg` v0.2.0 push (BROOMVA_ROOT env var + `_load_catalog_policy()` at the skill source — closes the P20 C2 self-regression vector).

### Cross-repo composition

- **`broomva/kg`** — live at https://github.com/broomva/kg, now at v0.2.0 (MIT-licensed; ships `SKILL.md`, `scripts/kg.py` with BROOMVA_ROOT env var + policy.yaml reader, `README.md`, `LICENSE`). Pushed BEFORE this PR merges to prevent the self-regression vector flagged by P20.
- **`broomva/workspace`** — adds the `catalog:` policy block + env-var overrides in `kg.py` mirror + `bench-kg.py`.
- **`broomva/bookkeeping`** — adds `BROOMVA_ROOT`/`KG_PY` env-var lookup in the tier-2 regression test.

### Why P6, not a new primitive

`kg` operationalizes P6 (Bookkeeping) at the **load** end of the loop — the dual of `knowledge-graph-memory` (which captures conversation logs into the knowledge graph). Same primitive, opposite arrow. No L3 stability-budget impact because no new P-N row.

---

## 0.14.0 — 2026-05-22

### L3 stability closure — compute + enforce λ in every bstack workspace

Closes the gap between the RCS paper (`research/rcs/papers/p0-foundations/`) and operational reality. Previously, `λ₃ ≈ 0.006` was cited in CLAUDE.md and AGENTS.md as static text — no bstack script or hook computed or enforced it. This release wires the math + a four-gate flow (Claude Code hook → git pre-commit → CI → doctor) so every bstack-using workspace inherits computational stability checking on first onboard.

### New scripts

- **NEW** `scripts/compute-lambda.sh` — bash CLI that recomputes per-level λᵢ from a workspace's `parameters.toml` (looks at `.control/rcs-parameters.toml`, `research/rcs/data/parameters.toml`, or the bundled template). Implements the formula `λᵢ = γᵢ − L_θᵢ·ρᵢ − L_dᵢ·ηᵢ − βᵢ·τ̄ᵢ − ln(νᵢ)/τ_aᵢ`. Emits JSON or human-readable; exit 0 if all stable, 1 if any λᵢ ≤ 0, 3 on drift > 1e-4 (`--strict`).

- **NEW** `scripts/l3-rate-gate.sh` — governance commit rate limiter. Reads L3-class path patterns from `.control/rcs-parameters.toml` `[gates.l3_paths]` (default: CLAUDE.md, AGENTS.md, .control/policy.yaml, .control/rcs-parameters.toml, METALAYER.md). Counts L3-class commits in the last τ_a₃ window (default 86400s = 1 day). Exits 0 within budget, 1 exceeded. Supports `--staged` (include uncommitted-but-staged for pre-commit use) and `--warn-only`.

- **NEW** `scripts/install-l3-stability.sh` — one-shot installer that deploys the L3 gate flow into a workspace: `.control/rcs-parameters.toml` + `.githooks/pre-commit` (G1) + `.github/workflows/l3-stability.yml` (G2) + `.claude/settings.json` PreToolUse hook entry (G0). Idempotent; existing files preserved unless `--force`. Settings.json merge is structurally idempotent (skips if `_bstack_primitive: "L3-G0"` already present).

- **NEW** `scripts/l3-stability-pretool-hook.sh` — Claude Code PreToolUse hook backend. Receives tool-call JSON on stdin; emits warning + audit-log entry when an Edit/Write/MultiEdit targets an L3 path. Defaults to `approve` (informational) — never blocks the agent; emits a `reason` string Claude Code surfaces into context.

### New templates

- **NEW** `assets/templates/rcs-parameters.toml.template` — default workspace parameters (L0–L3 calibrated from `research/rcs/data/parameters.toml`). Includes `[derived.lambda]` cached values + `[gates.l3_paths]` patterns. Self-documenting with the formula and customization notes for non-Life runtimes.

- **NEW** `assets/templates/githook-pre-commit-l3-rate.sh.template` — Gate G1 git pre-commit hook. Calls `l3-rate-gate.sh --staged`; blocks commit if rate exceeded (override with `git commit --no-verify`). Chains to existing `.githooks/pre-commit.local` if user had a hook before bstack onboarded.

- **NEW** `assets/templates/gh-workflow-l3-stability.yml.template` — Gate G2 GitHub Actions workflow. Triggers on PRs touching L3 paths; runs `compute-lambda.sh` + `l3-rate-gate.sh`; comments on the PR with the per-level λ + composite ω + rate verdict; status check `stability-check` fails if any λᵢ ≤ 0 (can be made required via branch protection).

- **NEW** `assets/templates/settings.json.l3-stability-hook.snippet` — Gate G0 Claude Code PreToolUse hook entry. Merged into `.claude/settings.json` by `install-l3-stability.sh`. Fires on Edit/Write/MultiEdit; backend is `scripts/l3-stability-pretool-hook.sh`.

### Doctor extensions

- **CHANGED** `scripts/doctor.sh` adds two sections:
  - **§14 RCS stability budget** — calls `compute-lambda.sh`; reports composite ω; HARD gap if any λᵢ ≤ 0; SOFT gap on drift > 1e-4 under `--strict`.
  - **§15 L3 stability gate-flow wiring** — verifies G0 (settings.json hook), G1 (.githooks/pre-commit), G2 (.github/workflows/l3-stability.yml), and rcs-parameters.toml are present in the workspace. Each missing piece prints an `[info]` line with the install command. Informational only (not a hard gap).

### Onboarding + repair

- **CHANGED** `scripts/onboard.sh` calls `install-l3-stability.sh` after bootstrap. New workspaces get the full L3 gate flow on first install.
- **CHANGED** `scripts/repair.sh` runs `install-l3-stability.sh` when doctor reports any G0/G1/G2 piece missing, parameters.toml absent, or λᵢ ≤ 0. Idempotent.

### What this changes operationally

Before: `λ₃ ≈ 0.006` was a citation. The agent could read it in CLAUDE.md and reason about it, but no machine path computed or enforced it.

After: every bstack-using workspace can run `bash scripts/compute-lambda.sh --human` and see the live λ values for its own parameters. `bstack doctor` recomputes on every run. `bstack onboard` installs the four gates. CI fails on `λᵢ ≤ 0`. Git pre-commit blocks excess governance churn. Claude Code agents see warnings when editing L3 files.

The 4-gate flow:

| Gate | Trigger | Action | Blocking? |
|---|---|---|---|
| G0 — Claude Code PreToolUse | Edit/Write to L3 path | Warning to agent + audit log entry | No |
| G1 — git pre-commit | Staged L3 path + over-rate | Block commit (bypassable with --no-verify) | Yes (bypassable) |
| G2 — GitHub Actions on PR | L3 path changed | Comment with λ + rate verdict; fail status if λ ≤ 0 | Yes (if branch protection) |
| G3 — bstack doctor §14 + §15 | Every SessionStart + on demand | Recompute λ + verify wiring | Informational |

### Test plan executed

- `compute-lambda.sh --human` against broomva's `parameters.toml` → all 4 λᵢ match cached (drift ~ 0)
- `compute-lambda.sh` with `γ₃` perturbed from 0.01 → 0.001 → λ₃ = −0.0026; exit 1
- `l3-rate-gate.sh` 24h window against ~/broomva → 0 commits, within budget, exit 0
- `l3-rate-gate.sh --window=2592000` (30-day) → 142 L3 commits, exceeded, exit 1
- `install-l3-stability.sh` against fake workspace → 4 files installed; re-run skipped 3 + idempotent settings.json merge
- `l3-stability-pretool-hook.sh` with `{"file_path": ".../CLAUDE.md"}` → emits reason warning; with `{"file_path": ".../foo.ts"}` → silent approve
- `doctor.sh` against ~/broomva → §14 reports composite ω = 0.006398, all stable; §15 reports G0/G1/G2 missing (informational, since broomva hasn't run `install-l3-stability.sh` yet)

### Why this isn't a new primitive

The four gates are *mechanisms*, not new primitives. The primitive is the existing RCS L3 stability constraint (cited in CLAUDE.md `RCS Hierarchy` section and `Self-Evolution Protocol`). This release implements that constraint in code, where v0.13.0 implemented P11 Empirical operationalization in code. No bstack primitive count change.

---

## 0.13.0 — 2026-05-22

### P11 Empirical operationalization — Dogfood Plan reflex + per-stack cookbook + doctor §13

Closes P11's *operationalization gap*: the discipline of "validate by interacting" was well-defined, but agents lacked a concrete *how* keyed to the tech stack the workspace was instantiated from. This release adds:

- **NEW** `references/dogfood-patterns.md` — per-stack cookbook with surfaces matrix (Tauri+sidecar / Next.js / Expo RN / Rust CLI / REST API / MCP server). Each pattern names the canonical arc, the skill toolkit (Interceptor mandatory for visual deploy verification; gstack, cliclick, screencapture, curl+jq compose per stack), the gotchas observed in production, and the receipt template. Anchored by the Houston dogfood-pattern.html worked example.

- **CHANGED** `references/primitives.md` §P11 — adds reflex rule 7 (Dogfood Plan keyed to detected stack): before substantive work, the agent produces a plan (entry surface · driver · evidence · smoke · end-to-end · receipt anchor) in the response and PR body, citing the per-stack pattern from the cookbook. Companion-reference callout points to the cookbook.

- **CHANGED** `assets/templates/AGENTS.md.template` §P11 — propagates the reflex rule 7 to every new bstack'd project AND stubs a `## Dogfood Plan (Stack: TBD)` block with the row template, so the first substantive feature work has a concrete anchor to fill.

- **CHANGED** `SKILL.md` — surfaces `references/dogfood-patterns.md` in the on-demand reference index alongside the canonical primitive contract.

- **CHANGED** `scripts/doctor.sh` — adds §13 P11 Empirical dogfood-readiness. Auto-detects tech stack from repo signals (Cargo.toml + src-tauri/ → tauri-sidecar; next.config.* → nextjs; app.json + expo → expo-rn; Cargo.toml solo → rust-cli; openapi.* or REST-framework deps → rest-api; mcp.{json,yaml} → mcp-server). Verifies a Dogfood Plan anchor exists at one of three accepted locations (AGENTS.md `## Dogfood Plan`, `docs/dogfood-plan.md`, or PR body). Informational — never blocks (rule-of-three not yet hit; promotion to blocking gate requires ≥3 documented incidents per Crystallize P16).

- **CHANGED** `scripts/onboard.sh` — after bootstrap, auto-detects stack and substitutes the `## Dogfood Plan (Stack: TBD)` placeholder with the detected stack name. Surfaces the cookbook reference in the next-step receipt. Persists detected stack in the initialization marker.

### L3 stability budget — why this isn't P21

P11 already covers "validate by interacting" with full reflex rules. The gap was operationalization (the *how*), not coverage. Adding a 21st primitive for the cookbook would consume L3 stability budget (λ₃ ≈ 0.006) for no policy delta. Instead: sub-rule + cookbook + doctor check at L2 operationalization layer.

### Promotion gating for §13

The §13 check ships as informational (warn-only). Promotion to `policy.yaml` blocking gate requires (a) ≥3 documented incidents where missing Dogfood Plan caused user-visible regression, (b) the unambiguous blocking criterion ("PR has no Dogfood Plan in body"), (c) failure mode named (P11 ritual without substance), (d) L3 stability budget available. Logged in `research/entities/pattern/bstack-engine.md` candidate ledger.

### Companion artifact

Human-readable record of /autonomous flow integration at `~/broomva/docs/reports/2026-05-22-autonomous-flow-achieved.html` (in workspace repo, separate PR). Per P18 Audience: HTML for human reading; this CHANGELOG + the cookbook + the primitives reference stay markdown for agent loading.

---

## 0.12.0 — 2026-05-21

### LLM-as-index wiring — catalog refresh hook + `/kg load` skill doctor checks (BRO-1223)

Wires the workspace-side LLM-as-index architecture into bstack's bootstrap and audit machinery. The workspace ships a Stop hook that regenerates `docs/knowledge-index.md` after each session (so the dense catalog stays fresh without manual `bookkeeping index` invocations) and a Claude skill `/kg load <topic>` that uses the catalog to route the loading agent.

The architectural anchor: at sub-10k-entity scale, the substrate (`research/entities/**/*.md`) fits in any 1M-context model with >95% headroom — the LLM **is** the index. One projection (the catalog), one consumer (the loading agent), inferences fold back into the substrate as commits. No SQLite mirror, no embeddings, no typed-edge schema.

- **CHANGED** `assets/templates/settings.json.snippet` — adds a second `Stop` hook entry pointing at `${BROOMVA_WORKSPACE}/scripts/knowledge-catalog-refresh-hook.sh`, tagged `_bstack_primitive: "P6"`. New installs and `bstack repair` invocations now wire the catalog refresh by default. Composes cleanly with the existing `conversation-bridge-hook.sh` Stop entry — bridge first, catalog second.
- **CHANGED** `scripts/doctor.sh` — section 7 (Primitive mechanisms) gains three new P6 sub-checks:
  - `scripts/knowledge-catalog-refresh-hook.sh` present + executable
  - `docs/knowledge-index.md` exists AND mtime ≤ 48h (catalog freshness gate; warns at >48h with the repair command)
  - `~/.claude/skills/kg/SKILL.md` + `scripts/kg.py` present (the load skill)

  All three are advisory (informational nudges) — they never fail strict-mode CI on missing-skill, because the kg skill is intentionally workspace-local v1 (no GitHub repo, no skills-lock entry) until rule-of-three earns the promotion.

### Cross-repo composition

This release pairs with two workspace-side PRs:
- `broomva/bookkeeping` — adds `cmd_index` (the catalog generator) + `from __future__ import annotations` Py3.9 compatibility fix
- `broomva/broomva` — adds the Stop hook script, wires `.claude/settings.json`, adds AGENTS.md §P6 sub-rule on substrate-edit-as-inference-persistence, ships the `/kg load` skill at `~/.claude/skills/kg/`, mirrors documentation at `docs/skills/kg.md`

### Validation gate

The composite release is validated by the **12-query parity test** documented in the PR body: each of 12 representative graph queries (tag intersection, k-hop neighborhood, shortest path, provenance trace, hub identification, full-text body search, cross-type co-occurrence, etc.) must be answerable by the agent using only `/kg load` + on-demand entity reads, matching the deterministic Python-script baseline.

### Future work

- Promote `broomva/kg` to its own GitHub repo + bstack skills-lock entry once usage exceeds rule-of-three (≥3 sessions with load-bearing `/kg load` invocations)
- Add `bookkeeping index --cache-graph` to memoize the parsed in-memory graph to `~/.cache/broomva/bookkeeping/graph.json` (drops repeat runs from ~315ms to ~5ms)
- Optional sidecar embeddings index at >2k entities (not needed today; pre-mature now)

## 0.11.0 — 2026-05-20

### Live mode for `bstack bench` — Databricks Gateway provider + OpenAI-compatible abstraction (BRO-1211)

Closes the live-mode gap left open by v0.10.0 (BRO-1205). v0.10.0 shipped `StubLiveRunner` + `StubLLMJudgeEvaluator` that raised NotImplementedError. v0.11.0 ships the real thing: an industry-standard provider abstraction with **Databricks Model Serving Gateway** as the first concrete provider. Live mode validated end-to-end against real Databricks Anthropic Claude endpoints (5/5 live tests green; 221 real tokens on a `databricks-claude-haiku-4-5` call; Haiku-agent + Sonnet-judge run reached quality cliff at rc=0).

The contract bstack adopts is **OpenAI Chat Completions API v1** — the de facto LLM standard in 2026, served by Databricks, OpenAI, Anthropic-via-Bedrock, Together, Fireworks, Anyscale, vLLM, llama.cpp, etc. Future providers (anthropic, openai, openai-compat, bedrock) plug in by implementing `Provider.chat()`.

- **NEW** `scripts/bench/providers/` package (stdlib + optional `openai` SDK):
  - `base.py` — `Provider` ABC + OpenAI-compatible `ChatMessage` / `Usage` / `ChatCompletion` types + `ProviderError` / `ProviderNotConfigured` / `ProviderNotInstalled` taxonomy + `estimate_cost_usd()` with per-model pricing table.
  - `databricks.py` — `DatabricksGatewayProvider`: wraps the OpenAI SDK with `base_url = {DATABRICKS_HOST}/serving-endpoints` + `api_key = DATABRICKS_TOKEN`. Mirrors Stimulus's `apps/api/src/utils/databricks_openai.py` pattern. Known models hardcoded: `databricks-claude-{haiku-4-5, sonnet-4, opus-4-5}` + `databricks-meta-llama-4-maverick`.
  - `registry.py` — `get_provider(name, **kwargs)` factory with lazy module loading. Built-in providers: `databricks`, `mock`. Runtime extension via `register_provider()`.
  - `__init__.py` — public API exports.
- **NEW** `references/provider-standards.md` — documents OpenAI-compatible contract bstack adopts, how to add a new provider, P20 model-isolation enforcement rules, and Railway credential-broker invocation pattern.
- **NEW** `tests/bench-providers.test.sh` — 10 offline tests covering unknown-provider error, missing `--model`, mock provider end-to-end (real `chat()` call), P20 violation (rc=8), P20 override rationale captured in config, P20 distinct-models accepted, `DATABRICKS_TOKEN` absent (rc=9), `list_providers()` shape, provider-standards doc presence, public API symbol coverage. All green.
- **NEW** `tests/bench-live.test.sh` — 5 live integration tests (gated by `BSTACK_BENCH_LIVE=1` + `DATABRICKS_HOST` + `DATABRICKS_TOKEN`). Validates: `DatabricksGatewayProvider` instantiates with real creds, minimal `chat()` returns PONG with parseable usage stats, Phase 1 run produces non-canned token counts (real Databricks usage), `LLMJudgeEvaluator` end-to-end (Haiku agent + Sonnet judge), P20 enforcement holds in live mode. **All 5 passed against real Databricks at ship time** — this PR ships proven-working live mode, not stubs.
- **CHANGED** `scripts/bench/agent_runner.py` — `LiveProviderRunner` replaces `StubLiveRunner` (which is kept as legacy fallback). Delegates to a Provider, captures real token usage from `completion.usage`, writes deliverables, estimates cost from per-model pricing table.
- **CHANGED** `scripts/bench/evaluator.py` — `LLMJudgeEvaluator` replaces `StubLLMJudgeEvaluator`. Builds structured judge prompt from rubric criteria, parses JSON verdict (with fallback for prose-wrapped output), computes weighted pass rate, applies 0.6 cliff. Handles judge-side provider errors + parse failures gracefully (no traceback).
- **CHANGED** `scripts/bench/orchestrator.py` — adds `--provider`, `--model`, `--judge-model`, `--allow-same-judge-model RATIONALE` flags. Enforces P20 model isolation: judge model MUST differ from agent model unless explicit override with rationale (captured in `config.json` for audit). New exit codes 8 (P20 violation), 9 (provider not configured), 10 (SDK not installed).
- **CHANGED** `bin/bstack-bench` — surfaces new flags in `--help`; documents new exit codes; adds live-mode invocation examples (direct + Railway credential broker pattern).
- **CHANGED** `tests/bench-mvp.test.sh` — tests #13 + #15 updated for v0.11.0 semantics: live runner / llm-judge without `--provider` now fails *fast* at the CLI layer with rc=2 + "--provider required" instead of reaching the stub. Cleaner error, surfaces missing config before any task runs.

### Design choices

- **OpenAI Chat Completions API is the contract.** Picked because Databricks, OpenAI, vLLM, Together, Fireworks, Anyscale, llama.cpp, and Anthropic-via-Bedrock all serve identical request/response JSON. Choosing the same shape means new providers ship with zero translation layer.
- **`openai` SDK is a soft dependency.** Imported lazily inside `DatabricksGatewayProvider.__init__`; raises `ProviderNotInstalled` with install hint when missing. CI doesn't install it (mock provider covers offline tests). Live runs need it.
- **Railway as credential broker.** Recommended invocation: `railway run --service stimulus-api -- bstack bench run ...`. Credentials never written to disk in the bstack tree. Direct env export works identically.
- **P20 enforcement at CLI layer.** Same model for agent + judge is the same-model-echo-chamber failure mode P20 exists for. Rejected with rc=8 unless `--allow-same-judge-model "rationale"` is passed; rationale is captured in `config.json` for audit.
- **Mock provider is built in.** Deterministic in-process provider; tests + CI never need network or credentials. Same registry, same factory, same API as `databricks`.
- **No `.env` file loading at runtime.** Bstack reads `os.environ`; how vars get there is the caller's concern (Railway, direnv, sops, 1Password, manual export — all work).
- **Stimulus pattern mirrored.** `DatabricksGatewayProvider` directly mirrors `apps/api/src/utils/databricks_openai.py` — same base_url construction (`{HOST}/serving-endpoints`), same auth (token as `api_key`), same model name conventions.

### What this enables (next BRO-1205 followups, now unblocked)

- Per-skill telemetry counters can land — substrate now produces real token usage to populate them.
- Crystallize (P16) FIX/DERIVED/RETIRE sub-modes can read from real bench runs, not synthetic numbers.
- Cross-provider benchmarking — agent on Databricks Claude, judge on OpenAI GPT-4o (when `openai` provider lands).
- Cost-per-quality measurement — bench reports now include real `cost_usd` from `estimate_cost_usd()`.

### Test counts

- `tests/bench-mvp.test.sh`: 18 → 18 (unchanged, two assertions retargeted for v0.11.0 semantics)
- `tests/bench-providers.test.sh`: NEW, 10 assertions
- `tests/bench-live.test.sh`: NEW, 5 assertions (gated, ran green at ship time)
- Total: 33 offline + 5 live (gated) = 38 assertions

### Linked artifacts

- Linear: BRO-1211 (this PR); BRO-1205 (predecessor — MVP)
- Spec: `specs/bench-skill-evolution.md` (updated)
- Reference: `references/provider-standards.md` (NEW)
- Stimulus mirror: `apps/api/src/utils/databricks_openai.py` (reference implementation)

### Cross-Review (P20) round-1 fixes (applied before merge)

Fresh-context subagent scored round-0 at 6.6/10 (below ≥7/10 threshold) and surfaced 3 blocking defects + 2 should-fix items. Round 1 closes all 5 with 6 new adversarial tests + 2 de-rigged existing tests:

- **Defect #1 — budget escape via unknown model.** `agent_runner.py` silently zeroed `cost_usd` for any model absent from `base.py:_COST_TABLE_USD_PER_MILLION`, defeating `--budget-usd`. Fix: orchestrator refuses to start with rc=2 + "not in the cost table" when `--budget-usd` set against an unknown model. Opt-out via `--allow-unknown-cost RATIONALE` captured in config. Test #11 + #12.
- **Defect #2 — resume silently destroyed audit trail.** `orchestrator.py` unconditionally overwrote `config.json` on resume, allowing silent model/provider/judge_model swap mid-run. Fix: on resume, diff incoming args against stored config; rc=2 if any of `{provider, model, judge_model, runner, evaluator}` changed without `--allow-config-drift RATIONALE`. Test #13 + #14.
- **Defect #3 — test rigging.** `bench-providers.test.sh:99` and `bench-live.test.sh:172` accepted `rc=0 OR rc=6` — same harness-can-subtract anti-pattern P20 round-0 flagged on PR #40. Fix: provider tests now assert `tokens=20 + runner=live + config records provider/model`; live judge test now asserts ≥1 clean judge result (no `parse-fail`/`id-mismatch` suffix).
- **Defect #4 — judge ID hallucination silently scored 0.** When the judge returned criterion IDs that didn't match the rubric, `verdicts.get(cid, False)` returned False for every rubric ID → score 0.0 with judge's positive `overall_feedback` preserved verbatim. Fix: `LLMJudgeEvaluator.evaluate` detects set-difference between rubric IDs and judge-returned IDs; on mismatch, evaluator name becomes `llm-judge(id-mismatch)` and feedback is prefixed `[ID-MISMATCH]` listing unrecognized + missing IDs. Test #15.
- **Defect #5 — `max_tokens=2048` hardcoded.** No CLI override → silent truncation of large deliverables or many-criteria judge verdicts. Fix: `--max-tokens` + `--judge-max-tokens` CLI flags, default 2048, threaded through `get_runner` + `get_evaluator`. Test #16.

Test count: provider tests 10 → 16 (+6 adversarial). Live tests still 5/5 against real Databricks, with the judge assertion now substantive ("3 task(s) judged cleanly").

## 0.10.0 — 2026-05-20

### Skill-evolution benchmark substrate (BRO-1205)

Closes the **Empirical (P11)** substrate gap. Before 0.10.0, bstack had L3 stability margins (λ₃ ≈ 0.006) and a 20-primitive composition graph but **no empirical performance number** — every P-primitive promotion was faith-based. 0.10.0 ships the harness that makes those claims falsifiable.

Origin: HKUDS/OpenSpace research dive (see `research/entities/project/openspace.md` + `research/notes/2026-05-20-openspace-evolver-synthesis.md` in the consuming workspace). OpenSpace's `gdpval_bench/` shipped 4.2× higher earned income vs ClawWork baseline + 46% Phase 2 token reduction on GDPVal; this is the bstack-native port of that substrate, refactored to drop OpenSpace coupling and apply P20 Cross-Review discipline (judge model MUST differ from agent model).

- **NEW** `scripts/bench/` Python package (stdlib only — zero third-party deps):
  - `orchestrator.py` — two-phase loop (Phase 1 cold → snapshot skills → Phase 2 warm → compare). Subcommands: `run | compare | tasks list | status`. Resume support via `--resume <run-id>`. Budget cap via `--budget-usd N` (exit 4 when exceeded). All-tasks-failed → exit 6.
  - `task_loader.py` — `Task` dataclass + JSONL loader. `BSTACK_BENCH_TASKS_DIR` env override for tests.
  - `agent_runner.py` — `DryRunRunner` (canned, deterministic, $0 cost) + `StubLiveRunner` (clear NotImplementedError pointing to spec). Pluggable contract for future `claude-code`/`codex`/`vanilla-anthropic` runners.
  - `evaluator.py` — `RubricMatchEvaluator` (deterministic rubric checks: `has_section` / `sentence_count_at_least` / `bullet_count_at_least` / `contains_any`) + `StubLLMJudgeEvaluator` for the future LLM judge. 0.6 quality cliff (matches OpenSpace + ClawWork policy: `quality < 0.6` → payment = 0).
  - `tasks/bstack-smoke.jsonl` — 3 hand-written bstack-themed tasks (Linear ticket triage, PR diff summary, primitive-symptom matching) with simple rubrics.
- **NEW** `bin/bstack-bench` — bash dispatcher. Mirrors `bstack-crystallize`'s shape. Robust Python interpreter discovery (PATH lookup + well-known absolute install paths for restricted-PATH environments).
- **NEW** `tests/bench-mvp.test.sh` — 14-assertion smoke test verifying: dispatcher `--help`, task set discovery, exit code shape (2/3/4/5/6), JSONL result schema, comparison + REPORT.md generation, Phase 2 token ratio < 1.0 + Δquality ≥ 0 (canned dry-run deltas), `compare` without args picks latest, `status` lists runs, budget cap behavior, live-stub-runner clear migration message, skill-snapshot tarball creation.
- **CHANGED** `bin/bstack` — dispatcher: `bench` subcommand wired alongside `crystallize`; usage text updated.
- **CHANGED** `SKILL.md` — Quick start section lists `/bstack bench` triplet.

### Design choices

- **Stdlib only.** No `anthropic`, no `litellm`, no third-party deps. Matches `crystallize.py`'s discipline. CI runners ship Python 3.10+; macOS dev fallback probes `/opt/homebrew/bin/python3.X` directly.
- **Dry-run is the default.** v0.10.0 ships rubric matching + canned responses; live mode is a stub with a clear migration message ("install anthropic SDK + set ANTHROPIC_API_KEY"). This is the responsible /autonomous path — the substrate is exercisable for free; live mode opt-in flips on when SDK+key are wired in a future PR.
- **Two-phase protocol from OpenSpace, not the SQLite content-snapshot+diff lineage.** Git already gives us lineage on entity pages + skills; we don't need OpenSpace's content-snapshot SQLite schema.
- **0.6 quality cliff preserved** (OpenSpace + ClawWork compatibility). Payment is $0 below cliff, full `task_value_usd` above.
- **Skill snapshot is synthetic in dry-run.** `_simulate_phase1_skill_dir()` mints a tiny fake `phase1-skills/` between phases so the snapshot tarball path is exercised without touching `~/.claude/skills/`.
- **P20 Cross-Review forward compatibility.** The evaluator docstring documents the upcoming constraint: judge model MUST differ from agent model. Enforcement lands when the LLM judge stub is replaced.
- **State location follows bstack convention.** `~/.config/bstack/bench/runs/<run-id>/` (mirrors `~/.config/broomva/p7/`, `~/.config/broomva/p8-janitor/`). Override via `BSTACK_BENCH_HOME` env var (used by tests).

### What this enables

- Future PRs can wire FIX/DERIVED/RETIRE sub-modes of Crystallize (P16) — extending P16 from CAPTURED-only to all four sub-modes (the OpenSpace decomposition). Per-skill telemetry counters (`total_selections / total_applied / total_completions / total_fallbacks`) and metric-driven evolution triggers are the next layer; this PR is the measurement substrate they build on.
- The `live` runner stub is the integration point for the Anthropic SDK / `claude --print` subprocess path. Once wired, the same harness runs real-LLM benchmarks against any bstack-instrumented agent.
- The substrate composes with P9 (`p9 watch` long-running benches), P12 (`persist iterate` multi-hour campaigns), and P19 (Orchestrate cube cell selection for bench shape).

### Linked artifacts (in the consuming workspace, not in this repo)

- Spec: `bstack/specs/bench-skill-evolution.md`
- Project entity: `research/entities/project/openspace.md` (9/9 Nous)
- Concept entity: `research/entities/concept/skill-self-evolution.md` (7/9 candidate)
- Synthesis: `research/notes/2026-05-20-openspace-evolver-synthesis.md`
- Linear ticket: BRO-1205

### Cross-Review (P20) round-1 fixes (applied before merge)

A fresh-context subagent under devil's-advocate brief scored the first push at 5/10 (below the ≥7/10 P20 threshold) and surfaced four correctness defects + dead code. Round 1 closes all four with adversarial tests (one per defect, written to falsify the bug existed, not to confirm the fix works):

- **Defect #1 — evaluator stub leaked raw traceback.** `_run_task` caught `NotImplementedError` from `runner.run` but not `evaluator.evaluate`. `--evaluator llm-judge` now exits 6 with a clean stderr migration message, mirroring the runner path. Test #15.
- **Defect #2 — budget cap broken on resume.** `spent` initialized at 0.0 in `cmd_run`, ignoring prior-session costs in the existing JSONL. `--resume --budget-usd 0.01` could spend a fresh \$0.01 each session. Fixed: when `--resume`, sum `cost_usd` from every existing phase-results row before the phase loop. Refuses to start if prior cost already exceeds budget. Test #16.
- **Defect #3 — aggregate double-counted resumed tasks.** `_read_existing_results` returned every JSONL row; a task that failed then re-ran successfully produced two rows under the same `task_id`, inflating `task_count` and `total_tokens`. Fixed: last-write-wins dedup by `task_id` in `_read_existing_results`. Resume-completion contract preserved (success row, if any, is last). Test #17.
- **Defect #4 — compare emitted phantom regression on phase-1-only runs.** `_emit_compare` accepted empty phase2 lists and reported "Phase 2 = 0 tokens, Δquality = -0.8" — noise masquerading as data. Fixed: refuse to compare with exit 7 + clear message when either phase is empty. Test #18.
- **Cleanups.** Removed dead `--workers` argparse flag, unused `asdict` + `EvaluationResult` imports, dead `tasks.set_defaults(func=cmd_tasks)` (subparser `required=True` makes it unreachable), and the `# pragma: no cover (defensive)` slop tell.

Test count: 14 → 18 assertions. New exit code 7 documented in dispatcher + orchestrator headers.

## 0.9.5 — 2026-05-18

### Crystallization detection (Phase 7 of substrate completion)

Closes substrate-completion gap **4.4.1** — until now, P16 (rule-of-three crystallization) ran in the user's head. Phase 7 ships machine-assist: `bstack crystallize candidates` scans `docs/conversations/*.md` for patterns that recur in ≥3 distinct sessions with explicit failure-mode and acknowledgement signals. Candidates are surfaced for human approval; the substrate **never** auto-promotes a primitive.

- **NEW** `scripts/crystallize.py` — rule-of-three pattern detector (Python). Heuristics per spec §6 Phase 7:
  - Phrase recurs in ≥`--min-sessions` distinct conversation files (default 3)
  - ≥1 occurrence co-locates within a 200-char window of a failure-mode keyword (`failed`, `orphaned`, `race`, `regression`, `shipped broken`, …)
  - ≥1 occurrence co-locates with a repetition-acknowledgement keyword (`again`, `twice`, `third time`, `recurring`, `had to redo`, …)
  - Substring suppression: keep shorter phrase when it strictly recurs more often than a longer one (the longer is a phrasing variant; the shorter is the recurring kernel)
  - n-gram window: 2–4 tokens; 2-grams require both tokens be content (no stop-words); 3+-grams reject stop-word prefix or suffix and require ≥⌈n/2⌉ content tokens
  - Citation excerpts are scrubbed for common secret patterns (`sk-…`, `ghp_…`, `xoxb-…`, AWS/GCP keys, JWTs, generic `password:`/`token:` assignments) before emission — excerpts may flow into PR comments and CI artifacts
  - Failure/ack keyword lists overridable via `CRYSTALLIZE_FAILURE_KEYWORDS` / `CRYSTALLIZE_ACK_KEYWORDS` env vars
- **NEW** `bin/bstack-crystallize` — thin bash dispatcher; delegates to `scripts/crystallize.py`. Defaults `--conversations` to `$BSTACK_CONVERSATIONS` → `$BROOMVA_WORKSPACE/docs/conversations` → `$PWD/docs/conversations`.
- **NEW** `bstack crystallize candidates [--json] [--conversations <dir>] [--min-sessions <N>] [--limit <N>]` — surface detected candidates with citations + signal summaries.
- **NEW** `bstack crystallize promote <slug> [--json]` — draft a primitive scaffold (auto-detected pattern + failure mode + ack signals + citations + P16 manual-gate checklist). Explicitly **does not** auto-merge a primitive; the scaffold is a starting point, not a decision.
- **NEW** `tests/canary/05-crystallize.test.sh` — 14 assertions covering: fixtures present, `--help` lists both subcommands, `--json` shape, known squash-merge-race pattern surfaces at ≥3 sessions, `--min-sessions=99` returns 0 (no false positives), promote scaffold contains DRAFT + auto-merge disclaimer + P16 reference, unknown subcommand exits 2, missing conversations directory exits 3, unknown promote slug exits 4.
- **NEW** `tests/fixtures/conversations/{positive-1..4,negative-1..2}.md` — 4 fixtures sharing the `squash merge race` rule-of-three pattern + 2 negative fixtures (no recurrence / no failure-mode signal).
- **CHANGED** `bin/bstack` — dispatcher: `crystallize` subcommand wired alongside `skills`; usage text updated.

### Design choices

- **Detection, not promotion.** Phase 7's contract is that P16 stays a deliberate human decision. The scaffold output explicitly disclaims auto-merge and surfaces the four manual P16 gates (concrete mechanism, stated invariant, stated failure mode, short name).
- **Bounded false-positive risk.** Per the spec §8 risk table, the failure mode is *false-positive ritual detection*. Mitigation: candidates are surfaced for human review; the substring-suppression rule keeps the recurring kernel rather than every phrasing variant; the failure-mode + ack co-occurrence filter rejects phrases that recur without an actual problem signal.
- **No new dependencies.** Pure Python stdlib + standard `jq` (already a canary-suite dependency). The detector runs on the same Python interpreter that runs `scripts/wave.py` and the `measure-*.sh` setpoint scripts.

### SLO targets (introduced)

- `bstack crystallize candidates` (fixtures, ~6 files): p50 < 200ms, p99 < 1s
- `bstack crystallize candidates` (workspace, ~50 files): p50 < 2s, p99 < 5s
- `bstack crystallize promote <slug>`: p50 < 200ms, p99 < 1s (re-runs detection then formats one candidate)

### Exit codes

- `0` success (zero or more candidates surfaced)
- `2` invalid arguments / unknown subcommand
- `3` conversations directory missing
- `4` `promote` slug not found in current candidate set

### Out of scope for v0.9.5 (deferred)

- Setpoint-history-driven trend detection (open question §10.1)
- Cross-workspace candidate aggregation (depends on Phase 8 federation)
- Auto-PR drafting via `gh pr create` from `crystallize promote` (deliberate — keeps P16 a manual decision)

## 0.9.0 — 2026-05-18

### Vendored upgrade path + canary suite (Phase 6 of substrate completion)

Closes two v1.0 blockers from the substrate completion spec (§4.3.2, §4.6.2). Vendored installs (`npx skills add` produces these — no `.git`) can now self-upgrade via release tarball + sha256 verification + atomic swap. The canary suite verifies the substrate's load-bearing contracts hold on a fresh install — runs on every PR.

- **NEW** `bstack upgrade --self` for vendored installs (extends `bin/bstack` `bstack_upgrade_vendored`):
  - Downloads `bstack-vX.Y.Z.tar.gz` from the GitHub Release
  - Downloads matching `.sha256` sidecar
  - **Mandatory** sha256 verification — no `--skip-sha256` flag; fail-closed on mismatch
  - Atomic swap via `mv current → .bak`, `mv new → install`; rollback on swap failure
  - `BSTACK_DRY_RUN=1` env override prints the plan without writing
  - Falls back to manual `npx skills add` guidance if tarball missing (pre-v0.9.0 releases)
  - Structured log at `~/.bstack/auto-upgrade.log`
- **CHANGED** `.github/workflows/release.yml` — new `Package + publish vendored upgrade tarball` step:
  - Builds `bstack-vX.Y.Z.tar.gz` from the in-repo skill payload (excludes `.git`, `.github`, `tests`, worktree dirs)
  - Uses `tar --sort=name` for byte-deterministic tarballs
  - Computes sha256, uploads tarball + sha256 sidecar via `gh release upload --clobber`
- **NEW** `tests/canary/01-fresh-bootstrap.test.sh` — Plant Contract verification on a fresh workspace (10 assertions: bootstrap exits 0, governance files scaffold, hooks wired for SessionStart/Stop/PreToolUse, doctor produces expected summary, doctor exits 0 per HC-1)
- **NEW** `tests/canary/02-metrics-pipeline.test.sh` — Phase 1 (v0.4.0) end-to-end: collect produces valid JSON, latest.json written, observe single-setpoint returns id-matched output
- **NEW** `tests/canary/03-status-surface.test.sh` — Phase 2 (v0.5.0) end-to-end: 7 core sections render, --json shape valid, --setpoint deep-view, --aggregate Phase 8 placeholder
- **NEW** `tests/canary/04-schemas-validate.test.sh` — Phase 3 (v0.6.0) contracts: 4 schemas valid draft-07, primitives.yaml validates, companion-skills.yaml validates, policy.yaml.template validates (top-level shape + flat-schema parts)
- **CHANGED** `.github/workflows/ci.yml` — new `canary` job gated on `lint` + `doctor`; installs jq + jsonschema + PyYAML; runs `tests/canary/*.test.sh`

### SLO targets (introduced)

- `bstack upgrade --self` (vendored, cold network): p50 < 30s, p99 < 60s
- canary suite (4 tests, sequential): p99 < 30s

### Supply-chain safety

- sha256 verification mandatory — no bypass flag
- Atomic swap with `.bak` rollback on failure
- Tarball excludes ephemeral state and CI tooling — only the canonical skill payload ships
- Backup retained until swap completes successfully

### Out of scope for v0.9.0 (deferred to v0.9.1)

- Cosign signature verification — sha256 covers the principal integrity concern; cosign adds publisher identity verification
- `bstack reproduce` subcommand (drift detection vs fresh-install reference)
- Canary tests 05-08 (skills auto-install, gates audit, release pipeline E2E) — ship as Phase 4-6 deliverables stabilize

## 0.8.0 — 2026-05-18

### Doctor extensions + pre-existing test fixes (Phase 5 of substrate completion)

`bstack doctor` now lints gate-enforcement consistency, and the two pre-existing tests that were excluded from the CI suite since 0.2.3 are fixed and back in the gate. The full `tests/*.test.sh` suite runs on every PR.

- **CHANGED** `scripts/doctor.sh` — new **Section 11: gate enforcement type validation**. For every blocking/governed gate in `.control/policy.yaml`, verifies the gate has either a `pattern` (regex for hook enforcement), an `enforcement.spec` (named runtime check), or a `measurement` (description of how compliance is verified). Advisory / soft / warn severities exempt. Catches gates declared blocking with no mechanism behind them (Gap 4.2.1).
- **CHANGED** `scripts/bootstrap.sh` — honors `BSTACK_SKIP_SKILLS=1` env to skip the npx skill-install loop. Used by CI fixtures and workspace operators wanting a governance-only bootstrap.
- **FIX** `tests/template_lockstep.test.sh` — `assert_contains` is now **case-insensitive** (`grep -iqF`). Canonical word can appear capitalized at sentence start (e.g. "Twenty irreducible primitives" in SKILL.md description) without breaking the lockstep contract — the test only cares the token + count are consistent across files. 15/15 now passing locally and in CI.
- **FIX** `tests/onboard.test.sh` — `run_onboard` now sets `BSTACK_SKIP_SKILLS=1` so `bootstrap.sh` short-circuits the network-bound `npx skills add` loop. Without it T3+ would block on the install loop and hang in CI. 8/8 now passing.
- **CHANGED** `.github/workflows/ci.yml` — drops the vetted-test allowlist. The `tests/*.test.sh` glob runs every test file on every PR. Removes the technical debt the allowlist was carrying since v0.2.3.

### Live verification against this workspace

```
$ bstack doctor (Section 11)
  [ok] all 12 declared gates have enforcement type (pattern / runtime_check / measurement)
```

### Closes gaps from substrate completion spec §4

- **4.2.1** — soft gates advisory without enforcement → doctor §11 makes the requirement machine-checkable
- **4.6.3** — vetted test allowlist → full suite runs now

### Out of scope (deferred)

The original Phase 5 spec included six more deliverables; this PR keeps focus on the high-impact items shipping fixes for the persistent technical debt. Deferred to **0.8.1 / Phase 5.1**:

- Doctor §12 (reflexive-primitive compliance sampling from `docs/conversations/`)
- Doctor §13 (skill roster lockstep — `SKILL.md` ROSTER ↔ `references/companion-skills.yaml`)
- `scripts/control-gate-hook.sh` moved into bstack (Gap 4.2.5 — currently workspace-only)
- `~/.bstack/gate-audit.jsonl` bypass-attempt log (Gap 4.2.4)
- `SLOs.md` consolidated latency budgets document
- `auto_merge` `p20_score >= 7` requirement for substantive PRs (Gap 4.2.3 — depends on `/cross-review` skill instrumentation)

The deferred items are each meaningful but composable; the 0.8.0 cut focuses on the test-debt closure since that gates everything downstream (canary suite in Phase 6 needs the full `tests/*.test.sh` running first).

### SLO targets

- `bstack doctor` end-to-end (full 11 sections): p50 < 1s, p99 < 3s
- Section 11 alone (12-gate fixture): p50 < 100ms, p99 < 500ms

Spec reference: §6 Phase 5 of [specs/2026-05-18-substrate-completion.md](specs/2026-05-18-substrate-completion.md).

## 0.7.0 — 2026-05-18

### Companion skill auto-install (Phase 4 of substrate completion)

The 31-skill companion roster is now a canonical YAML file with a `bstack skills` subcommand that installs, checks, and lists. Closes the install-the-roster gap — previously the bash `ROSTER=(...)` array in `SKILL.md` was *checked* but never *installed*.

- **NEW** `references/companion-skills.yaml` — **canonical companion-skill roster** (31 skills). Per entry: `name`, `repo`, `category`, optional `primitive` (P-id this skill embodies), optional `required` flag, `introduced_in`, `description`. Single source of truth.
- **NEW** `schemas/companion-skills.v1.json` — JSON Schema for the roster YAML.
- **NEW** `bin/bstack-skills` — roster manager subcommand dispatcher:
  - `bstack skills install [--all] [--interactive] [--required-only] [--dry-run]` — installs missing (or all) skills via `npx --yes skills add <repo>`. Idempotent default (skip already-installed). `--dry-run` shows what would happen. `--required-only` installs the `required: true` subset only (fast onboarding path).
  - `bstack skills status [--json]` — text or JSON output of installed/missing/total + per-skill version (reads `~/.agents/skills/<name>/VERSION` or `SKILL.md` frontmatter).
  - `bstack skills list [--json] [--required-only]` — print declared roster without touching filesystem.
- **EDIT** `bin/bstack` — register `skills` subcommand + new help entry under `Observability:`.
- **NEW** `tests/skills-roster.test.sh` — 8 fixture-based tests covering: YAML validation, list count, `--required-only` filter, list `--json` shape, status text + JSON, install `--dry-run` non-invocation, install with mock-npx invocation tracking. Added to vetted CI suite.

### Env overrides

- `BSTACK_SKILLS_YAML` — override roster YAML path (test fixtures)
- `BSTACK_NPX_CMD` — override the `npx --yes skills add` invocation (test mocks)
- `BSTACK_DIR` — override bstack root

### Closes gaps from substrate completion spec §4

- **4.3.1** — 31 companion skills checked but not auto-installed → `bstack skills install` shipped
- **4.4.4** — ROSTER count drift ("27" descriptor vs 31 array entries) → canonical YAML resolves; `SKILL.md` ROSTER stays in place for backward compat (broomva.tech install script depends on it; CI lint catches drift in future phase)

### Out of scope (deferred)

- `references/primitives.md` regen from `primitives.yaml` (Phase 3 deferred to follow-up — same pattern applies here for `SKILL.md` ROSTER ↔ `companion-skills.yaml`)
- `bstack onboard --json <answers>` for pre-filled onboarding (mentioned in Phase 4 spec but separable; will land in 0.7.1 if needed)
- `scripts/bootstrap.sh` calling `bstack skills install --suggest` automatically — current behavior is informational (status + list); auto-install on first-time setup would surprise users. Deferred to Phase 5 doctor-extensions PR which can add a confirm-and-install flow.

### SLO targets (introduced)

- `bstack skills install` (clean machine, 31 skills): p50 < 60s, p99 < 180s (network bound)
- `bstack skills status` (filesystem-only): p50 < 200ms, p99 < 500ms
- `bstack skills list`: p50 < 100ms, p99 < 300ms (YAML parse only)

Spec reference: §6 Phase 4 of [specs/2026-05-18-substrate-completion.md](specs/2026-05-18-substrate-completion.md).

## 0.6.0 — 2026-05-18

### Schema versioning + canonical primitive registry (Phase 3 of substrate completion)

The substrate's declarative surfaces (`.control/policy.yaml`, primitive metadata) are now schema-versioned. Workspaces can be validated against the bstack contract via `bstack doctor` (extended) — and migrations are framework-ready for future v2.

- **NEW** `schemas/setpoint.v1.json` — single setpoint shape (id pattern, target, alert bands, severity, owner, introduced_in).
- **NEW** `schemas/gate.v1.json` — single gate shape (id, rule, pattern, enforcement, severity, profiles, bypass_audit).
- **NEW** `schemas/primitives.v1.json` — primitive registry shape (id, short_name, mechanism, invariant, failure_mode, rule_of_three, skill_repo, introduced_in).
- **NEW** `schemas/policy.v1.json` — full `.control/policy.yaml` shape composing setpoint + gate via `$ref`.
- **NEW** `references/primitives.yaml` — **canonical machine-readable registry of all 20 primitives**. Single source of truth. Validates against `primitives.v1.json`. Each entry carries id, short_name, mechanism.type + .spec, invariant, failure_mode, introduced_in.
- **NEW** `assets/templates/METALAYER.md.template` — bstack-shipped METALAYER template scaffolded by `bstack bootstrap`. Was previously workspace-only; closes Gap 4.1.4 from the substrate completion spec.
- **NEW** `scripts/migrate.sh` — schema migration framework. `v1 → v1` is identity (no-op); future `vN → vN+1` migrations registered via dispatch table. Backs up policy.yaml to `.bak.<epoch>` before any structural change. Respects `--dry-run` and `--apply-all`.
- **CHANGED** `scripts/doctor.sh` — new **Section 10: schema validation**. Validates `references/primitives.yaml` against `primitives.v1.json` and workspace `.control/policy.yaml` against `policy.v1.json` (with `$ref` resolver). Degrades gracefully when `python3` / `jsonschema` / `PyYAML` are absent.
- **NEW** `tests/schema-validation.test.sh` — 8 fixture-based tests: all 4 schemas valid draft-07, primitives.yaml validates, policy.yaml.template validates, invalid setpoint/gate/primitive rejected, migrate.sh v1→v1 no-op, migrate.sh `--dry-run` is non-mutating. Added to vetted CI suite.

### Backward-compat consideration

The current `policy.yaml.template` uses `severity: warn` and (in some downstream workspaces) `severity: soft`. To accept these in v1 without breaking existing workspaces, both schemas list them as v1-era synonyms (canonical: `informational` and `advisory`). Schema v2 will enforce canonical names — a migration in `scripts/migrate.sh` will rename automatically.

### What's NOT included this PR

- `references/primitives.md` auto-generation from `primitives.yaml` — deferred to a follow-up. The YAML is the canonical machine-readable source; the prose `.md` remains hand-written for now. Generator could be added in v0.6.1 if drift becomes an issue.
- `validate-release.yml` schema-check extension — `bstack doctor` Section 10 covers the local check; a CI-side gate is deferred to Phase 5 (doctor extensions in CI lane).

### Closes gaps from the substrate completion spec

- 4.1.4 — `METALAYER.md` workspace-only → now templated
- 4.4.2 — no policy.yaml schema → 4 schemas shipped
- 4.4.3 — no deprecation path for retired primitives → `deprecated_in` / `retired_in` fields added
- 4.6.1 — contracts not formalized as schemas → 4 schemas formalize 4 of the 8 contracts (Setpoint, Gate, Primitive, Plant-via-policy.yaml)

### SLO targets (introduced)

- `bstack doctor` schema validation: p99 < 2s (4 schemas, ~20 primitives, 15 setpoints, 11 gates).
- `scripts/migrate.sh` v1→v1 identity: p99 < 200ms.
- CI schema validation step: p99 < 5s.

Spec reference: §6 Phase 3 of [specs/2026-05-18-substrate-completion.md](specs/2026-05-18-substrate-completion.md).

## 0.5.0 — 2026-05-18

### Substrate status surface (Phase 2 of substrate completion)

The metrics pipeline shipped in v0.4.0 now has a readable face. A single command surfaces substrate health across 8 dimensions — Plant, Setpoints, Gates, Primitives, Companion skills, Bridge, RCS stability, Last upgrade — in colored text for humans or JSON for tooling.

- **NEW** `bin/bstack-status` — health summary dispatcher.
  - `bstack status` — colored text summary (ANSI-aware, TTY-detecting; `--no-color` opt-out).
  - `bstack status --json` — single JSON object: `{bstack_version, workspace, profile, generated_at, setpoints, summary}` with `summary` carrying derived counts (`setpoints_in_target`, `primitives`, `gates_total`, `gate_bypass_attempts_24h`, `rcs_l3_lambda`, `last_upgrade`). Intended for CI / external consumers / status badges.
  - `bstack status --setpoint S<n>` — detailed single-setpoint view (text or `--json`).
  - `bstack status --aggregate` — placeholder for Phase 8 federation; exits 3 with explanation.
  - `--no-collect` — render from cached `~/.bstack/metrics/latest.json` even if stale (default behavior auto-runs `bstack metrics collect` when the cache is missing or > 5 min old).
- **EDIT** `bin/bstack` — register `status` subcommand. New `Observability:` `--help` line.
- **NEW** `tests/status-surface.test.sh` — 8 fixture-based tests covering: all 8 sections render, `--json` shape, `--setpoint` text + JSON modes, unknown-setpoint error, `--aggregate` placeholder, `--no-color` strips ANSI, auto-collect on stale cache. Added to vetted CI suite.

### Composition with v0.4.0

`bstack-status` is a pure reader. It calls `bstack metrics collect` itself when needed so users running `bstack status` cold (e.g. fresh install) still see a populated panel. Cache TTL (5 min for status's own collection trigger; 60s for metrics's own cache) keeps repeated invocations cheap.

### Data sources

| Section | Source |
|---|---|
| Plant | derived from S11 (governance files) + S12 (hooks wired) in `~/.bstack/metrics/latest.json` |
| Setpoints | all setpoints in `latest.json`, classified vs alert thresholds, counted as `in_target/measured` |
| Gates | grep on `.control/policy.yaml` for `^\s+- id: G[0-9]+` |
| Primitives | grep on `CLAUDE.md` (workspace) or `assets/templates/CLAUDE.md.template` for primitive table rows; falls back to 20 |
| Companion skills | S10 in `latest.json` |
| Bridge | S13 in `latest.json` |
| RCS stability | parse `~/<workspace>/research/rcs/data/parameters.toml` for `l3` lambda (gracefully degrades when absent) |
| Last upgrade | `~/.bstack/just-upgraded-from` (if present) + `~/.bstack/last-update-check` cache |

### Bug fix from earlier development (caught in smoke)

The first iteration of `primitive_count()` used `grep -cE PATTERN file || echo 0` which double-emits `"0\n0"` on BSD/macOS grep (where `grep -c` exits 1 on zero matches AND outputs `0`). Replaced with `|| true` + `tr -d` whitespace + default. Same fix applied to `gate_count()` and `gate_bypass_count_24h()`. The regex was also corrected from `^\| \*\*P[0-9]+\*\*` (matches no rows) to `^\| P[0-9]+ \|` (matches actual table format).

### SLO targets (introduced)

- `bstack status` (cached metrics): p50 < 500ms, p99 < 1s
- `bstack status` (cold, auto-collects first): p50 < 2.5s, p99 < 6s
- `bstack status --setpoint <S-id>`: p50 < 100ms, p99 < 300ms (single jq read)

Spec reference: §6 Phase 2 of [specs/2026-05-18-substrate-completion.md](specs/2026-05-18-substrate-completion.md).

## 0.4.0 — 2026-05-18

### Setpoint measurement pipeline (Phase 1 of substrate completion)

The first measurable substrate. Every setpoint declared in `.control/policy.yaml` that the substrate can compute now has a measurement script, and a new top-level CLI surfaces them.

- **NEW** `bin/bstack-metrics` — the measurement dispatcher.
  - `bstack metrics collect [--no-cache] [--json]` — runs every `scripts/metrics/measure-S<n>.sh` discovered in the bstack install, aggregates outputs into `~/.bstack/metrics/latest.json` under `{generated_at, setpoints}`. TTL-cached (default 60s).
  - `bstack metrics observe <S-id>` — single-setpoint JSON to stdout. Useful for `--setpoint` queries in Phase 2's `bstack status`.
  - Per-script timeout: 2s (configurable via `BSTACK_METRICS_TIMEOUT`). Failing scripts produce `{value: null, error: <kind>}` rather than blocking the whole run.
- **NEW** `scripts/metrics/measure-S<n>.sh` — six substrate-measurable setpoints from `.control/policy.yaml`:
  - `S10 bstack_skills_installed` — union count under `~/.agents/skills` + `~/.claude/skills`.
  - `S11 governance_files_present` — checks CLAUDE.md + AGENTS.md + METALAYER.md + .control/policy.yaml + schemas/. Reports missing list.
  - `S12 hooks_wired` — pre-commit + Stop hook + PreToolUse hook. Reports present/missing arrays.
  - `S13 bridge_freshness_seconds` — mtime of `~/.cache/broomva-bridge-stamp`. `null` with `error: stamp-missing` when never run.
  - `S14 conversation_sessions_indexed` — count of `docs/conversations/session-*.md`.
  - `S15 pii_redaction_active` — greps `_redact_pii`/`redact_pii`/`redactPII` in the workspace bridge script. Returns `1` (true) or `0` (false).
- **EDIT** `bin/bstack` — register `metrics` subcommand in the dispatcher. New `Observability:` section in `bstack --help`.
- **NEW** `tests/metrics-pipeline.test.sh` — 7 fixture-based tests covering: aggregate shape, single-setpoint observe, TTL cache honored, --no-cache bypass, per-script JSON validity + id matching, missing-script error, top-level JSON output shape. Added to vetted CI suite.
- **EDIT** `VERSION` → `0.4.0`.

### Output contract

Each `measure-S<n>.sh` emits one line of JSON on stdout matching:

```json
{
  "id": "S<n>",
  "name": "<snake_case_setpoint_name>",
  "value": <number|bool|null>,
  "target": <number|null>,
  "alert_below": <number>,   // OR alert_above
  "severity": "blocking" | "informational",
  "unit": "count" | "seconds" | "bool" | "ratio",
  "... optional fields ..."
}
```

The aggregate `latest.json` wraps these in:

```json
{
  "generated_at": "<iso8601>",
  "setpoints": {
    "S10": { ... },
    "S11": { ... },
    "...": { ... }
  }
}
```

This shape is informal in v0.4.0 and formalized via JSON Schema in v0.6.0 (Phase 3).

### Out of scope (Phase 1)

S1, S2, S4, S5, S6, S7, S8, S9 — measurement requires substrate-external infrastructure (CI history, EGRI runtime, shield instrumentation). They're simply absent from the metrics output until later phases stand up the supporting telemetry. Their presence in `policy.yaml` remains declarative.

### SLO targets (introduced this release)

- `bstack metrics collect` (full, cold): p50 < 2s, p99 < 5s
- `bstack metrics observe <S-id>` (single): p50 < 200ms, p99 < 1s
- Per-script timeout: 2s, fail-soft to structured error

Spec reference: §6 Phase 1 of [specs/2026-05-18-substrate-completion.md](specs/2026-05-18-substrate-completion.md).

## 0.3.1 — 2026-05-18

### Auto-release on merge-to-main

Closes the last manual step in the release workflow. When a PR bumping VERSION merges to main, GitHub Actions now tags `vX.Y.Z` and creates the GitHub Release automatically — using the matching `## X.Y.Z` section of `CHANGELOG.md` as the release body.

- **NEW** `.github/workflows/release.yml` — triggers on `push: branches: [main]` with `paths: [VERSION]`. Reads VERSION, checks if `vX.Y.Z` already exists (idempotent: skips silently if so), extracts the matching CHANGELOG section, creates the annotated tag, pushes it, and runs `gh release create`. The release title is the first `### ` heading inside the section, falling back to the tag. Composes with `validate-release.yml` (PR gate) so this workflow trusts that the merged VERSION is semver, monotonic, and has a CHANGELOG section.
- **CHANGED** `bin/bstack` `release tag` — the clean-tree precondition now only blocks on **modified or staged tracked files**. Untracked files (e.g. workspace-level `.agents/`, `skills-lock.json`, scratch artifacts) no longer prevent the manual helper from running, so it works in a normal development checkout. The error message now lists the offending paths instead of saying "dirty" with no detail.

### Self-validation

This release validates itself: when this PR merges, `release.yml` fires for the first time and creates v0.3.1 automatically — no manual tag or `gh release create` needed.

## 0.3.0 — 2026-05-18

### SessionStart auto-upgrade (push-to-main → live-on-next-session)

bstack now upgrades itself in the background when you start a new Claude Code session, provided you have a git-checkout install and have not opted out. Previously the update check only fired when the user invoked `/bstack` — installs that never invoked the skill stayed pinned forever.

- **NEW** `scripts/bstack-autoupdate-hook.sh` — SessionStart hook. Calls `bstack-update-check` (cached, ≤ 5s curl), and if `UPGRADE_AVAILABLE` is reported, runs `git stash && git fetch && git reset --hard origin/main` in the background so the next Claude session picks up the new release. Writes `~/.bstack/just-upgraded-from` so the preamble's `JUST_UPGRADED` path fires. Log at `~/.bstack/auto-upgrade.log`.
- **CHANGED** `assets/templates/settings.json.snippet` — adds the new SessionStart entry (tagged `_bstack_primitive: "P7"`, timeout 10s, ordered before the existing freshness + role-x hooks). Downstream installs running `bstack repair` (≥ 0.2.3) pick it up idempotently.

### Behavior change: `auto_upgrade` defaults to `true`

This is the silently-different default the bump to 0.3.0 captures. To opt out:

```bash
bstack config set auto_upgrade false   # persistent
# or
BSTACK_AUTO_UPGRADE=0                  # per-session env override
```

### Safety constraints

- **Git installs only.** Vendored installs (no `.git`) get an informational message — no auto-write. The destructive `mv + clone` upgrade path requires user confirmation through `/bstack-upgrade`.
- **`git stash push -u`** preserves any uncommitted local edits to the skill dir. They land in the stash; the auto-upgrade log records the stash message.
- **Backgrounded.** The fetch + reset runs detached so SessionStart's 10s timeout is never the bottleneck. The hook itself only does the cached check + spawn.
- **Cache TTL preserved.** `bstack-update-check` caches `UPGRADE_AVAILABLE` results for 12h, so the hook only does network work at most ~twice/day.

### Migration

Existing installs upgrading to 0.3.0 need the new SessionStart hook wired into their `.claude/settings.json`. Two paths:

1. **Automatic** (recommended) — run `bstack repair` once after upgrading. The merge logic from 0.2.3 picks up the new snippet entry idempotently.
2. **Manual** — run `bstack bootstrap` to re-scaffold from snippet (idempotent, never overwrites).

For installs that *do not want* auto-upgrade as the default, run `bstack config set auto_upgrade false` before upgrading to 0.3.0 (or immediately after — the hook honors the config from its first invocation).

## 0.2.3 — 2026-05-18

### `bstack repair` merges missing hooks (chicken-and-egg fix)

Closes the upgrade gap where a new hook shipped in `assets/templates/settings.json.snippet` would not reach existing installs unless they manually re-ran `bstack bootstrap`. Now `bstack repair` idempotently merges any missing hook entries from the snippet into the user's `.claude/settings.json`. Existing entries are never overwritten or reordered.

- **CHANGED** `scripts/repair.sh` — new `merge_hooks_into_settings` function (Python-driven, mirrors `bootstrap.sh`'s merge logic). Runs unconditionally on every `bstack repair` invocation, *before* doctor's "fully bstack-compliant" early-exit, so a compliant workspace still picks up newly-templated hooks. Silent when everything is in sync. Respects `--dry-run` and `--apply-all`. If `.claude/settings.json` is absent it scaffolds from snippet (replacing the previous "suggest re-running bootstrap" placeholder).
- **NEW** `tests/repair-merge-hooks.test.sh` — 5 fixture-based tests: (1) missing template hook is added, (2) existing user customization preserved, (3) re-running on synced settings is a no-op, (4) absent settings.json is scaffolded, (5) `--dry-run` reports without writing.
- **CHANGED** `.github/workflows/ci.yml` — new `tests` job runs every `tests/*.test.sh` on every PR. Also adds `SC2034` to the shellcheck exclude list (didn't land in 0.2.2's squash due to merge timing; CI would otherwise re-fail on pre-existing unused-vars in `scripts/doctor.sh` and `scripts/statusline-command.sh`).

### Why this is a 0.2.3 (patch) and not 0.3.0

The behavior change is additive — `bstack repair` was already part of the workspace lifecycle. Existing scripted callers (`bstack repair --apply-all` in CI flows) see no breaking change; they just get a more thorough run. The merge function is idempotent, so re-running is safe.

### Unblocks

This is the **prerequisite for 0.3.0** (SessionStart auto-upgrade hook). Without it, the new SessionStart entry shipped in 0.3.0's `settings.json.snippet` wouldn't propagate to existing installs without manual `bstack bootstrap`. With it: `bstack repair` is enough.

## 0.2.2 — 2026-05-18

### Release infrastructure

First formal release with proper OSS tooling. Establishes the foundation that 0.2.3 (`bstack repair` merges hooks) and 0.3.0 (SessionStart auto-upgrade) build on.

- **NEW** `bin/bstack` — top-level CLI dispatcher. Subcommands: `doctor`, `validate`, `repair`, `bootstrap`, `onboard`, `revamp`, `upgrade`, `config`, `update-check`, `wave`, `release tag`, `version`. Existing sub-binaries (`bstack-config`, `bstack-update-check`, `bstack-wave`) remain callable directly — the dispatcher is additive. `bstack release tag` is a maintainer helper that validates the tree, tags `vX.Y.Z`, pushes the tag, and creates the GitHub Release with the matching CHANGELOG section as notes.
- **NEW** `CONTRIBUTING.md` — contribution guide: branch/PR shape, Conventional Commits, primitive-promotion rule, local validation steps.
- **NEW** `RELEASE.md` — semver policy (pre-1.0: minor = potentially breaking), release checklist, retroactive-tag history, cadence guidance, update-check transport docs.
- **NEW** `.github/workflows/ci.yml` — shellcheck on `scripts/*.sh` and `bin/*`, JSON validation for `assets/templates/*.snippet`, `bstack doctor --quiet` on templated fixtures.
- **NEW** `.github/workflows/validate-release.yml` — PR check: if `VERSION` changed, `CHANGELOG.md` must have a matching `## X.Y.Z` section and the version must monotonically increase.
- **CHANGED** `bin/bstack-update-check` — primary source is now the GitHub Releases API (`/repos/broomva/bstack/releases/latest`), with raw `VERSION` on `main` as fallback. **This means dev-branch VERSION bumps no longer leak to downstream installs as "available upgrades"** — only tagged releases do. Two new env vars: `BSTACK_RELEASES_URL` (primary), `BSTACK_REMOTE_URL` (fallback, unchanged behavior).
- **HISTORY** `v0.2.0` and `v0.2.1` tags + GitHub Releases created retroactively on 2026-05-18 to give the update-check transport a stable anchor.

### Migration

None required. Existing installs continue to work — the API-first transport falls back to the raw `VERSION` URL on any failure, so behavior degrades gracefully.

## 0.2.1 — 2026-05-16

### Drop legacy fallback shims from the 0.2.0 renumber

The legacy env vars (`BROOMVA_P8_HOME`, `BROOMVA_P9_JANITOR_HOME`,
`BROOMVA_P8_THRESHOLD_DAYS`) and config-dir fallbacks (`~/.config/broomva/p8/`,
`~/.config/broomva/p9-janitor/`) that 0.2.0 added "for in-place upgrades" are
removed. The canonical paths (`p7/`, `p8-janitor/`) and env vars
(`BROOMVA_P7_HOME`, `BROOMVA_P8_JANITOR_HOME`, `BROOMVA_P7_THRESHOLD_DAYS`) are
the only paths/vars the scripts honor. The legacy-callout prose is stripped
from every governance and substrate document.

**Migration for users upgrading from < 0.2.1**: one-time `mv` of any existing
state into the canonical location:

```bash
[ -d ~/.config/broomva/p8 ] && mv ~/.config/broomva/p8 ~/.config/broomva/p7
[ -d ~/.config/broomva/p9-janitor ] && mv ~/.config/broomva/p9-janitor ~/.config/broomva/p8-janitor
```

After migration: the freshness stamp + janitor `protected.txt` are read from
the canonical paths. No shims in the codebase.

## 0.2.0 — 2026-05-16

### Primitive renumber: Wait moves back to P9 (skill-name↔primitive-number alignment)

The productive-wait primitive (`broomva/p9` skill) is now **P9 (Wait)** again,
matching the skill repo name. Freshness moves to **P7**, Janitor moves to **P8**.

| Before | After |
|---|---|
| P7 = Wait (`broomva/p9` skill — historical name) | P7 = Skill Freshness |
| P8 = Skill Freshness | P8 = Branch + Worktree Janitor |
| P9 = Branch + Worktree Janitor | P9 = Wait (`broomva/p9` skill — name matches number) |

**Rationale**: the interim P8-Freshness / P9-Janitor numbering broke the Name (Pn)
recall key for Wait — the `broomva/p9` skill name no longer matched any primitive
number, producing the exact "numeric soup" failure mode the naming rule (added
in 0.1.x) was designed to prevent. Memory feedback file `feedback_p9_reflexive.md`
became ambiguous (was it about the *skill* or the *primitive number*?). The
2026-05-16 renumber restores alignment so Wait = P9 = `broomva/p9` — primitive
number, primitive name, and skill repo name all agree.

**Migration**: 0.2.0 shipped with legacy fallbacks for in-place upgrades from
the interim numbering. **0.2.1 removes those fallbacks** — see the 0.2.1 entry
above for the one-time `mv` users on the interim numbering should run.

### Naming convention rule propagated to bstack-loaded surfaces

The "use `Name (Pn)` form in agent prose, never bare `Pn`" rule (already in
workspace CLAUDE.md + AGENTS.md) is now restated in `SKILL.md` (naming-convention
subsection after the primitives table) and `references/primitives.md` (top of
file, before TOC). When `/bstack` fires and agents load these surfaces, the rule
is visible at the entry point — closing the gap where agents reverted to bare
`Pn` because the rule was buried only in workspace governance files.

### Doctor extensions

- **New Section 9 — Naming convention propagation**: `scripts/doctor.sh` now lints
  that CLAUDE.md + AGENTS.md contain (a) the `Name (Pn)` naming rule in prose,
  and (b) a **Short-name index** line with exactly 20 entries. Closes the
  silent-drift failure mode where governance edits could leave the index
  mismatched with the primitive count.
- **Primitive-number checks updated**: Section 3 (AGENTS.md primitive sections),
  Section 4 (reflexive trigger rules), Section 6 (hook wiring labels), and
  Section 7 (script paths + labels) all reflect the new canonical ordering.

## 0.1.0

- Initial versioned release with auto-update mechanism
- Added `bin/bstack-update-check` — periodic version check with caching, snooze, and auto-upgrade support
- Added `bin/bstack-config` — read/write `~/.bstack/config.yaml` for persistent preferences
- Added `bstack-upgrade/SKILL.md` — inline upgrade flow with 4 user options
- Preamble now checks for updates before running skill detection
- 27 skills across 7 layers: Foundation, Memory, Orchestration, Research, Design, Platform, Strategy
