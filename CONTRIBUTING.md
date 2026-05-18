# Contributing to bstack

Thanks for opening a PR. bstack is the substrate that turns an agent-driven workspace into a self-operating system — every change to it propagates to every install. The contribution rules below exist to keep that propagation reliable.

## Branch + PR shape

- **Branch names**: `feat/<slug>`, `fix/<slug>`, `chore/<slug>`, `docs/<slug>`.
- **PR title**: Conventional Commits format (`feat:`, `fix:`, `chore:`, `docs:`, `refactor:`, `test:`). Examples in `git log --oneline`.
- **One concern per PR**. Mixing release infrastructure with a new feature makes both harder to revert.
- **Squash on merge**. Linear history.

## Commit messages

Conventional Commits, body explains the *why*. Existing commits are the reference:

```
feat(primitives): renumber so Wait=P9 — restore skill-name↔primitive-number alignment
fix(SKILL.md): compress description to ≤1024 chars per Agent Skill spec
chore(primitives): drop legacy fallback shims from the 0.2.0 renumber
```

## Local validation (before pushing)

```bash
make bstack-check     # validates skills + hooks + bridge + policy (if you have the workspace harness)
bash scripts/doctor.sh --quiet   # primitive-contract compliance lint
shellcheck scripts/*.sh bin/*    # shell hygiene
jq -e . assets/templates/*.snippet >/dev/null   # template JSON shape
```

CI runs the same checks via `.github/workflows/ci.yml`.

## Adding a new primitive (P21+)

bstack's L3 stability budget says governance changes are rare and deliberate. Before adding a new `Pn`:

1. Confirm rule-of-three: ≥ 3 independent instances of the failure mode the new primitive closes, each documented in `research/notes/` or an entity page.
2. The pattern must have: concrete mechanism, stated invariant, stated failure mode it prevents.
3. Add the row to `SKILL.md` §Primitives table.
4. Add the section to `assets/templates/AGENTS.md.template` §Primitives.
5. Update `references/primitives.md` Short-name index (must equal the new total count).
6. Update `scripts/doctor.sh` to lint the new row.
7. Bump VERSION minor and add a CHANGELOG entry — primitive additions are minor releases pre-1.0.

## Adding a skill to the roster

`SKILL.md` preamble has a `ROSTER=(...)` array of expected skill names. Add yours there. The skill itself lives in its own `broomva/<name>` repo; bstack tracks installation status, not source.

## Release

See `RELEASE.md`. Short version:

1. Bump `VERSION`.
2. Prepend a section to `CHANGELOG.md` matching the new version.
3. `validate-release.yml` confirms the two are aligned on the PR.
4. After merge, tag and create the GitHub Release (`gh release create vX.Y.Z`).

## Style

- **Shell**: `set -euo pipefail`, quote variables, `shellcheck`-clean.
- **Python**: PEP 8, type hints where useful, no global state.
- **Markdown**: agent-readable surfaces (SKILL.md, AGENTS.md, primitives.md) stay terse and structural. Human-readable docs (RELEASE.md, CONTRIBUTING.md) can be longer.

## Questions

Open a discussion in the repo or ping in the workspace channel where bstack is being used. PRs without context get bounced — paste the failure mode, the proposed fix, and the validation you ran.
