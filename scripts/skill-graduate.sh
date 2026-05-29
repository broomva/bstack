#!/usr/bin/env bash
# skill-graduate.sh — crystallized Tier-2 skill-graduation pattern (v0.21.7).
#
# Migrates a standalone `broomva/<name>` skill repo into the `broomva/skills`
# Tier-2 monorepo, following the Phase 2-4f migration pattern that ran 8 times
# manually on 2026-05-25/26 (strategy bundle + content + research + finance +
# specialty + neuroscience + orcahand dedup). This script is the crystallization
# of that repeated pattern (P16 — rule-of-three exceeded ~8x over).
#
# Invoked as: `bstack skills graduate <name> [options]`
#
# The pattern, automated:
#   1. Clone source repo + monorepo into temp worktrees
#   2. Copy canonical content into monorepo skills/<target>/ — EXCLUDING
#      .git, dot-prefixed IDE-mirror dirs, LICENSE, skills-lock.json
#   3. Commit + push branch + open PR on the monorepo
#   4. (--stub)     add redirect-stub README to the source repo + open PR
#   5. (--merge)    merge the opened PR(s)  [default: leave open for review]
#   6. Cleanup temp clones (always, via trap)
#
# NOT automated (printed for manual follow-up, since both need human judgment):
#   - The monorepo README Tier-2 table row — category placement varies; the
#     script prints a ready-to-paste row instead of guessing the section.
#   - The bstack registry (companion-skills.yaml + skills-roster.md + VERSION
#     + CHANGELOG) — needs a coordinated version bump a human/agent reviews.
#   Both are printed copy-paste-ready in the closing "NEXT" block.
#
# Env overrides (test fixtures use these to avoid network):
#   BSTACK_GRADUATE_GH        gh command (default: gh)
#   BSTACK_GRADUATE_GIT       git command (default: git)
#   BSTACK_GRADUATE_TMPDIR    temp root (default: mktemp -d)
#   BSTACK_GRADUATE_DRY_RUN   force dry-run (default: 0)
set -euo pipefail

GH="${BSTACK_GRADUATE_GH:-gh}"
GIT="${BSTACK_GRADUATE_GIT:-git}"

usage() {
    cat <<'EOF'
bstack skills graduate — migrate a standalone skill repo into broomva/skills monorepo

Usage:
  bstack skills graduate <name> [options]

Arguments:
  <name>                  Source skill name (the broomva/<name> repo)

Options:
  --target <name>         Rename during migration (e.g. drop -skill suffix).
                          Default: same as <name>.
  --source-repo <o/r>     Source GitHub repo. Default: broomva/<name>.
  --monorepo <o/r>        Destination monorepo. Default: broomva/skills.
  --category <cat>        Registry category (for the printed registry entry).
                          One of: lifecycle knowledge orchestration safety meta
                          design platform strategy content observability.
  --description <text>    One-line description (README cell + registry entry).
  --stub                  Add redirect-stub README to the source repo (default ON).
  --no-stub               Skip the source redirect-stub.
  --merge                 Merge opened PR(s) after opening (default: leave open).
  --exclude <glob>        Extra exclude pattern (repeatable). Defaults always
                          exclude: .git, .*  (dot dirs), LICENSE, skills-lock.json.
  --dry-run               Print the plan; make no clones, commits, or PRs.
  -h | --help             This message.

Examples:
  bstack skills graduate handoff --category lifecycle \
    --description "Fresh-session handoff doc drafting"

  bstack skills graduate omnivoice-skill --target omnivoice --category content \
    --description "OmniVoice Studio — TTS, voice cloning, dubbing in 646 languages"

  bstack skills graduate pre-mortem --category strategy --no-stub --dry-run
EOF
}

# ---- defaults ----
NAME=""
TARGET=""
SOURCE_REPO=""
MONOREPO="broomva/skills"
CATEGORY=""
DESCRIPTION=""
DO_STUB=1
DO_MERGE=0
DRY_RUN="${BSTACK_GRADUATE_DRY_RUN:-0}"
EXTRA_EXCLUDES=()

# ---- arg parsing ----
if [ $# -eq 0 ]; then usage >&2; exit 2; fi
while [ $# -gt 0 ]; do
    case "$1" in
        --target)       TARGET="${2:?--target needs a value}"; shift 2 ;;
        --source-repo)  SOURCE_REPO="${2:?--source-repo needs a value}"; shift 2 ;;
        --monorepo)     MONOREPO="${2:?--monorepo needs a value}"; shift 2 ;;
        --category)     CATEGORY="${2:?--category needs a value}"; shift 2 ;;
        --description)  DESCRIPTION="${2:?--description needs a value}"; shift 2 ;;
        --stub)         DO_STUB=1; shift ;;
        --no-stub)      DO_STUB=0; shift ;;
        --merge)        DO_MERGE=1; shift ;;
        --exclude)      EXTRA_EXCLUDES+=("${2:?--exclude needs a value}"); shift 2 ;;
        --dry-run)      DRY_RUN=1; shift ;;
        -h|--help|help) usage; exit 0 ;;
        -*)             echo "skill-graduate: unknown option: $1" >&2; usage >&2; exit 2 ;;
        *)
            if [ -z "$NAME" ]; then NAME="$1"; shift
            else echo "skill-graduate: unexpected argument: $1" >&2; exit 2; fi ;;
    esac
