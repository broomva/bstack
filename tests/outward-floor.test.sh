#!/usr/bin/env bash
# tests/outward-floor.test.sh — BRO-3955 m7 outward_artifact_floor (leverage-ship-sensor.py).
# Hermetic: merged-PR lists come from --outward-fixture; no gh, no network.
set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/.." && pwd)"
SHIP="$REPO/scripts/leverage-ship-sensor.py"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

PASS=0; FAIL=0
ok(){ PASS=$((PASS+1)); echo "  ok   $1"; }
bad(){ FAIL=$((FAIL+1)); echo "  FAIL $1"; }
eq(){ if [ "$2" = "$3" ]; then ok "$1 ($2)"; else bad "$1 (got '$2' want '$3')"; fi; }

# ISO timestamps relative to now: recent (1 day ago) and old (20 days ago).
read -r RECENT OLD <<<"$(python3 -c '
import datetime as d; n=d.datetime.now(d.timezone.utc)
f=lambda x:(n-d.timedelta(days=x)).strftime("%Y-%m-%dT%H:%M:%SZ"); print(f(1), f(20))')"
pr(){ printf '{"number":1,"mergedAt":"%s","author":{"login":"%s","is_bot":%s}}' "$1" "$2" "$3"; }
fx(){ local f="$TMP/$1.json"; cat > "$f"; echo "$f"; }
NOREC="$TMP/no-receipts.jsonl"   # never created: an absent receipts file
cfg(){ printf '{"product_repos":["acme/a","acme/b"],"author_allowlist":["broomva"],"door_receipts":"%s"}' "${1:-$NOREC}"; }
m7(){ python3 -I "$SHIP" --workspace "$TMP" --config-json '{}' --fleet-config-json "${2:-$(cfg)}" \
        --outward-fixture "$1" --json 2>/dev/null; }
val(){ python3 -c "import json,sys;d=json.load(sys.stdin);print(d$1)"; }

echo "== the floor =="
F=$(fx one <<J
{"acme/a":[$(pr "$RECENT" broomva false)],"acme/b":[]}
J
)
eq "one recent merge -> 1.0" "$(m7 "$F" | val "['m7_outward_artifact_floor']")" "1.0"
eq "counted per repo" "$(m7 "$F" | val "['outward']['per_repo']['acme/a']")" "1"
F=$(fx none <<J
{"acme/a":[],"acme/b":[]}
J
)
eq "every repo reachable, nothing merged -> 0.0 (fires)" "$(m7 "$F" | val "['m7_outward_artifact_floor']")" "0.0"

echo "== only the agent's own recent merges count =="
F=$(fx teammate <<J
{"acme/a":[$(pr "$RECENT" someone-else false)],"acme/b":[]}
J
)
eq "teammate merge does not count" "$(m7 "$F" | val "['m7_outward_artifact_floor']")" "0.0"
F=$(fx bot <<J
{"acme/a":[$(pr "$RECENT" broomva true)],"acme/b":[]}
J
)
eq "bot merge does not count" "$(m7 "$F" | val "['m7_outward_artifact_floor']")" "0.0"
F=$(fx old <<J
{"acme/a":[$(pr "$OLD" broomva false)],"acme/b":[]}
J
)
eq "merge outside the window does not count" "$(m7 "$F" | val "['m7_outward_artifact_floor']")" "0.0"

echo "== an unreachable repo cannot certify an empty window =="
F=$(fx partial <<J
{"acme/a":[]}
J
)
eq "nothing counted + a repo unreachable -> null" "$(m7 "$F" | val "['m7_outward_artifact_floor']")" "None"
eq "the unreachable repo is named" "$(m7 "$F" | val "['outward']['errors']")" "['acme/b']"
F=$(fx partial-shipped <<J
{"acme/a":[$(pr "$RECENT" broomva false)]}
J
)
eq "a counted merge still certifies 1.0 despite an outage" "$(m7 "$F" | val "['m7_outward_artifact_floor']")" "1.0"

echo "== door receipts =="
REC="$TMP/receipts.jsonl"
printf '{"ts":"%s","door":"talk"}\n{"ts":"%s","door":"old"}\nnot json\n' "$RECENT" "$OLD" > "$REC"
F=$(fx none2 <<J
{"acme/a":[],"acme/b":[]}
J
)
eq "a recent receipt satisfies the floor" "$(m7 "$F" "$(cfg "$REC")" | val "['m7_outward_artifact_floor']")" "1.0"
eq "only the in-window receipt counts" "$(m7 "$F" "$(cfg "$REC")" | val "['outward']['door_receipts']")" "1"
eq "absent receipts file is reported as absent" "$(m7 "$F" | val "['outward']['door_receipts_status']")" "absent"

echo "== receipts are read strictly (round 1) =="
read -r NAIVE_OLD EPOCH_OLD EPOCH_NEW <<<"$(python3 -c '
import datetime as d, time; n=d.datetime.now(d.timezone.utc)
print((n-d.timedelta(days=2000)).strftime("%Y-%m-%dT%H:%M:%S"), int(time.time())-2000*86400, int(time.time())-3600)')"
REC2="$TMP/receipts2.jsonl"
printf '{"ts":"%s"}\n{"ts":%s}\n{"ts":"garbage"}\n{"door":"no ts"}\n' "$NAIVE_OLD" "$EPOCH_OLD" > "$REC2"
eq "old naive-ISO and old epoch receipts do not count" "$(m7 "$F" "$(cfg "$REC2")" | val "['m7_outward_artifact_floor']")" "0.0"
eq "unplaceable lines are counted as unparsed" "$(m7 "$F" "$(cfg "$REC2")" | val "['outward']['door_receipts_unparsed']")" "2"
printf '{"ts":%s}\n' "$EPOCH_NEW" > "$REC2"
eq "a recent epoch receipt counts" "$(m7 "$F" "$(cfg "$REC2")" | val "['m7_outward_artifact_floor']")" "1.0"
if [ "$(id -u)" != 0 ]; then
  printf '{"ts":"%s"}\n' "$RECENT" > "$REC2"; chmod 000 "$REC2"
  eq "unreadable receipts + no merges -> null, not 0.0" "$(m7 "$F" "$(cfg "$REC2")" | val "['m7_outward_artifact_floor']")" "None"
  chmod 600 "$REC2"
fi

echo "== fail closed without config =="
eq "no product repos -> null" "$(m7 "$F" '{"author_allowlist":["broomva"]}' | val "['m7_outward_artifact_floor']")" "None"
eq "no author allowlist -> null" "$(m7 "$F" '{"product_repos":["acme/a"]}' | val "['m7_outward_artifact_floor']")" "None"

echo "outward-floor: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
