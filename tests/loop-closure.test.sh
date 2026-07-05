#!/usr/bin/env bash
# loop-closure.test.sh — proves the self-improvement loop is genuinely CLOSED and
# that the closure verdict FAILS on a fake/dead sensor (the un-blinding regression
# for the bug where §23 passed a 100%-null sensor as "wired + running + closing").
#
# Covers: real sensor h (transcript-derived, per RCS level), the actuation wire U
# (SessionStart injection > 0 bytes), and the content-aware closure verdict.
set -uo pipefail

BSTACK_REPO="$(cd "$(dirname "$0")/.." && pwd)"
SENSOR="$BSTACK_REPO/scripts/leverage-sensor.py"
WIRE="$BSTACK_REPO/scripts/knowledge-wakeup-hook.sh"
SETPOINTS_TPL="$BSTACK_REPO/assets/templates/leverage-setpoints.yaml"

PASS=0; FAIL=0
ok()  { echo "  ok   $1"; PASS=$((PASS+1)); }
bad() { echo "  FAIL $1"; FAIL=$((FAIL+1)); }

TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
WS="$TMP/ws"; TX="$TMP/tx"; EMPTY="$TMP/empty"
mkdir -p "$WS/.control" "$TX" "$EMPTY"
cp "$SETPOINTS_TPL" "$WS/.control/leverage-setpoints.yaml"

# ── fixture: one realistic session with a live signal at every RCS level ──────
python3 - "$TX/sess1.jsonl" <<'PY'
import json, sys
rows = [
  {"type": "assistant", "message": {"content": [
     {"type": "tool_use", "name": "Edit", "input": {"file_path": "/x/apps/web/page.tsx"}},           # L3 product edit
     {"type": "tool_use", "name": "Read", "input": {"file_path": "/x/research/entities/concept/foo.md"}},  # L2 kg read
  ]}},
  {"type": "user", "message": {"content": [
     {"type": "tool_result", "is_error": True,  "content": "File has not been read yet. Read it first before writing to it."},  # L0 read-before-edit
     {"type": "tool_result", "is_error": True,  "content": "command failed: exit 1"},                # L0 generic error
     {"type": "tool_result", "is_error": False, "content": "ok"},
  ]}},
  {"type": "user", "message": {"content": "continue please"}},  # L1 nudge
]
open(sys.argv[1], "w").write("\n".join(json.dumps(r) for r in rows))
PY

echo "== real loop (fixture with a live metric per level) =="
STATE="$WS/.control/leverage-state.json"
python3 "$SENSOR" --workspace "$WS" --transcripts "$TX/*.jsonl" --window 3650 >/dev/null 2>&1
[ -f "$STATE" ] && ok "sensor wrote leverage-state.json" || bad "sensor did not write state"

NN=$(python3 -c "import json;m=json.load(open('$STATE'))['metrics'];print(sum(1 for v in m.values() if v is not None))" 2>/dev/null)
[ "${NN:-0}" -ge 5 ] && ok "sensor produced ${NN} non-null metrics (not the all-null fake)" || bad "only ${NN:-0} non-null metrics"

CJ=$(python3 "$SENSOR" --workspace "$WS" --transcripts "$TX/*.jsonl" --window 3650 --closure --no-store 2>/dev/null); RC=$?
if echo "$CJ" | python3 -c "import json,sys;c=json.load(sys.stdin);sys.exit(0 if (c['sensor_live'] and c['levels_closed']) else 1)" 2>/dev/null; then
  ok "closure: sensor_live + levels_closed across L0–L3"
else
  bad "closure verdict not closed on the real loop"; echo "$CJ"
fi
[ "$RC" = "0" ] && ok "--closure exit 0 on a closed loop" || bad "--closure exit $RC on a closed loop"
echo "$CJ" | python3 -c "import json,sys;sys.exit(0 if not json.load(sys.stdin)['reference_authored'] else 1)" 2>/dev/null \
  && ok "reference_authored=false for bstack-default (causal-priority slot works)" \
  || bad "reference_authored should be false for the default template"

echo "== fake / dead sensor (no sessions) — must FAIL closed =="
CJ2=$(python3 "$SENSOR" --workspace "$WS" --transcripts "$EMPTY/*.jsonl" --window 3650 --closure --no-store 2>/dev/null); RC2=$?
echo "$CJ2" | python3 -c "import json,sys;sys.exit(0 if not json.load(sys.stdin)['closed'] else 1)" 2>/dev/null \
  && ok "closure verdict OPEN on a 0-session dead sensor" \
  || bad "dead sensor wrongly reported CLOSED (the blind-checker bug)"
[ "$RC2" != "0" ] && ok "--closure exit non-zero on a dead sensor (CI/doctor can gate)" || bad "--closure exit 0 on a dead sensor"

echo "== actuation wire (SessionStart injection) =="
BYTES=$(BSTACK_WORKSPACE="$WS" bash "$WIRE" 2>/dev/null | wc -c | tr -d ' ')
[ "${BYTES:-0}" -gt 0 ] && ok "SessionStart wire injected ${BYTES} bytes (was 0 with the phantom subcommand)" || bad "wire injected 0 bytes"

echo ""
echo "loop-closure: $PASS passed, $FAIL failed"
[ "$FAIL" = "0" ]
