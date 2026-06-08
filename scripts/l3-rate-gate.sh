#!/usr/bin/env bash
# bstack/scripts/l3-rate-gate.sh — Governance commit rate limiter (Gate G1/G2).
#
# Enforces the τ_a₃ assumption from the RCS stability budget: governance-class
# mutations (L3 paths) must not exceed one commit per τ_a₃ window (default
# 86400s = 1 day). Faster churn pushes λ₃ negative and destabilizes the whole
# RCS hierarchy.
#
# Path patterns are read from $WORKSPACE/.control/rcs-parameters.toml under
# [gates.l3_paths]. If the file is missing, falls back to bstack's default:
#   CLAUDE.md, AGENTS.md, .control/policy.yaml,
#   .control/rcs-parameters.toml, METALAYER.md
#
# τ_a₃ is read from the [[levels]] entry with id="L3" (default 86400 if missing).
#
# Usage:
#   bash scripts/l3-rate-gate.sh                 # check current rate
#   bash scripts/l3-rate-gate.sh --staged        # include staged-but-uncommitted (for pre-commit hook)
#   bash scripts/l3-rate-gate.sh --json          # JSON output
#   bash scripts/l3-rate-gate.sh --warn-only     # always exit 0, only print warning
#   bash scripts/l3-rate-gate.sh --window=3600   # override τ_a₃ in seconds
#
# Exit codes:
#   0 — within budget (or --warn-only)
#   1 — budget exceeded (more than 1 L3 commit in τ_a₃ window)
#   2 — parameters config malformed or git not available

set -uo pipefail

WORKSPACE="${BROOMVA_WORKSPACE:-$PWD}"
BSTACK_REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

INCLUDE_STAGED=0
FORMAT="human"
WARN_ONLY=0
WINDOW=""

for arg in "$@"; do
    case "$arg" in
        --staged)        INCLUDE_STAGED=1 ;;
        --json)          FORMAT="json" ;;
        --human)         FORMAT="human" ;;
        --warn-only)     WARN_ONLY=1 ;;
        --window=*)      WINDOW="${arg#*=}" ;;
        --help|-h)
            grep -E '^#( |$)' "$0" | sed 's/^# \?//' | head -28
            exit 0
            ;;
        *)
            echo "l3-rate-gate: unknown flag: $arg" >&2
            exit 2
            ;;
    esac
done

# Locate parameters config (mirrors compute-lambda.sh)
CONFIG=""
if [ -f "$WORKSPACE/.control/rcs-parameters.toml" ]; then
    CONFIG="$WORKSPACE/.control/rcs-parameters.toml"
elif [ -f "$WORKSPACE/research/rcs/data/parameters.toml" ]; then
    CONFIG="$WORKSPACE/research/rcs/data/parameters.toml"
else
    CONFIG="$BSTACK_REPO/assets/templates/rcs-parameters.toml.template"
fi

# Default L3 paths (fallback if config has no [gates.l3_paths])
DEFAULT_L3_PATHS=(
    "CLAUDE.md"
    "AGENTS.md"
    ".control/policy.yaml"
    ".control/rcs-parameters.toml"
    "METALAYER.md"
)

# Read L3 paths + tau_a from config (via Python for robust TOML parsing)
if [ -f "$CONFIG" ] && command -v python3 >/dev/null 2>&1; then
    eval "$(python3 - "$CONFIG" <<'PYEOF'
import sys
try:
    import tomllib
except ImportError:
    sys.exit(0)

try:
    with open(sys.argv[1], "rb") as f:
        data = tomllib.load(f)
except Exception:
    sys.exit(0)

l3_paths = data.get("gates", {}).get("l3_paths", {}).get("patterns", [])
tau_a_l3 = None
for lvl in data.get("levels", []):
    if lvl.get("id") == "L3":
        tau_a_l3 = lvl.get("tau_a")
        break

# Emit bash-eval lines
if l3_paths:
    print("L3_PATHS=(" + " ".join(f'"{p}"' for p in l3_paths) + ")")
if tau_a_l3 is not None:
    print(f"TAU_A_L3={tau_a_l3}")
PYEOF
)" || true
fi

# Apply fallbacks — `${arr[*]:-}` is bash-3.2-safe (macOS default)
if [ -z "${L3_PATHS[*]:-}" ]; then
    L3_PATHS=("${DEFAULT_L3_PATHS[@]}")
fi
TAU_A_L3="${TAU_A_L3:-86400}"
if [ -n "$WINDOW" ]; then
    TAU_A_L3="$WINDOW"
fi

# Cast tau_a to integer seconds (it may be a float in TOML)
TAU_A_L3_INT=$(printf '%.0f' "$TAU_A_L3" 2>/dev/null || echo "86400")

