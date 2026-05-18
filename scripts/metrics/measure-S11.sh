#!/usr/bin/env bash
# measure-S11.sh — governance_files_present
#
# Checks the 5 governance files that a closed-substrate workspace must have:
#   CLAUDE.md, AGENTS.md, METALAYER.md, .control/policy.yaml, schemas/
set -euo pipefail

WS="${BROOMVA_WORKSPACE:-$PWD}"
count=0
missing=()
for f in CLAUDE.md AGENTS.md METALAYER.md .control/policy.yaml schemas; do
    if [ -e "$WS/$f" ]; then
        count=$((count + 1))
    else
        missing+=("$f")
    fi
done

# Construct missing array as JSON
missing_json="[]"
if [ "${#missing[@]}" -gt 0 ]; then
    missing_json=$(printf '%s\n' "${missing[@]}" | jq -R . | jq -s -c .)
fi

cat <<EOF
{"id":"S11","name":"governance_files_present","value":${count},"target":5,"alert_below":5,"severity":"blocking","unit":"count","missing":${missing_json},"workspace":"${WS}"}
EOF
