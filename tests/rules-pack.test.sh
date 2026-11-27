#!/usr/bin/env bash
# tests/rules-pack.test.sh — BRO-4147. `bstack bootstrap` ends with a rule registry whose
# every row replays, and the rows bstack ships (references/rules-pack.yaml) each fail when
# the hook they prove stops refusing.
#
# The replay is done by the `correct` skill (broomva/skills). This test needs a copy that
# has `init --pack`: $BSTACK_TEST_CORRECT names its correct.py (CI pins one), otherwise the
# installed skill is used. There is no skip path: without the skill the test fails.
#
# Every case bootstraps an empty scratch repository. Nothing is written outside it.
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
EXPECTED_ASSERTIONS=52
PASS=0; FAIL=0; FAILED=()
pass() { echo "  [ok] $1"; PASS=$((PASS + 1)); }
fail() { echo "  [FAIL] $1"; FAIL=$((FAIL + 1)); FAILED+=("$1"); }
# is <label> <command...>: one assertion, passed when the command succeeds.
is() { local label="$1"; shift; if "$@"; then pass "$label"; else fail "$label"; fi; }
has() { grep -qF -- "$2" "$1"; }
lacks() { ! grep -qF -- "$2" "$1"; }
# row <file> <status> <id>: the replay printed that status for that row.
row() { grep -qE "^ *$2 +[a-z]+ +$3( |\$)" "$1"; }

echo "── rules-pack (BRO-4147) ──────────────────────────────"

CORRECT="${BSTACK_TEST_CORRECT:-}"
if [ -z "$CORRECT" ]; then
    CORRECT="$(BSTACK_CORRECT='' bash "$REPO/scripts/rules-registry.sh" locate)" || CORRECT=""
fi
if [ -z "$CORRECT" ] || [ ! -f "$CORRECT" ] || ! grep -q -- '--pack' "$CORRECT"; then
    echo "  [FAIL] no copy of the correct skill with \`init --pack\` (BSTACK_TEST_CORRECT='${BSTACK_TEST_CORRECT:-}')" >&2
    echo "         install it: npx skills add broomva/skills --skill correct -g" >&2
    exit 1
fi
echo "  using $CORRECT"

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
OUT="$TMP/out"
PACK="$REPO/references/rules-pack.yaml"

# shellcheck source=scripts/lib/broomva-paths.sh
. "$REPO/scripts/lib/broomva-paths.sh"
policy_of() { broomva_repo_path policy "$1"; }
registry_of() { echo "$(dirname "$(policy_of "$1")")/rules.yaml"; }

new_ws() {
    local ws="$TMP/$1"
    mkdir -p "$ws"
    (
        cd "$ws" && git init -q
        git -c user.email=t@t -c user.name=t commit -q --allow-empty -m init
        git checkout -q -b work
    ) >/dev/null 2>&1
    echo "$ws"
}
# boot <workspace> [VAR=value ...]: bootstrap it; the log is <workspace>.log.
boot() {
    local ws="$1"; shift
    env BSTACK_CORRECT="$CORRECT" BROOMVA_WORKSPACE="$ws" BSTACK_SKIP_SKILLS=1 BSTACK_SKIP_RCS=1 "$@" \
        bash "$REPO/scripts/bootstrap.sh" >"$ws.log" 2>&1
}
# replay <workspace>: `rules-registry.sh check`; output in $OUT, status in $RC.
replay() {
    RC=0
    BSTACK_CORRECT="$CORRECT" BROOMVA_WORKSPACE="$1" bash "$REPO/scripts/rules-registry.sh" check >"$OUT" 2>&1 || RC=$?
}
commit_all() {
    git -C "$1" add -A
    git -C "$1" -c user.email=t@t -c user.name=t commit -q -m "$2" >/dev/null 2>&1
}
# The ids of the pack's rows at a level ("hook" or "gap"), read from the pack itself, so a
# row added to the pack is asserted here without an edit.
pack_ids() {
    python3 - "$PACK" "$1" <<'PY'
import sys, yaml
for rule in yaml.safe_load(open(sys.argv[1]))["rules"]:
    if rule["level"] == sys.argv[2]:
        print(rule["id"])
PY
}
HOOK_ROWS="$(pack_ids hook)"
GAP_ROWS="$(pack_ids gap)"

