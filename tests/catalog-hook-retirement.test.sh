#!/usr/bin/env bash
# tests/catalog-hook-retirement.test.sh — BRO-2021.
#
# knowledge-catalog-refresh-hook.sh is RETIRED. It resolved bookkeeping.py
# workspace-relative (`$REPO_ROOT/skills/bookkeeping/scripts/bookkeeping.py`)
# while bookkeeping installs GLOBALLY via `npx skills add -g` — so on every
# workspace that did not vendor bookkeeping in-tree it was a silent no-op that
# still cost a process spawn at every session end. Its one real job (regenerate
# docs/knowledge-index.md AFTER the session is captured) now lives in the
# conversation bridge's background chain, where the ordering is a sequence rather
# than two hooks racing on two cooldowns.
#
# A retirement is only durable if the installers stop resurrecting it. bootstrap
# and repair both re-deploy any hook in their list and re-wire any hook in the
# snippet, so this test pins BOTH ends: the hook must not come back, and the
# capability must still be there.
#
# Asserts:
#   1. bstack ships no knowledge-catalog-refresh-hook.sh
#   2. the settings snippet wires no catalog hook
#   3. bootstrap neither wires nor deploys it
#   4. repair --apply-all does not resurrect it (the resurrection vector)
#   5. the shipped bridge carries the capability AND resolves bookkeeping globally
#   6. the bridge ACTUALLY runs `bookkeeping index` (executed, not just grepped)
#   7. doctor asserts the CAPABILITY, satisfied by the bridge
#   8. a workspace without the capability gets info, never a false gap
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PASS=0; FAIL=0; FAILED=()
pass() { echo "  [ok] $1"; PASS=$((PASS + 1)); }
fail() { echo "  [FAIL] $1"; FAIL=$((FAIL + 1)); FAILED+=("$1"); }

echo "── catalog-hook retirement (BRO-2021) ─────────────────"

HOOK=knowledge-catalog-refresh-hook.sh

# ── 1 + 2. nothing ships or wires it ────────────────────────────────────────
if [ ! -e "$REPO/scripts/$HOOK" ]; then
    pass "1. bstack ships no scripts/$HOOK"
else
    fail "1. scripts/$HOOK is still shipped"
fi
if ! grep -q "$HOOK" "$REPO/assets/templates/settings.json.snippet"; then
    pass "2. settings.json.snippet wires no catalog hook"
else
    fail "2. settings.json.snippet still wires the catalog hook"
fi

# ── 3. bootstrap does not resurrect it ──────────────────────────────────────
TW=$(mktemp -d); TH=$(mktemp -d)
trap 'rm -rf "$TW" "$TH"' EXIT
( cd "$TW" && git init -q && git -c user.email=t@t -c user.name=t commit -q --allow-empty -m init )
HOME="$TH" BROOMVA_WORKSPACE="$TW" BSTACK_SKIP_SKILLS=1 BSTACK_SKIP_RCS=1 \
    bash "$REPO/scripts/bootstrap.sh" >/dev/null 2>&1
SETTINGS="$TW/.claude/settings.json"
if [ -f "$SETTINGS" ] && ! grep -q "$HOOK" "$SETTINGS" && [ ! -e "$TW/scripts/$HOOK" ]; then
    pass "3. bootstrap neither wires nor deploys the catalog hook"
else
    fail "3. bootstrap resurrected the catalog hook (wired=$(grep -c "$HOOK" "$SETTINGS" 2>/dev/null), deployed=$([ -e "$TW/scripts/$HOOK" ] && echo yes || echo no))"
fi

# ── 4. repair does not resurrect it — the vector that undoes a merged deletion ─
HOME="$TH" BROOMVA_WORKSPACE="$TW" bash "$REPO/scripts/repair.sh" --apply-all >/dev/null 2>&1 || true
if ! grep -q "$HOOK" "$SETTINGS" && [ ! -e "$TW/scripts/$HOOK" ]; then
    pass "4. repair --apply-all does not resurrect the catalog hook"
else
    fail "4. repair resurrected the catalog hook (wired=$(grep -c "$HOOK" "$SETTINGS" 2>/dev/null), deployed=$([ -e "$TW/scripts/$HOOK" ] && echo yes || echo no))"
fi

