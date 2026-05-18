# Changelog

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