# ── 1. A fresh bootstrap ends with a registry whose every row replays ────────
echo "1. fresh bootstrap"
WS="$(new_ws fresh)"
is "bootstrap exits 0" boot "$WS"
is "bootstrap says the registry replays" has "$WS.log" "rule registry: every row replays."
is "the registry was written beside the policy" test -f "$(registry_of "$WS")"
replay "$WS"
is "the replay exits 0" test "$RC" -eq 0
is "five hook rows and one gap row come from the pack" test "$(echo "$HOOK_ROWS" | wc -l | tr -d ' ')/$(echo "$GAP_ROWS" | wc -l | tr -d ' ')" = "5/1"
missing=""
for id in $HOOK_ROWS; do row "$OUT" PASS "$id" || missing="$missing $id"; done
is "every hook row of the pack is replayed and passes (missing:${missing:- none})" test -z "$missing"
missing=""
for id in $GAP_ROWS; do row "$OUT" GAP "$id" || missing="$missing $id"; done
is "the gap row is reported as a gap, not as a pass" test -z "$missing"
is "the starter row replays too" row "$OUT" PASS branch-first
is "every blocking hook and git hook is accounted for" row "$OUT" PASS registry-completeness
is "nothing failed" lacks "$OUT" "FAIL"
is "the control gate runs on MultiEdit" grep -q '"matcher": "MultiEdit"' "$WS/.claude/settings.json"
is "the policy carries gates.hard" grep -q '^  hard: \[\]$' "$(policy_of "$WS")"

# ── 2. A second bootstrap changes nothing ────────────────────────────────────
echo "2. second bootstrap"
commit_all "$WS" bootstrapped
is "bootstrap exits 0 again" boot "$WS"
is "the tree is unchanged" test -z "$(git -C "$WS" status --porcelain)"
is "it says the registry is kept" has "$WS.log" "already has a registry"
is "and still replays it" has "$WS.log" "rule registry: every row replays."

# ── 3. Each row fails when its hook stops refusing ───────────────────────────
echo "3. a row fails when its hook stops refusing"
GATE="$WS/scripts/control-gate-hook.sh"
cp "$GATE" "$TMP/gate.orig"
# drop <reason>: remove the one pattern line of the gate that carries that reason.
drop() {
    python3 - "$GATE" "$1" <<'PY'
import sys
path, reason = sys.argv[1], sys.argv[2]
lines = open(path, encoding="utf-8").read().splitlines(keepends=True)
kept = [line for line in lines if reason not in line]
assert len(lines) - len(kept) == 1, f"{reason!r} is on {len(lines) - len(kept)} line(s) of the gate"
open(path, "w", encoding="utf-8").write("".join(kept))
PY
}
# One line per pattern of the gate: its reason, then the row that proves it.
while IFS='|' read -r reason id; do
    cp "$TMP/gate.orig" "$GATE"
    if drop "$reason"; then
        replay "$WS"
        others=""
        for other in $HOOK_ROWS; do
            [ "$other" = "$id" ] || row "$OUT" PASS "$other" || others="$others $other"
        done
        if [ "$RC" -eq 1 ] && row "$OUT" FAIL "$id" && [ -z "$others" ]; then
            pass "without \"$reason\" only $id fails"
        else
            fail "without \"$reason\": exit $RC, expected only $id to fail (also not passing:${others:- none})"
        fi
    else
        fail "the gate has no single line for \"$reason\""
    fi
done <<'EOF'
G1: force-push|no-force-push
G3: catastrophic recursive delete|no-catastrophic-delete
G3: hard reset to remote|no-hard-reset-to-remote
G2: bypassing pre-commit hooks|no-hook-bypass
G2: writing a .env secret file|no-secret-file-write
G2: writing a credentials file|no-secret-file-write
G2: writing a private SSH key|no-secret-file-write
G2: writing a .pem key|no-secret-file-write
G2: writing a .key file|no-secret-file-write
EOF
printf '#!/usr/bin/env bash\nexit 0\n' >"$GATE"
replay "$WS"
failed=""
for id in $HOOK_ROWS; do row "$OUT" FAIL "$id" || failed="$failed $id"; done
is "a gate that allows everything fails every hook row (not failing:${failed:- none})" test "$RC" -eq 1 -a -z "$failed"
cp "$TMP/gate.orig" "$GATE"