done

[ -n "$NAME" ] || { echo "skill-graduate: <name> is required" >&2; usage >&2; exit 2; }
TARGET="${TARGET:-$NAME}"
SOURCE_REPO="${SOURCE_REPO:-broomva/$NAME}"

# Validate target name against agentskills.io spec (lowercase, hyphens, <=64).
if ! printf '%s' "$TARGET" | grep -qE '^[a-z][a-z0-9-]{0,63}$'; then
    echo "skill-graduate: invalid target name '$TARGET' (must match ^[a-z][a-z0-9-]{0,63}\$)" >&2
    exit 2
fi

# Determine if this is a rename.
RENAME_NOTE=""
[ "$NAME" != "$TARGET" ] && RENAME_NOTE=" (renamed: $NAME -> $TARGET)"

echo "skill-graduate plan:"
echo "  source repo   : $SOURCE_REPO"
echo "  monorepo      : $MONOREPO"
echo "  skill path    : skills/$TARGET/$RENAME_NOTE"
echo "  category      : ${CATEGORY:-<unset — registry entry will note TODO>}"
echo "  redirect-stub : $([ "$DO_STUB" = 1 ] && echo yes || echo no)"
echo "  auto-merge    : $([ "$DO_MERGE" = 1 ] && echo yes || echo no)"
echo "  excludes      : .git .* LICENSE skills-lock.json ${EXTRA_EXCLUDES[*]:-}"
echo ""

if [ "$DRY_RUN" = 1 ]; then
    echo "[dry-run] No clones, commits, or PRs will be made."
    echo "[dry-run] Registry entry to add to broomva/bstack references/companion-skills.yaml:"
    cat <<EOF

  - name: $TARGET
    repo: $MONOREPO
    skillPath: skills/$TARGET/SKILL.md
    category: ${CATEGORY:-TODO}
    required: false
    introduced_in: TODO
    min_bstack_version: 0.21.0
    description: "${DESCRIPTION:-TODO}"
EOF
    exit 0
fi

# ---- real execution ----
TMP="${BSTACK_GRADUATE_TMPDIR:-$(mktemp -d)}"
# BSTACK_GRADUATE_NO_CLEANUP=1 leaves TMP in place (test fixtures inspect the
# copied tree). Default: always clean up.
cleanup() {
    [ "${BSTACK_GRADUATE_NO_CLEANUP:-0}" = 1 ] && return 0
    [ -n "${TMP:-}" ] && [ -d "$TMP" ] && rm -rf "$TMP"
}
trap cleanup EXIT

MONO_DIR="$TMP/monorepo"
SRC_DIR="$TMP/source"
BRANCH="feat/graduate-$TARGET"

# Idempotency pre-flight: bail if a PR for this branch already exists, rather
# than re-pushing + failing opaquely inside the subshell on the 2nd run.
if $GH pr list --repo "$MONOREPO" --head "$BRANCH" --state open --json number --jq '.[0].number' 2>/dev/null | grep -q '[0-9]'; then
    echo "skill-graduate: an open PR for branch '$BRANCH' already exists on $MONOREPO." >&2
    echo "  Close/merge it first, or pass --target to use a different skill name." >&2
    exit 1
fi

echo "==> cloning $MONOREPO + $SOURCE_REPO"
$GH repo clone "$MONOREPO" "$MONO_DIR" -- --depth=10 >/dev/null 2>&1
$GH repo clone "$SOURCE_REPO" "$SRC_DIR" -- --depth=10 >/dev/null 2>&1

echo "==> copying canonical content into skills/$TARGET/"
mkdir -p "$MONO_DIR/skills/$TARGET"
# Build exclude test. Always exclude dot-entries, LICENSE, skills-lock.json.
should_exclude() {
    local item="$1"
    case "$item" in
        .*|LICENSE|skills-lock.json) return 0 ;;
    esac
    local ex
    for ex in "${EXTRA_EXCLUDES[@]:-}"; do
        # SC2053 intentional: $ex is a glob pattern from EXTRA_EXCLUDES; quoting
        # the RHS would defeat the pattern match this exclude check depends on.
        # shellcheck disable=SC2053
        [ -n "$ex" ] && [[ "$item" == $ex ]] && return 0
    done
    return 1
}
copied=0
# Null-delimited iteration: robust against filenames with spaces, globs,
# or newlines. `ls`-based word-splitting (the obvious naive loop) breaks on
# spaced filenames under `set -e` mid-copy — regression-tested in
# tests/skill-graduate.test.sh T10.
while IFS= read -r -d '' path; do
    item="$(basename "$path")"
    if should_exclude "$item"; then continue; fi
    cp -R "$path" "$MONO_DIR/skills/$TARGET/"
    copied=$((copied + 1))
done < <(find "$SRC_DIR" -mindepth 1 -maxdepth 1 -print0)
echo "    copied $copied top-level items ($(find "$MONO_DIR/skills/$TARGET" -type f | wc -l | tr -d ' ') files total)"

