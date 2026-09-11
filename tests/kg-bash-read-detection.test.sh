#!/usr/bin/env bash
# kg-bash-read-detection.test.sh — the m5 shell blind spot, and the trap in closing it.
#
# raw.kg_sessions (the numerator of m5_kg_load_rate) counts a session as having
# consumed the knowledge graph. It saw Skill(kg|checkit) and Read/Grep/Glob PATH
# FIELDS; Bash it inspected for `dangerouslyDisableSandbox` alone, so the ordinary
# shell read of an entity scored zero.
#
# The trap, and the reason this file is long: m5 exists to make knowledge
# PRODUCTION conditional on CONSUMPTION. A detector that fires on production
# inverts the control loop -- `git add docs/research/knowledge-index.md` is the
# canonical LAST step of an authoring session, so scoring it as a read makes the
# "stop authoring, start reading" governor read greener the more you author.
#
# That is not hypothetical. The first draft of this fix matched any slash-bearing
# token against the knowledge-path regex, and adversarial review measured that 8
# of the 15 sessions it newly counted were writes, `ls`, or a CI script whose
# FILENAME contains "knowledge" -- turning a true 8/32 into a claimed 15/31.
#
# So the WRITE cases below are the substance of this file, not padding. A fix that
# passes only the read cases is the fix that was already refuted once.
#
# Direction of failure is deliberate: bash_read_targets is an ALLOWLIST of read
# verbs, so it UNDER-counts (`awk '/x/' $F`, a path in a variable, `find -exec cat`
# are all missed). For a floor gating a destructive actuator that is the correct
# direction -- the actuator fires on LOW readings, so a false high is the harmful
# error and a false low is merely conservative.
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

# ---------------------------------------------------------------------------
# Genuine reads — must count. Includes the two the refuted draft MISSED (a quoted
# path argument, and `git show <ref>:<path>`), because its quote-stripping treated
# every quoted path as a search term.
# ---------------------------------------------------------------------------
check_counts() { # check_counts <label> <command>
  rm -f "$TR"/*.jsonl; emit "$TR/x.jsonl" "$2"; r="$(kg_sessions)"
  [ "$r" = "1" ] && ok "READ counts: $1" || bad "READ not counted: $1 (kg_sessions=$r)"
}
check_misses() { # check_misses <label> <command>
  rm -f "$TR"/*.jsonl; emit "$TR/x.jsonl" "$2"; r="$(kg_sessions)"
  [ "$r" = "0" ] && ok "not a read: $1" || bad "NON-READ counted: $1 (kg_sessions=$r)"
}

check_counts "sed"                "sed -n '1,40p' docs/research/entities/pattern/one-write-path.md"
check_counts "head"               "head -40 docs/research/entities/gotcha/z.md"
check_counts "quoted path arg"    'cat "docs/research/entities/pattern/x.md"'
check_counts "git show ref:path"  "git show origin/main:docs/research/entities/decision/y.md"
check_counts "grep over store"    "grep -rn 'foo' docs/research/entities/"
check_counts "read in 2nd segment" "cd /tmp && cat docs/research/entities/pattern/q.md"

# ---------------------------------------------------------------------------
# Writes and incidental mentions — must NOT count. Every one of these was scored
# as a read by the refuted draft; five of them appeared in its headline sample.
# ---------------------------------------------------------------------------
check_misses "git add + commit"       "git add docs/research/knowledge-index.md && git commit -q -m regen"
check_misses "rm an entity"           "rm -f docs/research/entities/pattern/obsolete.md"
check_misses "mv an entity"           "mv docs/research/entities/a.md docs/research/entities/b.md"
check_misses "mkdir"                  "mkdir -p docs/research/entities/pattern"
check_misses "ls the directory"       "ls -la docs/research/entities/"
check_misses "redirect INTO the index" "python3 bookkeeping.py index > docs/research/knowledge-index.md"
check_misses "heredoc writes an entity" "cat > docs/research/entities/gotcha/new.md <<'EOF'
body
EOF"
check_misses "heredoc BODY names the catalog" "cat > /tmp/brief.md <<'B'
Read docs/research/knowledge-index.md first.
B"
check_misses "script FILENAME has knowledge" "bash scripts/ci/check-knowledge-index-fresh.sh"
check_misses "unrelated command"      "bun run lint && git status --porcelain"

echo
echo "  $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
