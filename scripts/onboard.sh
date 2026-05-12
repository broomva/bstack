#!/usr/bin/env bash
# bstack onboard — first-time setup wizard for the Broomva Stack.
#
# Runs in two modes:
#   - Interactive (default when invoked from a terminal or Claude Code shell):
#     prompts the user through 4 questions via `read -r`.
#   - Flag-driven (--skip-prompts + explicit values): no interaction; useful
#     for agent-driven onboarding via SKILL.md ## Onboarding, automated
#     scripts, or CI bootstrapping.
#
# Either mode collects the same 4 choices, persists them to
# ~/.bstack/config.yaml via `bin/bstack-config set`, runs bootstrap.sh
# with the collected values, and writes the init marker at
# ~/.config/broomva/bstack/initialized so subsequent SessionStart loads
# skip the wizard.
#
# Idempotent: if the marker already exists, onboard.sh exits 0 immediately
# (unless --force is passed). This is the same shape as install.sh's
# "already installed" check.
#
# Usage:
#   bash scripts/onboard.sh                                # interactive
#   bash scripts/onboard.sh --workspace=$HOME/broomva \
#                           --profile=personal \
#                           --life=skip \
#                           --auto-merge=human-required \
#                           --skip-prompts                 # non-interactive
#   bash scripts/onboard.sh --force                        # re-run even if initialized
#   bash scripts/onboard.sh --dry-run                      # print what would happen
#
# Profile values:
#   personal           — relaxed gates, ideal for solo + experimentation
#   enterprise         — strict gates, blocking on G1-G6, audit-friendly
#   autonomous-strict  — gates-are-trust principle fully wired; L3 auto-merge
#                        ENABLED (requires L3 trust gates in CI — see
#                        broomva/workspace G-L3-1..G-L3-5 work)
#
# Auto-merge policy values:
#   human-required     — current default; governance paths block on require_human
#                        until L3 trust gates are wired into GitHub Actions
#   trust-gates        — gates-are-trust principle; L3 paths auto-merge when
#                        gates pass (requires CI workflow to run bstack-l3-trust)

set -euo pipefail

# ─── Paths ────────────────────────────────────────────────────────────────
BSTACK_REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STATE_DIR="${BROOMVA_STATE_DIR:-$HOME/.config/broomva/bstack}"
MARKER_FILE="$STATE_DIR/initialized"
BSTACK_CONFIG_BIN="$BSTACK_REPO/bin/bstack-config"
BOOTSTRAP_SCRIPT="$BSTACK_REPO/scripts/bootstrap.sh"

# ─── Flag parsing ─────────────────────────────────────────────────────────
WORKSPACE=""
PROFILE=""
LIFE=""
AUTO_MERGE=""
SKIP_PROMPTS=0
FORCE=0
DRY_RUN=0

for arg in "$@"; do
    case "$arg" in
        --workspace=*)    WORKSPACE="${arg#*=}" ;;
        --profile=*)      PROFILE="${arg#*=}" ;;
        --life=*)         LIFE="${arg#*=}" ;;
        --auto-merge=*)   AUTO_MERGE="${arg#*=}" ;;
        --skip-prompts)   SKIP_PROMPTS=1 ;;
        --force)          FORCE=1 ;;
        --dry-run)        DRY_RUN=1 ;;
        --help|-h)
            sed -n '/^# Usage:/,/^$/p' "${BASH_SOURCE[0]}" | sed 's/^# \?//'
            exit 0 ;;
        *)
            echo "[onboard] unknown flag: $arg" >&2
            echo "Run: bash scripts/onboard.sh --help" >&2
            exit 2 ;;
    esac
done

# ─── Idempotency check ────────────────────────────────────────────────────
if [ -f "$MARKER_FILE" ] && [ "$FORCE" = "0" ]; then
    echo "[onboard] already initialized at $MARKER_FILE"
    echo "          → re-run with --force to redo onboarding"
    echo "          → or run 'bstack repair' to fix specific gaps"
    exit 0
fi

# ─── Banner ───────────────────────────────────────────────────────────────
echo ""
echo "  ┌──────────────────────────────────────────────────────┐"
echo "  │   bstack onboarding — first-time setup wizard       │"
echo "  │   Sixteen primitives. Twenty-nine skills. One mode. │"
echo "  └──────────────────────────────────────────────────────┘"
echo ""

