#!/usr/bin/env bash
# ask-origin-stamp-hook.sh — PostToolUse automatic ask-origin stamp (BRO-2918).
#
# ask_ledger.py has had stamp_origin()/`stamp` since BRO-2179's follow-up, but
# nothing called it when an ask was actually written: of 51 ledgers on main,
# only 1 carried an origin, and Maestro's answer router filed 6 of 14 answers
# as brand-new "Act on your answer" sessions for the reason "No session is
# named as the one that asked". This hook is the write-time call: every
# Write/Edit/MultiEdit of a `.control/asks/*.yaml` file gets `stamp` run on it
# immediately after, so a new ask always carries the session that raised it.
#
# Two cheap bash-only filters run BEFORE any subprocess, because this plugin
# loads at personal scope (fires in EVERY Claude Code session, every repo, on
# every Write/Edit/MultiEdit — see hooks.json's description):
#   1. no PASEO_AGENT_ID -> nothing to stamp with, exit immediately.
#   2. the raw JSON does not even mention `.control/asks/*.yaml` as a
#      substring -> not our file, exit immediately.
# Only a call that survives both spawns python3, which parses the JSON
# properly and re-checks the exact path before writing anything.
#
# Never blocks: PostToolUse exit codes do not gate anything (the tool call
# already happened), and this hook always exits 0 regardless. But `stamp` can
# now legitimately refuse a write it cannot do safely — a hand-written origin
# in a shape its line-based edit cannot verify (flow style, a comment on its
# line, keys after options) makes it raise and write nothing rather than guess
# (BRO-2918 P20 round 2) — so stderr is let through to the hook's own stderr
# (visible to the operator per Claude Code's hook-output capture) instead of
# being discarded; the edit that triggered this hook is never touched either way.
set -uo pipefail

INPUT="$(cat 2>/dev/null || echo '{}')"

[ -n "${PASEO_AGENT_ID:-}" ] || exit 0

case "$INPUT" in
  *'"tool_name":"Write"'*|*'"tool_name": "Write"'* | \
  *'"tool_name":"Edit"'*|*'"tool_name": "Edit"'* | \
  *'"tool_name":"MultiEdit"'*|*'"tool_name": "MultiEdit"'*) ;;
  *) exit 0 ;;
esac

case "$INPUT" in
  *'.control/asks/'*'.yaml'*) ;;
  *) exit 0 ;;
esac

command -v python3 >/dev/null 2>&1 || exit 0

SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd)"
LEDGER_PY="${CLAUDE_PLUGIN_ROOT:-$SELF_DIR/..}/scripts/ask_ledger.py"
[ -f "$LEDGER_PY" ] || exit 0

REPO_ROOT="${CLAUDE_PROJECT_DIR:-$(git rev-parse --show-toplevel 2>/dev/null || echo "$PWD")}"

# python3 -I, never bare python3 (BRO-2652): this hook runs with the session's
# cwd, i.e. whatever repository the session is in, so a json.py committed
# there must not be able to replace the stdlib module and misreport the path.
FILE_PATH="$(python3 -I - "$INPUT" "$REPO_ROOT" <<'PYEOF'
import json
import os
import sys

raw, repo_root = sys.argv[1], sys.argv[2]
try:
    data = json.loads(raw)
except Exception:
    sys.exit(0)

if data.get("tool_name") not in ("Write", "Edit", "MultiEdit"):
    sys.exit(0)

ti = data.get("tool_input")
if not isinstance(ti, dict):
    sys.exit(0)

fp = ti.get("file_path") or ti.get("filePath") or ""
if not fp:
    sys.exit(0)

# Repo-relative, exactly one path segment under asks/ — `.control/asks/<arc>.yaml`,
# never a nested subdirectory (mirrors the shape of every shipped ledger).
try:
    rel = os.path.relpath(fp, repo_root)
except ValueError:
    sys.exit(0)  # fp and repo_root on different drives (Windows) — not expected here

parts = rel.split(os.sep)
if parts[:2] != [".control", "asks"] or len(parts) != 3 or not parts[2].endswith(".yaml"):
    sys.exit(0)

print(fp)
PYEOF
)"

[ -n "$FILE_PATH" ] || exit 0
[ -f "$FILE_PATH" ] || exit 0

python3 -I "$LEDGER_PY" stamp "$FILE_PATH" >/dev/null
exit 0
