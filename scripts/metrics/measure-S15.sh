#!/usr/bin/env bash
# measure-S15.sh — pii_redaction_active
#
# Confirms the conversation bridge applies PII redaction before writing.
# Looks for a `_redact_pii(` call (or equivalent) inside workspace bridge
# scripts. Returns 1 (true) if found, 0 (false) if not.
set -euo pipefail

WS="${BROOMVA_WORKSPACE:-$PWD}"

# Candidate paths where the bridge logic typically lives.
candidates=(
    "$WS/scripts/conversation-bridge-hook.sh"
    "$WS/scripts/conversation-history.py"
    "$WS/scripts/conversation_bridge.py"
)

found=0
matched_file=""
for f in "${candidates[@]}"; do
    [ -f "$f" ] || continue
    if grep -qE '(_redact_pii|redact_pii|redactPII)' "$f"; then
        found=1
        matched_file="$f"
        break
    fi
done

cat <<EOF
{"id":"S15","name":"pii_redaction_active","value":${found},"target":1,"alert_below":1,"severity":"blocking","unit":"bool","matched":$( [ -n "$matched_file" ] && jq -R . <<<"$matched_file" || echo null )}
EOF
