#!/usr/bin/env bash
# bstack/scripts/control-gate-hook.sh — P2 Control Gate (Claude Code PreToolUse).
#
# THE safety shield. Filters the agent's proposed tool call before it acts.
# This script is SHIPPED by bstack and DEPLOYED into each workspace's scripts/
# by `bstack bootstrap` (Phase 3.1), so the hook reference in .claude/settings.json
# always resolves — closing the dangling-hook gap where a wired-but-undelivered
# control gate silently no-ops.
#
# Claude Code PreToolUse protocol:
#   stdin: { "tool_name", "tool_input": {...}, ... }
#   exit 0  -> allow the tool call
#   exit 2  -> BLOCK the tool call; stderr is shown to the model
#
# Hard gates (mirrors .control/policy.yaml gates.hard):
#   G1  no force-push to a shared branch
#   G2  no committing/writing secrets (.env, credentials, keys, .pem)
#   G3  no catastrophic deletes (rm -rf /, rm -rf ~) / hard reset to remote
#
# Self-contained: the canonical patterns are embedded; extra Bash deny-patterns
# in .control/policy.yaml under gates.hard[].pattern are merged in automatically.
# Portable: resolves the workspace via $CLAUDE_PROJECT_DIR / $BROOMVA_WORKSPACE / git.

set -uo pipefail

INPUT="$(cat 2>/dev/null || echo '{}')"
REPO_ROOT="${CLAUDE_PROJECT_DIR:-${BROOMVA_WORKSPACE:-$(git rev-parse --show-toplevel 2>/dev/null || echo "$PWD")}}"
POLICY="$REPO_ROOT/.control/policy.yaml"

command -v python3 >/dev/null 2>&1 || exit 0  # cannot evaluate -> allow (fail-open, non-blocking)

VERDICT="$(python3 - "$INPUT" "$POLICY" <<'PYEOF'
import sys, json, re

raw = sys.argv[1] if len(sys.argv) > 1 else "{}"
policy_path = sys.argv[2] if len(sys.argv) > 2 else ""
try:
    data = json.loads(raw)
except Exception:
    print("ALLOW")
    sys.exit(0)

tool = data.get("tool_name", "")
ti = data.get("tool_input", {}) if isinstance(data.get("tool_input"), dict) else {}

# embedded canonical hard gates
bash_deny = [
    (r"git\s+push\b.*(--force\b|--force-with-lease\b|\s-f\b)", "G1: force-push is blocked (rewrites shared history)"),
    (r"\brm\s+-rf\s+(/|~|\$HOME)(\s|$)",                        "G3: catastrophic recursive delete is blocked"),
    (r"\bgit\s+reset\s+--hard\b.*origin",                       "G3: hard reset to remote is blocked (discards work)"),
    (r"--no-verify\b",                                          "G2: bypassing pre-commit hooks (--no-verify) is blocked"),
]
path_deny = [
    (r"(^|/)\.env(\.|$)", "G2: writing a .env secret file is blocked"),
    (r"credentials",      "G2: writing a credentials file is blocked"),
    (r"(^|/)id_rsa\b",    "G2: writing a private SSH key is blocked"),
    (r"\.pem$",           "G2: writing a .pem key is blocked"),
    (r"\.key$",           "G2: writing a .key file is blocked"),
]

# merge extra Bash patterns from policy.yaml gates.hard[].pattern (best effort, no yaml dep)
try:
    if policy_path:
        with open(policy_path) as f:
            for line in f:
                m = re.search(r"^\s*pattern:\s*[\"']?(.+?)[\"']?\s*$", line)
                if m:
                    bash_deny.append((m.group(1), "policy.yaml gate"))
except Exception:
    pass

def deny(patterns, value):
    for pat, reason in patterns:
        try:
            if re.search(pat, value):
                return reason
        except re.error:
            continue
    return None

reason = None
if tool == "Bash":
    reason = deny(bash_deny, ti.get("command", ""))
elif tool in ("Write", "Edit", "MultiEdit"):
    fp = ti.get("file_path") or ti.get("path") or ""
    reason = deny(path_deny, fp)

print("BLOCK:" + reason if reason else "ALLOW")
PYEOF
)"

if [[ "$VERDICT" == BLOCK:* ]]; then
  echo "bstack P2 Control Gate blocked this action — ${VERDICT#BLOCK:}" >&2
  exit 2
fi
exit 0