# Sanity: a SKILL.md must exist after copy.
if [ ! -f "$MONO_DIR/skills/$TARGET/SKILL.md" ]; then
    echo "skill-graduate: ERROR — no SKILL.md found in skills/$TARGET/ after copy." >&2
    echo "  Source $SOURCE_REPO may keep its SKILL.md under a subdir; migrate manually." >&2
    exit 1
fi

echo "==> committing + opening PR on $MONOREPO"
(
    cd "$MONO_DIR"
    $GIT checkout -b "$BRANCH" >/dev/null 2>&1
    $GIT add "skills/$TARGET" >/dev/null 2>&1
    $GIT commit -q -m "feat(monorepo): graduate $TARGET to Tier-2$RENAME_NOTE

Migrated from $SOURCE_REPO via \`bstack skills graduate\`.
Install: npx skills add $MONOREPO --skill $TARGET"
    $GIT push -u origin "$BRANCH" >/dev/null 2>&1
    $GH pr create --base main --head "$BRANCH" \
        --title "feat(monorepo): graduate $TARGET to Tier-2$RENAME_NOTE" \
        --body "Graduated from \`$SOURCE_REPO\` via \`bstack skills graduate\`. Install: \`npx skills add $MONOREPO --skill $TARGET\`.${DESCRIPTION:+

$DESCRIPTION}" >/dev/null 2>&1
    if [ "$DO_MERGE" = 1 ]; then
        # Try delete-branch first; fall back to plain squash if branch
        # deletion is blocked (protected). A genuine merge failure (red CI,
        # conflicts) is surfaced, not swallowed.
        if ! $GH pr merge "$BRANCH" --squash --delete-branch >/dev/null 2>&1 \
           && ! $GH pr merge "$BRANCH" --squash >/dev/null 2>&1; then
            echo "    WARNING: auto-merge of monorepo PR failed (CI red, conflicts, or gate) — leaving PR open for manual review." >&2
        fi
    fi
)
MONO_PR=$($GH pr list --repo "$MONOREPO" --head "$BRANCH" --state all --json url --jq '.[0].url' 2>/dev/null || echo "(see $MONOREPO PRs)")
echo "    monorepo PR: $MONO_PR"

if [ "$DO_STUB" = 1 ]; then
    echo "==> adding redirect-stub on $SOURCE_REPO"
    (
        cd "$SRC_DIR"
        cat > README.md <<STUBEOF
# $NAME (DEPRECATED — migrated to $MONOREPO monorepo)

> **Status:** migrated to the [$MONOREPO](https://github.com/$MONOREPO) monorepo as a Tier-2 vendored skill$RENAME_NOTE. 6-month deprecation window before archival.

## New install command

\`\`\`bash
npx skills add $MONOREPO --skill $TARGET
\`\`\`

## Skill home

[$MONOREPO/skills/$TARGET](https://github.com/$MONOREPO/tree/main/skills/$TARGET)

## License

[MIT](LICENSE) — unchanged.
STUBEOF
        $GIT checkout -b chore/deprecate-redirect >/dev/null 2>&1
        $GIT add README.md >/dev/null 2>&1
        $GIT commit -q -m "chore(deprecate): redirect to $MONOREPO monorepo

Migrated to $MONOREPO/skills/$TARGET via \`bstack skills graduate\`."
        $GIT push -u origin chore/deprecate-redirect >/dev/null 2>&1
        $GH pr create --base main --head chore/deprecate-redirect \
            --title "chore(deprecate): redirect to $MONOREPO monorepo" \
            --body "Migrated to \`$MONOREPO/skills/$TARGET\` via \`bstack skills graduate\`. Install: \`npx skills add $MONOREPO --skill $TARGET\`." >/dev/null 2>&1
        if [ "$DO_MERGE" = 1 ]; then
            if ! $GH pr merge chore/deprecate-redirect --squash --delete-branch >/dev/null 2>&1 \
               && ! $GH pr merge chore/deprecate-redirect --squash >/dev/null 2>&1; then
                echo "    WARNING: auto-merge of source redirect-stub PR failed — leaving it open." >&2
            fi
        fi
    )
    STUB_PR=$($GH pr list --repo "$SOURCE_REPO" --head chore/deprecate-redirect --state all --json url --jq '.[0].url' 2>/dev/null || echo "(see $SOURCE_REPO PRs)")
    echo "    stub PR: $STUB_PR"
fi

echo ""
echo "==> NEXT: add this entry to broomva/bstack references/companion-skills.yaml + bump VERSION + CHANGELOG:"
cat <<EOF

  - name: $TARGET
    repo: $MONOREPO
    skillPath: skills/$TARGET/SKILL.md
    category: ${CATEGORY:-TODO}
    required: false
    introduced_in: <next-bstack-version>
    min_bstack_version: 0.21.0
    description: "${DESCRIPTION:-TODO}"
EOF
echo ""
echo "skill-graduate: done. $([ "$DO_MERGE" = 1 ] && echo 'PRs merged.' || echo 'PRs opened (review + merge when ready).')"
