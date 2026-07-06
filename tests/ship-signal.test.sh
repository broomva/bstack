#!/usr/bin/env bash
# tests/ship-signal.test.sh — BRO-1707 exogenous ship-signal sensor + shadow merge + l3 guard.
# Hermetic: classify_prs is driven by fixtures via --fixture/--config-json (no gh, no yaml).
set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/.." && pwd)"
SHIP="$REPO/scripts/leverage-ship-sensor.py"
SENSOR="$REPO/scripts/leverage-sensor.py"
L3HOOK="$REPO/scripts/l3-stability-pretool-hook.sh"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

PASS=0; FAIL=0
ok(){ PASS=$((PASS+1)); echo "  ok  $1"; }
bad(){ FAIL=$((FAIL+1)); echo "  FAIL $1"; }
eq(){ if [ "$2" = "$3" ]; then ok "$1 ($2)"; else bad "$1 (got '$2' want '$3')"; fi; }

CFG='{"author_allowlist":["broomva"],"tier_weights":{"green":1.0,"ungated":0.5}}'

# write a fixture PR-list JSON and echo its path
fx(){ local f="$TMP/$1.json"; cat > "$f"; echo "$f"; }
# m6s from a fixture
m6s(){ python3 "$SHIP" --fixture "$1" --config-json "$CFG" --json 2>/dev/null \
  | python3 -c 'import json,sys;print(json.load(sys.stdin)["m6s_meta_work_ship_ratio"])'; }
# a raw.<expr> field from a fixture, e.g. rawget f "['product_ship']"
rawget(){ python3 "$SHIP" --fixture "$1" --config-json "$CFG" --json 2>/dev/null \
  | python3 -c "import json,sys;d=json.load(sys.stdin);print(d['raw']$2)"; }

echo "== fractional classification (kills the single-file flip) =="
# 1 product file among 9 meta → 0.1 product / 0.9 meta, NOT flipped to product
F=$(fx frac <<'J'
[{"number":1,"mergedAt":"2026-07-05T00:00:00Z","author":{"login":"broomva","is_bot":false},
"files":[{"path":"apps/genesis/a.ts"},{"path":"scripts/b.sh"},{"path":"docs/c.md"},{"path":"docs/d.md"},{"path":"docs/e.md"},{"path":"docs/f.md"},{"path":"docs/g.md"},{"path":"docs/h.md"},{"path":"docs/i.md"},{"path":"research/j.md"}],
"statusCheckRollup":[{"conclusion":"SUCCESS"}]}]
J
)
eq "meta-dominant PR stays meta (m6s)" "$(m6s "$F")" "0.9"

echo "== unit-weighted (kills size-padding) =="
# a 1-file product PR and a 40-file product PR each contribute exactly 1.0 product
F1=$(fx one <<'J'
[{"number":2,"author":{"login":"broomva","is_bot":false},"mergedAt":"2026-07-05T00:00:00Z",
"files":[{"path":"apps/g/x.ts"}],"statusCheckRollup":[{"conclusion":"SUCCESS"}]}]
J
)
python3 - "$SHIP" "$CFG" <<'PY'
import json,subprocess,sys
ship,cfg=sys.argv[1],sys.argv[2]
# 40-file pure-product PR
big=[{"number":3,"author":{"login":"broomva","is_bot":False},"mergedAt":"2026-07-05T00:00:00Z",
"files":[{"path":f"apps/g/f{i}.ts"} for i in range(40)],"statusCheckRollup":[{"conclusion":"SUCCESS"}]}]
open("/tmp/_big.json","w").write(json.dumps(big))
PY
eq "1-file product PR product_ship" "$(rawget "$F1" "['product_ship']")" "1.0"
eq "40-file product PR product_ship (same)" "$(rawget /tmp/_big.json "['product_ship']")" "1.0"

echo "== author allowlist + bot exclusion (h ⟂ U) =="
F=$(fx teammate <<'J'
[{"number":4,"author":{"login":"ja-818","is_bot":false},"mergedAt":"2026-07-05T00:00:00Z",
"files":[{"path":"work/houston/x.ts"}],"statusCheckRollup":[{"conclusion":"SUCCESS"}]}]
J
)
eq "teammate PR excluded → m6s None" "$(m6s "$F")" "None"
eq "teammate counted-as author-excluded" "$(rawget "$F" "['excluded']['author']")" "1"
F=$(fx bot <<'J'
[{"number":5,"author":{"login":"broomva","is_bot":true},"mergedAt":"2026-07-05T00:00:00Z",
"files":[{"path":"apps/g/x.ts"}],"statusCheckRollup":[{"conclusion":"SUCCESS"}]}]
J
)
eq "bot PR excluded" "$(rawget "$F" "['excluded']['bot']")" "1"

echo "== CI gating (red / self-modified gate capped at ungated 0.5) =="
F=$(fx red <<'J'
[{"number":6,"author":{"login":"broomva","is_bot":false},"mergedAt":"2026-07-05T00:00:00Z",
"files":[{"path":"apps/g/x.ts"}],"statusCheckRollup":[{"conclusion":"FAILURE"}]}]
J
)
eq "red PR gated at 0.5 product_ship" "$(rawget "$F" "['product_ship']")" "0.5"
F=$(fx selfci <<'J'
[{"number":7,"author":{"login":"broomva","is_bot":false},"mergedAt":"2026-07-05T00:00:00Z",
"files":[{"path":"apps/g/x.ts"},{"path":".github/workflows/ci.yml"}],"statusCheckRollup":[{"conclusion":"SUCCESS"}]}]
J
)
# 1 product + 0 meta (.github not classified) → product_share 1.0, gate capped 0.5 → 0.5
eq "self-modified-gate green PR capped at 0.5" "$(rawget "$F" "['product_ship']")" "0.5"