cp "$WS/.claude/settings.json" "$TMP/settings.orig"
python3 - "$WS/.claude/settings.json" <<'PY'
import json, sys
path = sys.argv[1]
doc = json.load(open(path))
blocks = doc["hooks"]["PreToolUse"]
kept = [b for b in blocks if not (b.get("matcher") == "MultiEdit" and "control-gate-hook.sh" in json.dumps(b))]
assert len(blocks) - len(kept) == 1, "expected one MultiEdit control-gate block"
doc["hooks"]["PreToolUse"] = kept
json.dump(doc, open(path, "w"), indent=2)
PY
replay "$WS"
is "a gate that is not registered for MultiEdit fails the secrets row" test "$RC" -eq 1 -a "$(row "$OUT" FAIL no-secret-file-write; echo $?)" = 0
cp "$TMP/settings.orig" "$WS/.claude/settings.json"
replay "$WS"
is "restored, the replay exits 0 again" test "$RC" -eq 0

# ── 4. doctor reports the same two states ────────────────────────────────────
echo "4. doctor"
BSTACK_CORRECT="$CORRECT" BROOMVA_WORKSPACE="$WS" bash "$REPO/scripts/doctor.sh" >"$OUT" 2>&1
is "doctor passes the section when every row replays" has "$OUT" "[ok] rule registry: every row replays"
drop "G1: force-push"
BSTACK_CORRECT="$CORRECT" BROOMVA_WORKSPACE="$WS" bash "$REPO/scripts/doctor.sh" >"$OUT" 2>&1
is "doctor reports a gap when a row does not hold" has "$OUT" "[gap] rule registry: a row does not hold"
is "and names the row" grep -qE 'FAIL +hook +no-force-push' "$OUT"
cp "$TMP/gate.orig" "$GATE"
# A replay that runs out of time is neither: doctor says so and counts nothing.
RC=0
BSTACK_RULES_BUDGET_S=0.2 BSTACK_CORRECT="$CORRECT" BROOMVA_WORKSPACE="$WS" bash "$REPO/scripts/rules-registry.sh" check >"$OUT" 2>&1 || RC=$?
is "a replay cut off by its budget exits 5" test "$RC" -eq 5
BSTACK_DOCTOR_RULES_BUDGET_S=0.2 BSTACK_CORRECT="$CORRECT" BROOMVA_WORKSPACE="$WS" bash "$REPO/scripts/doctor.sh" --quiet >"$OUT" 2>&1
is "doctor reports an unfinished replay, under --quiet too" has "$OUT" "[info] rule registry: the replay did not finish in 0.2 s"
is "and counts it as neither a pass nor a gap" test "$(grep -cE '\[(ok|gap)\] rule registry' "$OUT")" = 0

# ── 5. A workspace bootstrapped before 0.53.0 is brought up ──────────────────
echo "5. a workspace from before 0.53.0"
OLD="$(new_ws old)"
is "BSTACK_SKIP_RULES=1 bootstraps without a registry" boot "$OLD" BSTACK_SKIP_RULES=1
is "and writes none" test ! -e "$(registry_of "$OLD")"
# Make it what 0.52 left: no gates.hard in the policy, no MultiEdit block for the gate.
python3 - "$(policy_of "$OLD")" "$OLD/.claude/settings.json" <<'PY'
import json, re, sys, yaml
policy, settings = sys.argv[1], sys.argv[2]
text = open(policy, encoding="utf-8").read()
start = text.index("gates:\n") + len("gates:\n")
end = text.index("  hard: []\n") + len("  hard: []\n")
text = text[:start] + text[end:].lstrip("\n")
assert "hard" not in yaml.safe_load(text)["gates"]
open(policy, "w", encoding="utf-8").write(text)
doc = json.load(open(settings))
doc["hooks"]["PreToolUse"] = [b for b in doc["hooks"]["PreToolUse"] if b.get("matcher") != "MultiEdit"]
json.dump(doc, open(settings, "w"), indent=2)
PY
is "bootstrap exits 0" boot "$OLD"
is "it added gates.hard to the policy" grep -q '^  hard: \[\]$' "$(policy_of "$OLD")"
is "it registered the gate for MultiEdit" grep -q '"matcher": "MultiEdit"' "$OLD/.claude/settings.json"
replay "$OLD"
is "and the replay exits 0" test "$RC" -eq 0

