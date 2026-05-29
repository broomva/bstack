#!/usr/bin/env bash
# bstack/scripts/install-l3-stability.sh — Deploy the L3 stability gate flow
# (G0 Claude Code hook + G1 pre-commit + G2 GitHub Actions + parameters.toml)
# into a workspace.
#
# Called by:
#   - bstack/scripts/onboard.sh       (first-time setup)
#   - bstack/scripts/repair.sh        (re-install if missing)
#   - directly                        (manual install)
#
# Idempotent — safe to re-run. Files that already exist are NOT overwritten by
# default; pass --force to overwrite. The settings.json merge is always
# idempotent (skips if the L3-G0 hook entry is already present).
#
# Usage:
#   bash scripts/install-l3-stability.sh                       # default
#   bash scripts/install-l3-stability.sh --workspace=$HOME/foo # explicit
#   bash scripts/install-l3-stability.sh --force               # overwrite existing
#   bash scripts/install-l3-stability.sh --dry-run             # show what would happen

set -uo pipefail

WORKSPACE="${BROOMVA_WORKSPACE:-$PWD}"
FORCE=0
DRY_RUN=0

for arg in "$@"; do
    case "$arg" in
        --workspace=*) WORKSPACE="${arg#*=}" ;;
        --force)       FORCE=1 ;;
        --dry-run)     DRY_RUN=1 ;;
        --help|-h)
            grep -E '^#( |$)' "$0" | sed 's/^# \?//' | head -22
            exit 0
            ;;
        *)
            echo "install-l3-stability: unknown flag: $arg" >&2
            exit 2
            ;;
    esac
done

BSTACK_REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [ ! -d "$WORKSPACE" ]; then
    echo "install-l3-stability: workspace not found: $WORKSPACE" >&2
    exit 2
fi

echo "install-l3-stability: target = $WORKSPACE"
echo "install-l3-stability: bstack = $BSTACK_REPO"
echo ""

# Helper: write file or report (dry-run vs real)
INSTALLED=0
SKIPPED=0
do_install() {
    local src="$1"
    local dst="$2"
    local mode="${3:-644}"

    if [ -e "$dst" ] && [ "$FORCE" = "0" ]; then
        echo "  [skip] $dst (already exists; --force to overwrite)"
        SKIPPED=$((SKIPPED + 1))
        return
    fi
    if [ "$DRY_RUN" = "1" ]; then
        echo "  [dry] would write $dst (mode $mode) from $src"
        return
    fi
    mkdir -p "$(dirname "$dst")"
    cp "$src" "$dst"
    chmod "$mode" "$dst"
    echo "  [ok]   $dst"
    INSTALLED=$((INSTALLED + 1))
}

# ── 1. Deploy parameters.toml ───────────────────────────────────────────────
echo "1. Parameters (.control/rcs-parameters.toml)"
do_install "$BSTACK_REPO/assets/templates/rcs-parameters.toml.template" \
           "$WORKSPACE/.control/rcs-parameters.toml" \
           "644"

# ── 2. Deploy git pre-commit hook ───────────────────────────────────────────
echo "2. Git pre-commit hook (.githooks/pre-commit)"
PRE_COMMIT="$WORKSPACE/.githooks/pre-commit"
if [ -f "$PRE_COMMIT" ] && grep -q "L3 rate gate" "$PRE_COMMIT" 2>/dev/null; then
    # Already our hook — idempotent no-op (even under --force; reinstalling
    # identical content is pointless).
    echo "  [skip] .githooks/pre-commit (already bstack L3 hook)"
    SKIPPED=$((SKIPPED + 1))
elif [ -f "$PRE_COMMIT" ] \
     && (cd "$WORKSPACE" && git ls-files --error-unmatch .githooks/pre-commit >/dev/null 2>&1) \
     && [ "$FORCE" = "0" ]; then
    # TRACKED hook + no --force → NEVER clobber. The committed pre-commit is
    # authoritative; overwriting it destroys a tracked file. Skip + warn.
    # (Bug found dogfooding on a repo with a tracked .githooks/pre-commit: the
    # repo's own hook got replaced even though core.hooksPath ≠ .githooks, so
    # the L3 hook never fired.) --force routes to the preserve-then-install
    # branch below, which DOES create the .pre-commit.local sidecar.
    echo "  [skip] .githooks/pre-commit is git-tracked — preserving the repo's committed hook"
    echo "         → to add the L3 rate gate: chain it into the existing hook by hand,"
    echo "           or re-run with --force (which preserves the current hook as .pre-commit.local)."
    SKIPPED=$((SKIPPED + 1))
elif [ -f "$PRE_COMMIT" ]; then
    # An existing hook we are about to replace: either an UNTRACKED local hook,
    # or a tracked hook under --force. Preserve it as .pre-commit.local first,
    # then install ours — so the sidecar recovery path the warning promises
    # actually exists in both cases.
    echo "  [info] existing .githooks/pre-commit found — preserving as .pre-commit.local"
    if [ "$DRY_RUN" = "0" ]; then
        mv "$PRE_COMMIT" "$WORKSPACE/.githooks/pre-commit.local"
        chmod +x "$WORKSPACE/.githooks/pre-commit.local"
    fi
    do_install "$BSTACK_REPO/assets/templates/githook-pre-commit-l3-rate.sh.template" \
               "$PRE_COMMIT" "755"
