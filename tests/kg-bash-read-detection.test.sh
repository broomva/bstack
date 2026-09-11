#!/usr/bin/env bash
# kg-bash-read-detection.test.sh — the m5 shell blind spot.
#
# raw.kg_sessions (the numerator of m5_kg_load_rate) counts a session as having consumed the knowledge graph when it
# calls Skill(kg|checkit), or Read/Grep/Glob with a knowledge path in a PATH-BEARING
# FIELD. Bash was inspected for one thing only (dangerouslyDisableSandbox), so
# `sed -n '1,40p' docs/research/entities/...` — the ordinary way to read an entity
# under a harness that prefers the shell — scored zero. Measured in
# work/stimulus/sri on 2026-09-11: m5 = 0.0 across 31 sessions while entity files
# were being read the whole time.
#
# Three cases, and the middle one is the reason this is not a one-line regex:
#
#   1. Bash READS an entity path            -> counts     (the fix; red before it)
#   2. Bash MENTIONS the path inside quotes  -> must NOT count
#      `grep "research/entities" notes.md` searches FOR the string and reads no
#      entity. Matching the raw command string would score it, which is precisely
#      the h ⟂ U break the Read/Grep/Glob branch avoids by reading `path` and never
#      `pattern`. A detector that fires on case 2 has replaced a false 0.0 with a
#      false 1.0.
#   3. Unrelated shell command               -> must NOT count
#
# Case 1 fails if `bash_path_tokens` or the Bash branch is reverted. Cases 2 and 3
# fail if either is widened to a raw-string match. Both directions are asserted
# because a blind detector and a credulous one are equally useless as a sensor.
set -uo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
# Overridable so the negative control can point at a pristine copy of the sensor
# and prove these assertions go red without the fix.
SENSOR="${SENSOR:-$REPO/scripts/leverage-sensor.py}"

PASS=0; FAIL=0
ok()  { echo "  ok   $1"; PASS=$((PASS+1)); }
bad() { echo "  FAIL $1"; FAIL=$((FAIL+1)); }

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
WS="$TMP/ws"; mkdir -p "$WS/.control"
TR="$TMP/transcripts"; mkdir -p "$TR"

# One session per file; each holds a single Bash tool_use. The sensor derives
# m5 = kg_sessions / sessions, so one session per case reads the rate directly.
emit() { # emit <file> <command>
  python3 - "$1" "$2" <<'PY'
import json, sys
path, cmd = sys.argv[1], sys.argv[2]
rec = {"type": "assistant", "message": {"content": [
    {"type": "tool_use", "name": "Bash", "input": {"command": cmd}}]}}
open(path, "w").write(json.dumps(rec) + "\n")
PY
}

# Assert on raw.kg_sessions, not metrics.m5: m5 is null unless a setpoints file
# loads, so binding the test to it would couple a detection assertion to unrelated
# config and mask the very counter the fix moves.
kg_sessions() {
  python3 "$SENSOR" --workspace "$WS" --transcripts "$TR/*.jsonl" \
      --window 3650 --json --no-store 2>/dev/null \
    | python3 -c 'import sys,json; print(json.load(sys.stdin)["raw"]["kg_sessions"])'
}

# --- case 1: a real shell read of the entity store ------------------------------
rm -f "$TR"/*.jsonl
emit "$TR/a.jsonl" "sed -n '1,40p' docs/research/entities/pattern/one-write-path.md"
r="$(kg_sessions)"
[ "$r" = "1" ] && ok "shell read of an entity path counts (kg_sessions=$r)" \
               || bad "shell read of an entity path NOT counted (kg_sessions=$r, want 1)"

# --- case 2: the path appears only as a quoted search term ----------------------
rm -f "$TR"/*.jsonl
emit "$TR/b.jsonl" 'grep -rn "research/entities" /tmp/unrelated-notes.md'
r="$(kg_sessions)"
[ "$r" = "0" ] && ok "quoted path is a search term, not a read (kg_sessions=$r)" \
               || bad "quoted path counted as a read (kg_sessions=$r, want 0)"

# --- case 3: unrelated shell work ----------------------------------------------
rm -f "$TR"/*.jsonl
emit "$TR/c.jsonl" "bun run lint && git status --porcelain"
r="$(kg_sessions)"
[ "$r" = "0" ] && ok "unrelated command does not count (kg_sessions=$r)" \
               || bad "unrelated command counted (kg_sessions=$r, want 0)"

echo
echo "  $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
