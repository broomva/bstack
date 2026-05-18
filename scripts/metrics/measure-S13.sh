#!/usr/bin/env bash
# measure-S13.sh — bridge_freshness_seconds
#
# Returns seconds elapsed since the conversation bridge last ran.
# Stamp file: ~/.cache/broomva-bridge-stamp (touched by the bridge hook).
set -euo pipefail

STAMP="${BROOMVA_BRIDGE_STAMP:-$HOME/.cache/broomva-bridge-stamp}"

mtime_epoch() {
    local f="$1"
    if stat -f %m "$f" >/dev/null 2>&1; then
        stat -f %m "$f"
    else
        stat -c %Y "$f"
    fi
}

if [ ! -f "$STAMP" ]; then
    cat <<EOF
{"id":"S13","name":"bridge_freshness_seconds","value":null,"error":"stamp-missing","target":120,"alert_above":3600,"severity":"informational","unit":"seconds","stamp_path":"${STAMP}"}
EOF
    exit 0
fi

age=$(( $(date +%s) - $(mtime_epoch "$STAMP") ))

cat <<EOF
{"id":"S13","name":"bridge_freshness_seconds","value":${age},"target":120,"alert_above":3600,"severity":"informational","unit":"seconds","stamp_path":"${STAMP}"}
EOF