# Check git availability
if ! command -v git >/dev/null 2>&1; then
    echo "l3-rate-gate: git not available" >&2
    exit 2
fi

cd "$WORKSPACE" 2>/dev/null || { echo "l3-rate-gate: cannot cd to $WORKSPACE" >&2; exit 2; }

if ! git rev-parse --git-dir >/dev/null 2>&1; then
    # Not a git repo — nothing to gate on; exit 0 silently
    exit 0
fi

# Compute the cutoff timestamp (now - tau_a_l3)
NOW=$(date +%s)
CUTOFF=$((NOW - TAU_A_L3_INT))

# Count L3-class commits in the window that MODIFIED an L3 file (--diff-filter=M).
# Additions (creation — e.g. the initial `bstack bootstrap` scaffold) are not
# mutations: there is no prior governance state to destabilize, so they do not
# consume the rate budget (BRO-1435). `grep -c .` counts robustly regardless of
# a trailing newline (fixes a latent off-by-one in the prior `wc -l` form).
COUNT_COMMITTED=$(git log --diff-filter=M --since="@$CUTOFF" --format='%H' -- "${L3_PATHS[@]}" 2>/dev/null | grep -c '.' || true)
COUNT_COMMITTED=${COUNT_COMMITTED:-0}

COUNT_STAGED=0
STAGED_FILES=""
if [ "$INCLUDE_STAGED" = "1" ]; then
    # Count a staged L3 file as a mutation ONLY if it already exists at HEAD.
    # Newly-created L3 files (e.g. the initial `bstack bootstrap` scaffold) are
    # creation, not mutation — there is no prior governance state to destabilize,
    # so they are exempt from the rate budget (BRO-1435). If HEAD does not exist
    # yet (first commit ever), every path is a creation → exempt.
    staged_now="$(git diff --cached --name-only 2>/dev/null)"
    for path in "${L3_PATHS[@]}"; do
        if printf '%s\n' "$staged_now" | grep -qFx "$path"; then
            if git cat-file -e "HEAD:$path" 2>/dev/null; then
                COUNT_STAGED=$((COUNT_STAGED + 1))
                STAGED_FILES="$STAGED_FILES $path"
            fi
        fi
    done
fi

# Budget: 1 L3 commit per tau_a_l3 window. Exceeded if (committed + staged) > 1.
TOTAL=$((COUNT_COMMITTED + COUNT_STAGED))
BUDGET=1
EXCEEDED=0
if [ "$TOTAL" -gt "$BUDGET" ]; then
    EXCEEDED=1
fi

# Format output
if [ "$FORMAT" = "json" ]; then
    cat <<EOF
{
  "window_seconds": $TAU_A_L3_INT,
  "cutoff_unix": $CUTOFF,
  "committed_in_window": $COUNT_COMMITTED,
  "staged_l3_files": $COUNT_STAGED,
  "total": $TOTAL,
  "budget": $BUDGET,
  "exceeded": $([ "$EXCEEDED" = "1" ] && echo "true" || echo "false"),
  "l3_paths": [$(printf '"%s",' "${L3_PATHS[@]}" | sed 's/,$//')]
}
EOF
else
    echo "L3 Rate Gate"
    echo "  Window:    ${TAU_A_L3_INT}s ($(( TAU_A_L3_INT / 3600 ))h)"
    echo "  Committed: $COUNT_COMMITTED L3 commit(s) in window"
    [ "$INCLUDE_STAGED" = "1" ] && echo "  Staged:    $COUNT_STAGED L3 file(s)$STAGED_FILES"
    echo "  Total:     $TOTAL / $BUDGET allowed"
    if [ "$EXCEEDED" = "1" ]; then
        echo "  Status:    EXCEEDED — L3 mutation rate violates RCS stability budget"
        echo ""
        echo "  Why this matters:"
        echo "    The RCS stability budget assumes one L3 mutation per tau_a_3 = ${TAU_A_L3_INT}s."
        echo "    Faster churn pushes lambda_3 negative and destabilizes the whole hierarchy."
        echo "    See: bstack/references/primitives.md \"L3 stability constraint\""
        echo ""
        echo "  Recommended:"
        echo "    - Postpone non-urgent governance changes until $(date -r "$((CUTOFF + TAU_A_L3_INT))" 2>/dev/null || echo "the next window")"
        echo "    - Or split the change into multiple PRs across days"
        echo "    - If urgent, bypass with: git commit --no-verify (DOCUMENT WHY in commit body)"
    else
        echo "  Status:    OK — within budget"
    fi
fi

if [ "$EXCEEDED" = "1" ] && [ "$WARN_ONLY" = "0" ]; then
    exit 1
fi

exit 0