# ── 5. the capability moved into the bridge, with GLOBAL resolution ─────────
BRIDGE="$TW/scripts/conversation-bridge-hook.sh"
if [ -f "$BRIDGE" ] && grep -qE 'bookkeeping[^\n]*index|BOOKKEEPING" index' "$BRIDGE" \
   && grep -q '.claude/skills/bookkeeping/scripts/bookkeeping.py' "$BRIDGE" \
   && grep -q '.agents/skills/bookkeeping/scripts/bookkeeping.py' "$BRIDGE"; then
    pass "5. deployed bridge runs the index AND resolves bookkeeping from the global skill dirs"
else
    fail "5. the deployed bridge lacks the catalog capability or the global resolution"
fi

# ── 6. it actually FIRES (executed, not grepped) ────────────────────────────
# Stand bookkeeping up where `npx skills add -g` puts it, have it record its
# argv, run the bridge, and wait for the background chain.
mkdir -p "$TH/.claude/skills/bookkeeping/scripts"
cat > "$TH/.claude/skills/bookkeeping/scripts/bookkeeping.py" <<'PY'
import sys, pathlib, os
pathlib.Path(os.environ["BK_MARKER"]).write_text(" ".join(sys.argv[1:]) + "\n")
PY
MARKER="$TW/.bookkeeping-called"
rm -f "$HOME/.cache/bstack-bridge-stamp" 2>/dev/null || true
echo '{"session_id":"t","transcript_path":"/dev/null"}' \
  | HOME="$TH" BK_MARKER="$MARKER" BROOMVA_WORKSPACE="$TW" CLAUDE_PROJECT_DIR="$TW" \
    bash "$BRIDGE" >/dev/null 2>&1
for _ in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15; do
    [ -f "$MARKER" ] && break
    sleep 0.2
done
if [ -f "$MARKER" ] && grep -q '^index' "$MARKER"; then
    pass "6. the bridge actually invoked 'bookkeeping index' ($(cat "$MARKER" | tr -d '\n'))"
else
    fail "6. the bridge did not invoke bookkeeping index (marker=$([ -f "$MARKER" ] && cat "$MARKER" || echo absent))"
fi

# ── 7. doctor asserts the CAPABILITY, satisfied by the bridge ───────────────
DOC=$(HOME="$TH" BROOMVA_WORKSPACE="$TW" BSTACK_DOCTOR_USER_SETTINGS="$TH/none.json" \
      bash "$REPO/scripts/doctor.sh" 2>&1)
if echo "$DOC" | grep -q '\[ok\] P6 catalog capability: Stop hook regenerates the catalog'; then
    pass "7. doctor: capability satisfied by the bridge (no implementation named)"
else
    fail "7. doctor did not recognize the bridge as satisfying the catalog capability:"$'\n'"$(echo "$DOC" | grep -i catalog)"
fi
if echo "$DOC" | grep -q 'P6 catalog hook missing'; then
    fail "7b. doctor still gaps on the retired hook by name — the false gap is back"
else
    pass "7b. doctor never gaps on the retired hook by name"
fi

# ── 8. no capability → info, never a false gap ─────────────────────────────
GAPS_WITH=$(echo "$DOC" | sed -n 's/^\[bstack doctor\].*, \([0-9]*\) gap(s).*/\1/p'); GAPS_WITH=${GAPS_WITH:-0}
python3 - "$BRIDGE" <<'PY'
import re, sys
p = sys.argv[1]
t = open(p).read()
open(p, "w").write(re.sub(r'^.*bookkeeping.*index.*$', '', t, flags=re.M | re.I))
PY
DOC2=$(HOME="$TH" BROOMVA_WORKSPACE="$TW" BSTACK_DOCTOR_USER_SETTINGS="$TH/none.json" \
       bash "$REPO/scripts/doctor.sh" 2>&1)
GAPS_WITHOUT=$(echo "$DOC2" | sed -n 's/^\[bstack doctor\].*, \([0-9]*\) gap(s).*/\1/p'); GAPS_WITHOUT=${GAPS_WITHOUT:-0}
if echo "$DOC2" | grep -q '\[info\] P6 catalog capability: no Stop hook statically resolves' \
   && [ "$GAPS_WITH" = "$GAPS_WITHOUT" ]; then
    pass "8. missing capability is info, not a gap (gaps $GAPS_WITH = $GAPS_WITHOUT)"
else
    fail "8. missing capability changed the gap total ($GAPS_WITH → $GAPS_WITHOUT) or printed no info"
fi

echo "─────────────────────────────────────"
echo "  Passed: $PASS"
echo "  Failed: $FAIL"
if [ "$FAIL" -gt 0 ]; then
    for t in "${FAILED[@]}"; do echo "    - $t"; done
    exit 1
fi
echo "  catalog-hook-retirement passed."
