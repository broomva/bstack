# Release process

bstack ships as a skill installed via `npx skills add broomva/bstack` (vendored) or `git clone` (git install). Every release must be reachable to both install types and properly tagged so `bin/bstack-update-check` and downstream tooling can discover it.

## Versioning policy (Semantic Versioning)

bstack follows [SemVer 2.0](https://semver.org/) with the **pre-1.0 convention** that minor versions may carry breaking behavior changes:

| Pre-1.0 (`0.x.y`) | Meaning |
|---|---|
| `0.x.0` (minor) | New primitives, new hooks wired by default, behavior-changing default flips. **May break existing installs** — document the migration in CHANGELOG. |
| `0.x.y` (patch) | Bug fixes, doc updates, additive non-default features, doctor lint additions. Safe to auto-upgrade. |

Once 1.0.0 ships, the standard SemVer rules apply (major = breaking, minor = additive backwards-compatible, patch = fixes only).

### Examples

| Change | Bump |
|---|---|
| New primitive `P21` added to table + doctor lint | **Minor** — governance change |
| New optional hook in `settings.json.snippet` | **Minor** — installs that re-run `bstack repair` pick it up |
| `bstack-update-check` switches transport (raw VERSION → GitHub releases API) | **Patch** — internal mechanism, observable behavior unchanged |
| Default flip (`auto_upgrade` defaults to true) | **Minor** — silently changes behavior for existing users |
| Typo fix in CLAUDE.md.template | **Patch** |
| Remove legacy fallback shim that 0.x.y added | **Minor** — breaks pinned-to-shim installs even if "internal" |

## Release checklist

Use this checklist for every release. The CI workflow `validate-release.yml` enforces the VERSION ↔ CHANGELOG alignment automatically; the rest is human discipline.

1. **PR opens with**:
   - `VERSION` bumped to the new `X.Y.Z`
   - `CHANGELOG.md` prepended with `## X.Y.Z — YYYY-MM-DD` section
   - Any breaking changes documented under a `### Migration` subheading
2. **Validate locally** — `bash scripts/doctor.sh --quiet`, `shellcheck scripts/*.sh bin/*`, `jq -e . assets/templates/*.snippet`.
3. **CI passes** — `ci.yml` (lint) + `validate-release.yml` (version/changelog match).
4. **Reviewer approves** — at least one human or `pr-review-toolkit:code-reviewer` agent verdict.
5. **Merge to main** (squash).
6. **Tag + GitHub Release** — `bstack release tag` (≥ 0.2.2) wraps the manual sequence:
   ```bash
   git fetch origin && git checkout main && git pull --ff-only
   bstack release tag    # validates clean tree, on main, in sync; tags + pushes + creates Release
   ```
   The dispatcher reads `VERSION`, picks the matching `## X.Y.Z` section out of `CHANGELOG.md` as the release notes, and uses the first `### ` heading inside that section as the release title. If `gh` is not installed the tag is still pushed and the command prints the manual `gh release create` invocation.

   Manual fallback (pre-0.2.2 installs, or if the dispatcher is unavailable):
   ```bash
   VERSION=$(cat VERSION)
   git tag -a "v${VERSION}" -m "v${VERSION} — <title from CHANGELOG>"
   git push origin "v${VERSION}"
   gh release create "v${VERSION}" --title "v${VERSION} — <title>" --notes-file <(awk "/^## ${VERSION}/{flag=1; next} /^## /{flag=0} flag" CHANGELOG.md)
   ```
7. **Downstream verification**:
   - `bin/bstack-update-check --force` from any install should now emit `UPGRADE_AVAILABLE <old> <new>` within the cache TTL window.
   - For git installs with `auto_upgrade=true`, the SessionStart hook (≥ 0.3.0) auto-pulls on next session.

## Cadence

bstack has no fixed release cadence. The triggers for a release are:

- A new primitive earns its rule-of-three and gets promoted → **minor**.
- A behavior-changing default flip (anything that affects installs without their action) → **minor**.
- A bundle of fixes/docs is ready to ship → **patch**.
- A critical bug or security issue → **patch**, immediately.

Avoid letting `main` accumulate more than 2-3 unreleased PRs — each unreleased PR is invisible to downstream installs.

## Backporting

bstack does not maintain release branches. If a fix on `main` is needed urgently on a pinned install, the downstream user pins to a tag and applies the fix locally. There is no `0.2.x` branch to backport to.

## Retroactive tagging (history)

The repo's first tagged release was **v0.2.0** (2026-05-16, commit `322ba23`). Earlier versions (`0.1.0`, etc.) referenced in `CHANGELOG.md` predate the release-infrastructure formalization and are not tagged.

Tags `v0.2.0` and `v0.2.1` were created retroactively on 2026-05-18 as part of the v0.2.2 release-infrastructure work to give `bin/bstack-update-check` a stable anchor to compare against.

## Update check transport

`bin/bstack-update-check` (≥ 0.2.2) compares the local `VERSION` against:

1. **Primary**: GitHub Releases API — `GET /repos/broomva/bstack/releases/latest`, read `.tag_name`, strip leading `v`.
2. **Fallback**: raw `VERSION` file on `main` (`https://raw.githubusercontent.com/broomva/bstack/main/VERSION`) — used when the API is unreachable or rate-limited.

This separation means **development-branch VERSION bumps do not leak as available upgrades to downstream installs** — only tagged releases do. Bump `VERSION` freely on a feature branch; downstream sees nothing until the tag lands.

## Disabling update checks

Downstream users can disable update checks entirely:

```bash
bstack-config set update_check false
```

Or snooze a specific version via the `/bstack-upgrade` interactive flow.

## Questions

See `CONTRIBUTING.md` for the contribution + PR shape. Cadence-or-policy questions belong in repo discussions; mechanical bugs in the release workflow are issues.
