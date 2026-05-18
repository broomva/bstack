#!/usr/bin/env bash
# measure-S14.sh — conversation_sessions_indexed
#
# Counts captured session markdown files in docs/conversations/.
set -euo pipefail

WS="${BROOMVA_WORKSPACE:-$PWD}"
DIR="$WS/docs/conversations"

if [ ! -d "$DIR" ]; then
    cat <<EOF
{"id":"S14","name":"conversation_sessions_indexed","value":0,"target":1,"alert_below":1,"severity":"informational","unit":"count","directory":"${DIR}","error":"directory-missing"}
EOF
    exit 0
fi

count=$(find "$DIR" -maxdepth 1 -name 'session-*.md' -type f 2>/dev/null | wc -l | tr -d '[:space:]')

cat <<EOF
{"id":"S14","name":"conversation_sessions_indexed","value":${count},"target":1,"alert_below":1,"severity":"informational","unit":"count","directory":"${DIR}"}
EOF