# ─── Validators ───────────────────────────────────────────────────────────
valid_profile() {
    case "$1" in
        personal|enterprise|autonomous-strict) return 0 ;;
        *) return 1 ;;
    esac
}
valid_life() {
    case "$1" in
        install|skip) return 0 ;;
        *) return 1 ;;
    esac
}
valid_auto_merge() {
    case "$1" in
        human-required|trust-gates) return 0 ;;
        *) return 1 ;;
    esac
}

# ─── Q1: Workspace path ───────────────────────────────────────────────────
DEFAULT_WORKSPACE="$HOME/broomva"
if [ -z "$WORKSPACE" ]; then
    if [ "$SKIP_PROMPTS" = "1" ]; then
        WORKSPACE="$DEFAULT_WORKSPACE"
    else
        echo "  [1/4] Workspace path"
        printf "        Where should bstack scaffold the workspace? [%s] " "$DEFAULT_WORKSPACE"
        read -r WORKSPACE
        WORKSPACE="${WORKSPACE:-$DEFAULT_WORKSPACE}"
    fi
fi
# Expand ~ if user typed it
WORKSPACE="${WORKSPACE/#\~/$HOME}"

# ─── Q2: Profile ──────────────────────────────────────────────────────────
DEFAULT_PROFILE="personal"
if [ -z "$PROFILE" ]; then
    if [ "$SKIP_PROMPTS" = "1" ]; then
        PROFILE="$DEFAULT_PROFILE"
    else
        echo ""
        echo "  [2/4] Profile"
        echo "        personal           — relaxed gates (solo dev, experimentation)"
        echo "        enterprise         — strict gates, audit-friendly"
        echo "        autonomous-strict  — gates-are-trust; L3 auto-merge enabled"
        printf "        Choose [%s]: " "$DEFAULT_PROFILE"
        read -r PROFILE
        PROFILE="${PROFILE:-$DEFAULT_PROFILE}"
    fi
fi
if ! valid_profile "$PROFILE"; then
    echo "[onboard] invalid profile: $PROFILE (must be personal|enterprise|autonomous-strict)" >&2
    exit 2
fi

# ─── Q3: Life Agent OS integration ────────────────────────────────────────
DEFAULT_LIFE="skip"
if [ -z "$LIFE" ]; then
    if [ "$SKIP_PROMPTS" = "1" ]; then
        LIFE="$DEFAULT_LIFE"
    else
        echo ""
        echo "  [3/4] Life Agent OS integration"
        echo "        install            — also installs life-os + arcan CLI binaries"
        echo "        skip               — bstack only; can install later via cargo"
        printf "        Choose [%s]: " "$DEFAULT_LIFE"
        read -r LIFE
        LIFE="${LIFE:-$DEFAULT_LIFE}"
    fi
fi
if ! valid_life "$LIFE"; then
    echo "[onboard] invalid life: $LIFE (must be install|skip)" >&2
    exit 2
fi

# ─── Q4: Auto-merge policy ────────────────────────────────────────────────
DEFAULT_AUTO_MERGE="human-required"
if [ -z "$AUTO_MERGE" ]; then
    if [ "$SKIP_PROMPTS" = "1" ]; then
        AUTO_MERGE="$DEFAULT_AUTO_MERGE"
    else
        echo ""
        echo "  [4/4] Auto-merge policy for governance paths (CLAUDE.md/AGENTS.md/.control/)"
        echo "        human-required     — safe default; humans approve L3 PRs"
        echo "        trust-gates        — gates-are-trust; L3 auto-merges when"
        echo "                             G-L3-1 + G-L3-2 pass (needs CI wiring)"
        printf "        Choose [%s]: " "$DEFAULT_AUTO_MERGE"
        read -r AUTO_MERGE
        AUTO_MERGE="${AUTO_MERGE:-$DEFAULT_AUTO_MERGE}"
    fi
fi
if ! valid_auto_merge "$AUTO_MERGE"; then
    echo "[onboard] invalid auto-merge: $AUTO_MERGE (must be human-required|trust-gates)" >&2
    exit 2
fi

# ─── Receipt ──────────────────────────────────────────────────────────────
echo ""
echo "  ─── Choices ─────────────────────────────────────────"
echo "    workspace:  $WORKSPACE"
echo "    profile:    $PROFILE"
echo "    life:       $LIFE"
echo "    auto-merge: $AUTO_MERGE"
echo "  ─────────────────────────────────────────────────────"
echo ""

