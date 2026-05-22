#!/usr/bin/env bash
# bstack/scripts/l0-tool-audit-hook.sh — Claude Code PostToolUse hook for L0
# (plant) stability monitoring.
#
# Receives the tool-call result JSON on stdin from Claude Code. Logs the
# tool name + latency + exit indicator to .control/audit/l0-tools.jsonl in
# the workspace. Always exits 0 (hooks must not block tool flow).
#
# Claude Code PostToolUse hook protocol (May 2026):
#   stdin: { "tool_name", "tool_input": {...}, "tool_result": {...} }
#   stdout: optional JSON ({"decision":"approve"} default — PostToolUse can't block)
#   exit 0 = hook ran; non-zero = hook errored (treated as approve)
#
# The hook is intentionally minimal — writes one line and exits. Heavy
# processing happens in compute-budget-status.sh when doctor §16 reads
# the log.

set -uo pipefail

WORKSPACE="${BROOMVA_WORKSPACE:-$(git rev-parse --show-toplevel 2>/dev/null || echo "$PWD")}"
LOG_DIR="$WORKSPACE/.control/audit"
LOG_FILE="$LOG_DIR/l0-tools.jsonl"

mkdir -p "$LOG_DIR" 2>/dev/null || { echo '{"decision":"approve"}'; exit 0; }

INPUT="$(cat 2>/dev/null || echo '{}')"

# Extract tool_name + exit indicator via inline Python (robust JSON parsing)
if command -v python3 >/dev/null 2>&1; then
    LINE=$(python3 - "$INPUT" <<'PYEOF'
import sys, json, time
raw = sys.argv[1] if len(sys.argv) > 1 else "{}"
try:
    data = json.loads(raw)
except Exception:
    data = {}

tool_name = data.get("tool_name", "unknown")
tool_input = data.get("tool_input", {}) if isinstance(data.get("tool_input"), dict) else {}
tool_result = data.get("tool_result", {}) if isinstance(data.get("tool_result"), dict) else {}

# Best-effort latency: Claude Code may provide latency_ms; otherwise we log
# the current timestamp and accept the audit log can compute deltas later.
latency_ms = tool_result.get("latency_ms")
if latency_ms is None:
    latency_ms = data.get("latency_ms")

is_error = bool(tool_result.get("is_error") or data.get("is_error"))

# Compact, single-line JSON for append-only JSONL
entry = {
    "ts": int(time.time() * 1000),
    "tool": tool_name,
    "latency_ms": latency_ms,
    "is_error": is_error,
}
# Optional file_path if present (Edit/Write/Read) — useful for cross-ref
fp = tool_input.get("file_path") or tool_input.get("path")
if fp:
    entry["file"] = fp

print(json.dumps(entry, separators=(",", ":")))
PYEOF
)
    if [ -n "$LINE" ]; then
        echo "$LINE" >> "$LOG_FILE" 2>/dev/null || true
    fi
fi

echo '{"decision":"approve"}'
exit 0