# A policy whose `gates:` this script does not edit is left byte for byte, and said so.
FLOW="$(new_ws flow)"
mkdir -p "$(dirname "$(policy_of "$OLD")" | sed "s|^$OLD|$FLOW|")"
FLOW_POLICY="$(policy_of "$OLD" | sed "s|^$OLD|$FLOW|")"
printf 'version: 1\ngates: {governed: []}\n' >"$FLOW_POLICY"
cp "$FLOW_POLICY" "$TMP/flow.orig"
RC=0
BSTACK_CORRECT="$CORRECT" BROOMVA_WORKSPACE="$FLOW" bash "$REPO/scripts/rules-registry.sh" init >"$OUT" 2>&1 || RC=$?
is "a flow-style gates block is not rewritten" cmp -s "$FLOW_POLICY" "$TMP/flow.orig"
is "and the script says to add the key by hand" has "$OUT" "add \`hard: []\` under \`gates:\` by hand"
is "and init does not report success" test "$RC" -ne 0

# ── 6. Without the skill nothing is written and nothing aborts ───────────────
echo "6. without the correct skill"
EMPTY_HOME="$TMP/home"; mkdir -p "$EMPTY_HOME"
BARE="$(new_ws bare)"
is "bootstrap still exits 0" boot "$BARE" HOME="$EMPTY_HOME" BSTACK_CORRECT=""
is "it says how to install the skill" has "$BARE.log" "npx skills add broomva/skills --skill correct -g"
is "it writes no registry" test ! -e "$(registry_of "$BARE")"
RC=0
HOME="$EMPTY_HOME" BSTACK_CORRECT="" BROOMVA_WORKSPACE="$BARE" bash "$REPO/scripts/rules-registry.sh" check >"$OUT" 2>&1 || RC=$?
is "check on a workspace with no registry exits 4" test "$RC" -eq 4
RC=0
HOME="$EMPTY_HOME" BSTACK_CORRECT="" BROOMVA_WORKSPACE="$WS" bash "$REPO/scripts/rules-registry.sh" check >"$OUT" 2>&1 || RC=$?
is "check on a registry it cannot replay exits 3" test "$RC" -eq 3
HOME="$EMPTY_HOME" BSTACK_CORRECT="" BROOMVA_WORKSPACE="$WS" bash "$REPO/scripts/doctor.sh" >"$OUT" 2>&1
is "doctor counts an unreplayable registry as a gap" has "$OUT" "[gap] the workspace has a rule registry and the correct skill that replays it is not installed"
RC=0
BSTACK_CORRECT="$TMP/nowhere/correct.py" BROOMVA_WORKSPACE="$WS" bash "$REPO/scripts/rules-registry.sh" check >"$OUT" 2>&1 || RC=$?
is "a named copy that is not there is an error, not a fallback" test "$RC" -eq 2
printf '#!/usr/bin/env python3\nprint("an older copy")\n' >"$TMP/old-correct.py"
RC=0
BSTACK_CORRECT="$TMP/old-correct.py" BROOMVA_WORKSPACE="$BARE" bash "$REPO/scripts/rules-registry.sh" init >"$OUT" 2>&1 || RC=$?
is "a copy older than the pack option is reported, exit 3" test "$RC" -eq 3 -a "$(has "$OUT" "predates"; echo $?)" = 0
is "and still writes no registry" test ! -e "$(registry_of "$BARE")"

echo ""
echo "rules-pack: $PASS passed, $FAIL failed"
if [ "$FAIL" -gt 0 ]; then
    printf '  failed: %s\n' "${FAILED[@]}"
    exit 1
fi
if [ "$PASS" -ne "$EXPECTED_ASSERTIONS" ]; then
    echo "rules-pack: $PASS assertions ran, $EXPECTED_ASSERTIONS expected (a case stopped asserting, or one was added without the count)" >&2
    exit 1
fi
