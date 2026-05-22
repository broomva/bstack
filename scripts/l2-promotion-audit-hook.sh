#!/usr/bin/env bash
# bstack/scripts/l2-promotion-audit-hook.sh — L2 (EGRI / meta-control)
# candidate-promotion audit hook.
#
# Called by bookkeeping.py promote step (or any equivalent) with promotion
# metadata. Writes one entry per promotion to .control/audit/l2-promotions.jsonl.
# Also performs the throttle check — if more than N promotions in the last
# τ_a_2 window, prints a warning to stderr and exits 2 (the caller should
# defer remaining promotions, but is not forced to).
#
# Usage:
#   bash scripts/l2-promotion-audit-hook.sh \
#        --slug <entity-slug> \
#        --type <concept|pattern|tool|person|project> \
#        --score <1..9> \
#        --source <raw-extract-path>
#
#   echo '{"slug":"...","type":"...","score":7,"source":"..."}' | \
#        bash scripts/l2-promotion-audit-hook.sh --stdin
#
# Exit codes:
#   0 — within budget; promotion proceeds
#   2 — over throttle; caller SHOULD defer (warning printed to stderr)
#   3 — invalid input or workspace not found

set -uo pipefail

WORKSPACE="${BROOMVA_WORKSPACE:-$(git rev-parse --show-toplevel 2>/dev/null || echo "$PWD")}"
LOG_DIR="$WORKSPACE/.control/audit"
LOG_FILE="$LOG_DIR/l2-promotions.jsonl"

SLUG=""
TYPE=""
SCORE=""
SOURCE=""
STDIN=0

for arg in "$@"; do
    case "$arg" in
        --slug=*)    SLUG="${arg#*=}" ;;
        --type=*)    TYPE="${arg#*=}" ;;
        --score=*)   SCORE="${arg#*=}" ;;
        --source=*)  SOURCE="${arg#*=}" ;;
        --stdin)     STDIN=1 ;;
        --help|-h)
            grep -E '^#( |$)' "$0" | sed 's/^# \?//' | head -25
            exit 0
            ;;
    esac
done

mkdir -p "$LOG_DIR" 2>/dev/null || { echo "l2-promotion-audit: cannot mkdir $LOG_DIR" >&2; exit 3; }

if ! command -v python3 >/dev/null 2>&1; then
    echo "l2-promotion-audit: python3 required" >&2
    exit 3
fi

# If --stdin, read JSON from stdin
if [ "$STDIN" = "1" ]; then
    INPUT_JSON="$(cat 2>/dev/null || echo '{}')"
else
    # Build JSON from flags
    INPUT_JSON=$(python3 -c "
import json, sys
print(json.dumps({
    'slug': '$SLUG',
    'type': '$TYPE',
    'score': '$SCORE',
    'source': '$SOURCE',
}))
")
fi

# Read tau_a_L2 from parameters.toml (default 3600s)
PARAMS=""
if [ -f "$WORKSPACE/.control/rcs-parameters.toml" ]; then
    PARAMS="$WORKSPACE/.control/rcs-parameters.toml"
elif [ -f "$WORKSPACE/research/rcs/data/parameters.toml" ]; then
    PARAMS="$WORKSPACE/research/rcs/data/parameters.toml"
fi

# Throttle budget default — N promotions per window; configurable later
BUDGET="${L2_PROMOTION_BUDGET:-5}"

python3 - "$INPUT_JSON" "$LOG_FILE" "$PARAMS" "$BUDGET" <<'PYEOF'
import sys, json, time, os
from pathlib import Path

raw = sys.argv[1]
log_file = sys.argv[2]
params_path = sys.argv[3]
budget = int(sys.argv[4])

try:
    entry_in = json.loads(raw)
except Exception:
    entry_in = {}

# Determine tau_a_L2
tau_a = 3600.0  # default 1 hour
if params_path and Path(params_path).exists():
    try:
        import tomllib
        with open(params_path, "rb") as f:
            data = tomllib.load(f)
        for lvl in data.get("levels", []):
            if lvl.get("id") == "L2":
                tau_a = float(lvl.get("tau_a", tau_a))
                break
    except Exception:
        pass

now_ms = int(time.time() * 1000)
cutoff_ms = now_ms - int(tau_a * 1000)

# Count promotions in the window
window_count = 0
if Path(log_file).exists():
    with open(log_file) as f:
        for line in f:
            try:
                prev = json.loads(line)
            except Exception:
                continue
            if prev.get("ts", 0) >= cutoff_ms:
                window_count += 1

over_budget = (window_count + 1) > budget

# Write the new entry
entry = {
    "ts": now_ms,
    "slug": entry_in.get("slug", ""),
    "type": entry_in.get("type", ""),
    "score": entry_in.get("score"),
    "source": entry_in.get("source", ""),
    "window_seconds": tau_a,
    "window_count_before": window_count,
    "budget": budget,
    "over_budget": over_budget,
}

with open(log_file, "a") as f:
    f.write(json.dumps(entry, separators=(",", ":")) + "\n")

if over_budget:
    msg = (
        f"l2-promotion-audit: THROTTLE — {window_count + 1}/{budget} promotions "
        f"in the last {int(tau_a)}s. Defer further promotions or increase budget."
    )
    print(msg, file=sys.stderr)
    sys.exit(2)

sys.exit(0)
PYEOF
