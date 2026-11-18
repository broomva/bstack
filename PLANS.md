# Verification contracts in bstack

Owner direction: autonomously implement the proposed bstack follow-up to the
Ginkgo-derived skill and Autoany changes. No biological work is part of this arc.

## Observable result

The real cross-review CLI cannot return success for malformed, low-score or
zero-axis pass claims. Findings require falsifiable settling observations.
Bootstrap installs versioned claim-review, measurement and capability contracts
without replacing existing files. Doctor reports missing contracts/validators as
advisory unknowns, with an explicit known-good control for its validator probe.

## Dependency trace

- Upstream: `scripts/cross-review.py` model JSON boundary and existing score rule;
  `schemas/`, `scripts/bootstrap.sh` scaffold/preservation rules;
  `scripts/doctor.sh` advisory/quiet semantics.
- Downstream: `bin/bstack` dispatcher, JSON exit consumers and Markdown reports;
  newly bootstrapped workspaces; schema callers; offline CLI/bootstrap/doctor
  regression tests and `.github/workflows/ci.yml`.
- Companion skill/Autoany PRs remain separate: no runtime dependency on an
  unmerged branch, no writes to their repos, no new primitive or global merge gate.
- Human dependencies: no new credentials needed to implement or test. The correct
  Linear connector previously required reauthentication; keep tracking in this
  plan/PR if unavailable, never create a ticket in another workspace.

## Plan

1. Add versioned schemas plus a shared checker and `bstack verify` CLI. Preserve
   stdlib-only cross-review validation; JSON-schema checks explicitly report
   missing optional jsonschema as inconclusive, never success.
2. Connect model JSON validation, rendering, bootstrap distribution and advisory
   doctor diagnostics. Keep detailed review procedures in companion skills.
3. Exercise real CLI paths with stubbed subprocesses; validate schema/runtime
   agreement and bootstrap idempotency; mutation-prove new gate regressions.
4. Obtain independent read-only review, fix reproduced findings, publish/link PR,
   resolve checks and review threads, and use the policy-gated delivery path.

## Progress

- Baseline: fetched `origin/main` at 12718cf, v0.51.5; isolated clean branch.
  Main checkout is behind with untracked docs and remains untouched.
- Reproduced: unvalidated model pass with score 0 maps to success; findings have
  no required settling observation. Workspace control audit passed.

- Implemented four versioned contracts, stdlib live-review validation, verify CLI,
  non-overwriting bootstrap install and advisory doctor fixture checks.
- Validation: 15 integration tests, eight existing review smoke assertions,
  bootstrap-preservation regression and machine-folder doctor regression pass.
  Fresh-bootstrap canary: 15/15 with an isolated doctor home. Its shared-home
  failure is also present on unchanged main (host ratchet state, not this diff).
- Independent review and mutation checks are in progress; no merge/release yet.