echo "== degenerate inputs =="
F=$(fx unclass <<'J'
[{"number":8,"author":{"login":"broomva","is_bot":false},"mergedAt":"2026-07-05T00:00:00Z",
"files":[{"path":"VERSION"},{"path":"LICENSE"}],"statusCheckRollup":[{"conclusion":"SUCCESS"}]}]
J
)
eq "unclassifiable PR → excluded, m6s None" "$(m6s "$F")" "None"
eq "unclassifiable counted" "$(rawget "$F" "['excluded']['unclassifiable']")" "1"
F=$(fx empty <<'J'
[]
J
)
eq "empty PR list → m6s None" "$(m6s "$F")" "None"

echo "== pure meta vs pure product =="
F=$(fx puremeta <<'J'
[{"number":9,"author":{"login":"broomva","is_bot":false},"mergedAt":"2026-07-05T00:00:00Z",
"files":[{"path":"bstack/scripts/x.sh"},{"path":"CLAUDE.md"}],"statusCheckRollup":[{"conclusion":"SUCCESS"}]}]
J
)
eq "pure-meta PR → m6s 1.0" "$(m6s "$F")" "1.0"

echo "== main sensor SHADOW merge: non-actuating, never worst =="
mkdir -p "$TMP/.control"
python3 - "$TMP" <<'PY'
import json,os,datetime
tmp=os.sys.argv[1]
now=datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
json.dump({"measured_at":now,"window_days":7,"shadow":True,"gh_ok":True,"repos":["x"],
  "m6s_meta_work_ship_ratio":0.42,"raw":{"pr_count_counted":3}},
  open(os.path.join(tmp,".control","leverage-ship-state.json"),"w"))
PY
OUT=$(python3 "$SENSOR" --workspace "$TMP" --window 7 --json --no-store 2>/dev/null)
GOT=$(echo "$OUT" | python3 -c 'import json,sys;print(json.load(sys.stdin)["metrics"].get("m6s_meta_work_ship_ratio"))')
eq "fresh ship-state merged into metrics" "$GOT" "0.42"
WORST=$(echo "$OUT" | python3 -c 'import json,sys;w=json.load(sys.stdin).get("worst");print((w or {}).get("key"))')
if [ "$WORST" != "m6s_meta_work_ship_ratio" ]; then ok "shadow metric is NOT worst (non-actuating)"; else bad "shadow metric became worst"; fi
STATUS=$(echo "$OUT" | python3 -c 'import json,sys;print([r["status"] for r in json.load(sys.stdin)["results"] if r["key"]=="m6s_meta_work_ship_ratio"][0])')
eq "shadow metric status = no_setpoint" "$STATUS" "no_setpoint"

echo "== staleness: >48h ship-state is NOT merged =="
python3 - "$TMP" <<'PY'
import json,os,datetime
tmp=os.sys.argv[1]
old=(datetime.datetime.now(datetime.timezone.utc)-datetime.timedelta(days=3)).isoformat(timespec="seconds")
json.dump({"measured_at":old,"m6s_meta_work_ship_ratio":0.99,"raw":{}},
  open(os.path.join(tmp,".control","leverage-ship-state.json"),"w"))
PY
GOT=$(python3 "$SENSOR" --workspace "$TMP" --window 7 --json --no-store 2>/dev/null \
  | python3 -c 'import json,sys;print(json.load(sys.stdin)["metrics"].get("m6s_meta_work_ship_ratio"))')
eq "stale ship-state ignored" "$GOT" "None"

echo "== l3 guard: setpoints protected + malformed-JSON bug fixed =="
export BROOMVA_WORKSPACE="$TMP"
rm -f "$TMP/.control/audit/l3-edits.jsonl"
OUT=$(printf '{"tool_name":"Edit","tool_input":{"file_path":"%s/.control/leverage-setpoints.yaml"}}' "$TMP" | bash "$L3HOOK")
echo "$OUT" | grep -q '"decision": *"approve"' && ok "setpoints edit approved (informational)" || bad "no approve decision"
echo "$OUT" | grep -qi 'leverage-setpoints.yaml' && ok "reason names the protected file" || bad "reason missing file"
ROW=$(tail -1 "$TMP/.control/audit/l3-edits.jsonl" 2>/dev/null)
echo "$ROW" | python3 -c 'import json,sys;json.load(sys.stdin)' 2>/dev/null && ok "logged row is valid JSON (malformed bug fixed)" || bad "logged row malformed: $ROW"
TN=$(echo "$ROW" | python3 -c 'import json,sys;print(json.load(sys.stdin)["tool_name"])' 2>/dev/null)
eq "logged tool_name is a bare value" "$TN" "Edit"
# non-L3 file → approve silently, no new row
rm -f "$TMP/.control/audit/l3-edits.jsonl"
printf '{"tool_name":"Edit","tool_input":{"file_path":"%s/apps/g/x.ts"}}' "$TMP" | bash "$L3HOOK" >/dev/null
[ ! -f "$TMP/.control/audit/l3-edits.jsonl" ] && ok "non-L3 edit not logged" || bad "non-L3 edit wrongly logged"
unset BROOMVA_WORKSPACE

echo
echo "ship-signal: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