else
    do_install "$BSTACK_REPO/assets/templates/githook-pre-commit-l3-rate.sh.template" \
               "$PRE_COMMIT" "755"
fi

# Configure git to use .githooks/ if not already
if [ "$DRY_RUN" = "0" ] && [ -d "$WORKSPACE/.git" ]; then
    cur_hooks_path=$(cd "$WORKSPACE" && git config --local --get core.hooksPath 2>/dev/null || true)
    if [ -z "$cur_hooks_path" ]; then
        if [ "$FORCE" = "1" ] || [ ! -f "$WORKSPACE/.git/hooks/pre-commit" ]; then
            (cd "$WORKSPACE" && git config --local core.hooksPath .githooks)
            echo "  [ok]   git config core.hooksPath .githooks"
        fi
    elif [ "$cur_hooks_path" = ".githooks" ]; then
        echo "  [ok]   git core.hooksPath = .githooks (already set)"
    else
        echo "  [warn] git core.hooksPath = $cur_hooks_path (not .githooks) — manually merge bstack hooks"
    fi
fi

# ── 3. Deploy GitHub Actions workflow ───────────────────────────────────────
echo "3. GitHub Actions workflow (.github/workflows/l3-stability.yml)"
do_install "$BSTACK_REPO/assets/templates/gh-workflow-l3-stability.yml.template" \
           "$WORKSPACE/.github/workflows/l3-stability.yml" \
           "644"

# ── 4. Merge Claude Code PreToolUse hook into .claude/settings.json ─────────
echo "4. Claude Code PreToolUse hook (.claude/settings.json)"
SETTINGS="$WORKSPACE/.claude/settings.json"
SNIPPET="$BSTACK_REPO/assets/templates/settings.json.l3-stability-hook.snippet"

if [ "$DRY_RUN" = "1" ]; then
    echo "  [dry] would merge L3-G0 PreToolUse hook into $SETTINGS"
elif ! command -v python3 >/dev/null 2>&1; then
    echo "  [skip] python3 not available; cannot merge JSON safely"
    SKIPPED=$((SKIPPED + 1))
else
    mkdir -p "$(dirname "$SETTINGS")"
    python3 - "$SETTINGS" "$SNIPPET" "$BSTACK_REPO" <<'PYEOF' && INSTALLED=$((INSTALLED + 1)) || true
import sys, json, os
from pathlib import Path

settings_path = Path(sys.argv[1])
snippet_path = Path(sys.argv[2])
bstack_repo = sys.argv[3]

# Load existing settings (or start fresh)
if settings_path.exists():
    try:
        with settings_path.open() as f:
            settings = json.load(f)
    except json.JSONDecodeError:
        print(f"  [skip] {settings_path} is not valid JSON; leaving alone")
        sys.exit(1)
else:
    settings = {}

# Load snippet
with snippet_path.open() as f:
    snippet = json.load(f)

settings.setdefault("hooks", {})
pre_tool = settings["hooks"].setdefault("PreToolUse", [])

# Idempotent: skip if any existing entry has _bstack_primitive == "L3-G0"
already = any(
    any(h.get("_bstack_primitive") == "L3-G0" for h in entry.get("hooks", []))
    for entry in pre_tool
)

if already:
    print(f"  [skip] .claude/settings.json (L3-G0 PreToolUse hook already present)")
    sys.exit(0)

# Substitute $BSTACK_REPO in the snippet's command paths
def substitute(o):
    if isinstance(o, dict):
        return {k: substitute(v) for k, v in o.items()}
    if isinstance(o, list):
        return [substitute(x) for x in o]
    if isinstance(o, str):
        return o.replace("$BSTACK_REPO", bstack_repo)
    return o

snippet = substitute(snippet)
pre_tool.extend(snippet["hooks"]["PreToolUse"])

with settings_path.open("w") as f:
    json.dump(settings, f, indent=2)

print(f"  [ok]   {settings_path} (L3-G0 PreToolUse hook merged)")
PYEOF
fi

# ── Summary ─────────────────────────────────────────────────────────────────
echo ""
echo "install-l3-stability: $INSTALLED installed, $SKIPPED skipped"
if [ "$DRY_RUN" = "1" ]; then
    echo "                      (dry-run; no files written)"
fi
echo ""
echo "Verify with: bash $BSTACK_REPO/scripts/doctor.sh   (look for sections 14 + 15)"
echo "Inspect rate gate manually: bash $BSTACK_REPO/scripts/l3-rate-gate.sh"
echo "Inspect lambda: bash $BSTACK_REPO/scripts/compute-lambda.sh --human"

exit 0
