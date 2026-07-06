#!/usr/bin/env bash
# bstack/scripts/l3-stability-pretool-hook.sh — Claude Code PreToolUse hook
# for L3 stability (Gate G0).
#
# Fires on Edit/Write/MultiEdit tool calls. Receives the tool input as JSON on
# stdin. Checks if the target file path is in the L3 path list; if so, emits a
# JSON object that surfaces a warning to the agent + logs the event.
#
# Claude Code PreToolUse hook protocol (May 2026):
#   - stdin: JSON with { "tool_name", "tool_input": { "file_path": ..., ... } }
#   - stdout: JSON with { "decision": "approve" | "block", "reason": "..." }
#   - exit 0: hook ran successfully (decision honored)
#   - exit non-zero: hook errored (Claude Code may treat as approve)
#
# Default behavior is to APPROVE the edit (don't block agent work), but with a
# loud reason that gets logged to the agent's context. The block path is
# reserved for cases where the workspace's policy.yaml explicitly disables
# governance edits (a stricter mode).

set -uo pipefail

# Read tool input
INPUT="$(cat 2>/dev/null || echo '{}')"

# Try to extract file_path; jq is preferred but degrade to grep if unavailable
FILE_PATH=""
if command -v jq >/dev/null 2>&1; then
    FILE_PATH=$(echo "$INPUT" | jq -r '.tool_input.file_path // .tool_input.path // ""' 2>/dev/null)
else
    FILE_PATH=$(echo "$INPUT" | grep -oE '"(file_path|path)"[[:space:]]*:[[:space:]]*"[^"]+"' | head -1 | sed -E 's/.*"([^"]+)"[[:space:]]*$/\1/')
fi

# L3 path patterns (kept in sync with rcs-parameters.toml [gates.l3_paths])
L3_PATHS=(
    "CLAUDE.md"
    "AGENTS.md"
    ".control/policy.yaml"
    ".control/rcs-parameters.toml"
    "METALAYER.md"
    # BRO-1707: the leverage governor's own dial + sensors are L3 — an agent must not
    # silently retune the metric that watches it (governor-edits-its-own-setpoints).
    ".control/leverage-setpoints.yaml"
    "scripts/leverage-sensor.py"
    "scripts/leverage-ship-sensor.py"
)

# Check if file_path matches any L3 path (suffix match on basename or full
# relative path; agents pass absolute paths so we suffix-check both forms)
IS_L3=0
MATCHED=""
for pattern in "${L3_PATHS[@]}"; do
    case "$FILE_PATH" in
        *"$pattern"|*"/$pattern")
            IS_L3=1
            MATCHED="$pattern"
            break
            ;;
    esac
done

if [ "$IS_L3" = "0" ]; then
    # Not an L3 file — approve silently
    echo '{"decision":"approve"}'
    exit 0
fi

# Log the event for audit
WORKSPACE="${BROOMVA_WORKSPACE:-$(git rev-parse --show-toplevel 2>/dev/null || echo "$PWD")}"
LOG_DIR="$WORKSPACE/.control/audit"
mkdir -p "$LOG_DIR" 2>/dev/null || true
LOG_FILE="$LOG_DIR/l3-edits.jsonl"
if [ -w "$(dirname "$LOG_FILE")" ] 2>/dev/null; then
    # tool_name must be a bare JSON string VALUE (e.g. "Edit"). The prior grep
    # captured the whole `"tool_name":"Edit"` pair and re-embedded it under %s,
    # producing malformed `"tool_name":"tool_name":"Edit"` (BRO-1707 fix).
    if command -v jq >/dev/null 2>&1; then
        TOOL_NAME=$(echo "$INPUT" | jq -c '.tool_name // "unknown"' 2>/dev/null || echo '"unknown"')
    else
        TOOL_NAME=$(echo "$INPUT" | grep -oE '"tool_name"[[:space:]]*:[[:space:]]*"[^"]+"' | head -1 \
            | sed -E 's/.*:[[:space:]]*("[^"]+")$/\1/')
    fi
    [ -n "$TOOL_NAME" ] || TOOL_NAME='"unknown"'
    printf '{"ts":"%s","file":"%s","matched":"%s","tool_name":%s}\n' \
        "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
        "$FILE_PATH" \
        "$MATCHED" \
        "$TOOL_NAME" \
        >> "$LOG_FILE" 2>/dev/null || true
fi

# Surface warning to the agent (does NOT block; this is informational reflex)
REASON="L3 governance mutation: editing $MATCHED consumes RCS stability budget (lambda_3 ~ 0.006, tau_a_3 = 1 day). Document the reason in your commit body. Run 'bash \$BSTACK_REPO/scripts/compute-lambda.sh --human' after the edit to confirm lambda_3 > 0."

# Emit approve + reason (Claude Code shows the reason to the agent)
cat <<EOF
{
  "decision": "approve",
  "reason": "$REASON"
}
EOF

exit 0
