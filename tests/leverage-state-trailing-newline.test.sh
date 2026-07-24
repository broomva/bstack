#!/usr/bin/env bash
# leverage-state-trailing-newline.test.sh — BRO-1973.
#
# Both leverage sensors write a JSON snapshot into <workspace>/.control/. A governed
# repo may TRACK those files, so an emitted file without a POSIX trailing newline
# leaves the workspace git-dirty after every session and fails formatter gates
# (biome / ultracite / prettier) on a repo that is otherwise green — observed live in
# work/stimulus/sri, where `bun run lint` failed on nothing but the missing byte.
#
# The invariant asserted here: every file either sensor emits into .control/ ends
# with "\n". Reverting either `f.write("\n")` must turn this test red.
set -uo pipefail

BSTACK_REPO="$(cd "$(dirname "$0")/.." && pwd)"
SENSOR="$BSTACK_REPO/scripts/leverage-sensor.py"
SHIP="$BSTACK_REPO/scripts/leverage-ship-sensor.py"
SETPOINTS_TPL="$BSTACK_REPO/assets/templates/leverage-setpoints.yaml"

PASS=0; FAIL=0
ok()  { echo "  ok   $1"; PASS=$((PASS+1)); }
bad() { echo "  FAIL $1"; FAIL=$((FAIL+1)); }

# ends_with_newline <file> — true when the final byte is 0x0a. Uses tail -c1 rather
# than a text-mode read so an editor's own newline handling can't mask the defect.
ends_with_newline() {
  [ -s "$1" ] || return 1
  [ "$(tail -c 1 "$1" | od -An -tu1 | tr -d ' ')" = "10" ]
}

assert_newline() {
  local file="$1" label="$2"
  if [ ! -f "$file" ]; then bad "$label — file not written ($file)"; return; fi
  if ends_with_newline "$file"; then
    ok "$label ends with a trailing newline"
  else
    bad "$label ends WITHOUT a trailing newline — tracked copies go git-dirty every session"
  fi
}

TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
WS="$TMP/ws"; TX="$TMP/tx"
mkdir -p "$WS/.control" "$TX"
cp "$SETPOINTS_TPL" "$WS/.control/leverage-setpoints.yaml"

STATE="$WS/.control/leverage-state.json"
METRICS="$WS/.control/leverage-metrics.jsonl"
SHIP_STATE="$WS/.control/leverage-ship-state.json"

# ── fixture: one session with enough signal that the sensor emits a full record ──
python3 - "$TX/sess1.jsonl" <<'PY'
import json, sys
rows = [
  {"type": "assistant", "message": {"content": [
     {"type": "tool_use", "name": "Edit", "input": {"file_path": "/x/apps/web/page.tsx"}},
     {"type": "tool_use", "name": "Read", "input": {"file_path": "/x/research/entities/concept/foo.md"}},
  ]}},
  {"type": "user", "message": {"content": [
     {"type": "tool_result", "is_error": False, "content": "ok"},
  ]}},
]
open(sys.argv[1], "w").write("\n".join(json.dumps(r) for r in rows))
PY

echo "== leverage-sensor.py (.control/leverage-state.json) =="
python3 "$SENSOR" --workspace "$WS" --transcripts "$TX/*.jsonl" --window 3650 >/dev/null 2>&1
assert_newline "$STATE" "leverage-state.json"
assert_newline "$METRICS" "leverage-metrics.jsonl"

python3 -c "import json,sys;json.load(open(sys.argv[1]))" "$STATE" 2>/dev/null \
  && ok "leverage-state.json still parses as JSON with the newline appended" \
  || bad "leverage-state.json no longer parses as JSON"

# The state file is rewritten ("w") on every run — a second pass must not lose the
# newline, and must not accumulate blank lines either.
python3 "$SENSOR" --workspace "$WS" --transcripts "$TX/*.jsonl" --window 3650 >/dev/null 2>&1
assert_newline "$STATE" "leverage-state.json after a second run (rewrite path)"
TRAILING=$(python3 -c "
import sys
b = open(sys.argv[1],'rb').read()
print(len(b) - len(b.rstrip(b'\n')))
" "$STATE")
[ "$TRAILING" = "1" ] \
  && ok "exactly one trailing newline (no accumulation across runs)" \
  || bad "found $TRAILING trailing newlines — rewrite path is appending"

echo "== leverage-ship-sensor.py (.control/leverage-ship-state.json) =="
# No ship_signal.repos configured → compute() makes zero `gh` calls and returns
# gh_ok=False, but the store path still runs. Hermetic: no network, no auth.
python3 "$SHIP" --workspace "$WS" --config-json '{"repos": []}' >/dev/null 2>&1
assert_newline "$SHIP_STATE" "leverage-ship-state.json"

python3 -c "import json,sys;json.load(open(sys.argv[1]))" "$SHIP_STATE" 2>/dev/null \
  && ok "leverage-ship-state.json still parses as JSON with the newline appended" \
  || bad "leverage-ship-state.json no longer parses as JSON"

# The ship sensor writes through a tempfile + os.replace; the newline must survive
# the atomic swap, and no .ship-*.tmp may be left behind.
LEFTOVER=$(find "$WS/.control" -name ".ship-*.tmp" | wc -l | tr -d ' ')
[ "$LEFTOVER" = "0" ] \
  && ok "atomic swap left no .ship-*.tmp behind" \
  || bad "$LEFTOVER stale .ship-*.tmp file(s) in .control/"

echo ""
echo "leverage-state-trailing-newline: $PASS passed, $FAIL failed"
[ "$FAIL" = "0" ]