if [ "$DRY_RUN" = "1" ]; then
    echo "[onboard] --dry-run set; not writing config or bootstrapping"
    exit 0
fi

# ─── Persist choices to ~/.bstack/config.yaml ─────────────────────────────
if [ -x "$BSTACK_CONFIG_BIN" ]; then
    "$BSTACK_CONFIG_BIN" set workspace "$WORKSPACE"
    "$BSTACK_CONFIG_BIN" set profile "$PROFILE"
    "$BSTACK_CONFIG_BIN" set life "$LIFE"
    "$BSTACK_CONFIG_BIN" set auto_merge "$AUTO_MERGE"
    "$BSTACK_CONFIG_BIN" set onboarded_at "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo "[onboard] choices persisted to \$HOME/.bstack/config.yaml"
else
    echo "[onboard] warn: bstack-config binary not executable at $BSTACK_CONFIG_BIN"
    echo "                skipping config persist; choices used for this run only"
fi

# ─── Run bootstrap with collected choices ─────────────────────────────────
# bootstrap.sh accepts an optional TARGET_DIR arg + reads env vars.
# Bootstrap failures are reported but do NOT block onboarding completion —
# onboarding's job is to persist the user's choices and mark initialization;
# bootstrap fixes (e.g., bash 4+ requirement on macOS) are reported via the
# receipt so the user can re-run `bash scripts/bootstrap.sh` after fixing
# their environment without losing their answered questions.
BOOTSTRAP_STATUS="ok"
BOOTSTRAP_DETAIL=""
if [ -x "$BOOTSTRAP_SCRIPT" ] || [ -f "$BOOTSTRAP_SCRIPT" ]; then
    echo ""
    echo "[onboard] running bootstrap.sh against $WORKSPACE"
    mkdir -p "$WORKSPACE"
    # Disable set -e for the bootstrap subprocess; capture status explicitly.
    set +e
    BSTACK_PROFILE="$PROFILE" \
    BSTACK_LIFE="$LIFE" \
    BSTACK_AUTO_MERGE="$AUTO_MERGE" \
        bash "$BOOTSTRAP_SCRIPT" "$WORKSPACE"
    BOOTSTRAP_EXIT=$?
    set -e
    if [ "$BOOTSTRAP_EXIT" != "0" ]; then
        BOOTSTRAP_STATUS="failed"
        BOOTSTRAP_DETAIL="bootstrap.sh exited $BOOTSTRAP_EXIT"
        echo ""
        echo "[onboard] warn: bootstrap.sh exited $BOOTSTRAP_EXIT — onboarding will mark initialized,"
        echo "                but you'll need to re-run 'bash $BOOTSTRAP_SCRIPT $WORKSPACE' after fixing."
        echo "                Common cause on macOS: stock bash is 3.2; install bash 4+ via"
        echo "                  brew install bash"
    fi
else
    BOOTSTRAP_STATUS="skipped"
    BOOTSTRAP_DETAIL="not found at $BOOTSTRAP_SCRIPT"
    echo "[onboard] warn: bootstrap.sh not found at $BOOTSTRAP_SCRIPT"
fi

# ─── Mark initialized ─────────────────────────────────────────────────────
mkdir -p "$STATE_DIR"
{
    echo "# bstack initialization marker"
    echo "onboarded_at: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo "workspace: $WORKSPACE"
    echo "profile: $PROFILE"
    echo "life: $LIFE"
    echo "auto_merge: $AUTO_MERGE"
    echo "bstack_repo: $BSTACK_REPO"
    echo "bootstrap_status: $BOOTSTRAP_STATUS"
    [ -n "$BOOTSTRAP_DETAIL" ] && echo "bootstrap_detail: $BOOTSTRAP_DETAIL"
} > "$MARKER_FILE"

# ─── Next-step receipt ────────────────────────────────────────────────────
echo ""
echo "  ─── bstack onboarding complete ──────────────────────"
echo ""
echo "  Marker written to: $MARKER_FILE"
echo ""
echo "  Next steps in Claude Code:"
echo "    /bstack             # verify substrate compliance"
echo "    /autonomous         # engage canonical operating mode"
echo ""
echo "  Or in your terminal:"
echo "    cd $WORKSPACE       # cd into your workspace"
echo "    make bstack-check   # run the smoke gate"
echo ""
echo "  Re-run onboarding anytime: bash scripts/onboard.sh --force"
echo "  ─────────────────────────────────────────────────────"
echo ""
