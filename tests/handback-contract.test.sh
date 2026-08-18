#!/usr/bin/env bash
# handback-contract.test.sh — BRO-2179. The Stop hook must refuse a terminal turn
# that halts the arc on a human but asks them nothing answerable.
#
# Both polarity arms are mandatory. A rejector that rejects everything passes the
# negative arm and is worthless; the positive arm is what makes the negative one
# mean something. Each of the three conditions in has_ask_block() is then
# mutation-proved: gut it, and a fixture that differs ONLY in that condition must
# flip verdict. A condition whose mutant does not flip is decoration.
set -uo pipefail

BSTACK_REPO="$(cd "$(dirname "$0")/.." && pwd)"
S="$BSTACK_REPO/scripts"
PASS=0; FAIL=0
ok()  { echo "  ok   $1"; PASS=$((PASS+1)); }
bad() { echo "  FAIL $1"; FAIL=$((FAIL+1)); }

TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
export BROOMVA_AUTONOMOUS_HOME="$TMP/arcs"
export ARC_DRAIN_MS=150
ARC="$S/autonomous-arc.sh"
CONT="$S/arc-continuation-hook.sh"

# fixture <name> <text-on-stdin> → path to a one-turn transcript (JSON-safe)
fixture() {
  local name="$1"                 # NOTE: separate statements — `local a=$1 b=$TMP/$a` expands
  local f="$TMP/$name.jsonl"      # $a before assigning it, and `set -u` then aborts the fixture,
                                  # which silently made every "does not block" assertion vacuous.
  python3 -c '
import json,sys
text=sys.stdin.read()
print(json.dumps({"type":"assistant","uuid":sys.argv[1],
  "message":{"role":"assistant","content":[{"type":"text","text":text}]}}))' "$name" > "$f"
  printf '%s' "$f"
}
# Guard: an empty/absent transcript makes the hook exit 0, which would make every
# "must NOT block" assertion pass without testing anything. Fail loudly instead.
cont() {
  if [ -z "${2:-}" ] || [ ! -s "$2" ]; then
    echo "  FAIL harness: fixture for '$1' is missing or empty ('${2:-}')" >&2
    FAIL=$((FAIL+1)); return 1
  fi
  echo "{\"session_id\":\"$1\",\"transcript_path\":\"$2\",\"stop_hook_active\":false}" | bash "$CONT"
}
blocks()     { cont "$1" "$2" | grep -q '"decision": "block"'; }
handbacky()  { cont "$1" "$2" | grep -q 'no answerable ask block'; }

# ── conforming messages (positive arm) ───────────────────────────────────────
CONFORMING_TABLE="$(fixture good_table <<'EOF'
## ⛔ Blocked on you — 2 items, ~2 min

| # | Ask | Unblocks | If you say nothing |
|---|---|---|---|
| 1 | **Merge `workspace#403`** — `gh pr merge 403 --squash`. Tests green. | the entry lands | it stays open |

## ✅ Shipped
Both PRs open. Neither merge is mine to make; I am blocked on the row above.
EOF
)"
CONFORMING_BULLET="$(fixture good_bullet <<'EOF'
### ⛔ Blocked on you — 1 item

- **Paste an API key into local env** — `AI_GATEWAY_API_KEY`. That lane is blocked without it.
  If you say nothing, I skip it and finish the other three lanes.
EOF
)"
CONFORMING_WINFY="$(fixture good_winfy <<'EOF'
## What I need from you

Stopping here — one lane is blocked on you.

| # | Ask | Default |
|---|---|---|
| 1 | **Decide: ship behind a flag, or hold for review?** I suggest the flag. | I ship behind the flag |
EOF
)"
CONFORMING_NUM="$(fixture good_num <<'EOF'
## ⛔ Blocked on you — 3 of 11 shown

1. **Approve the Azure subscription re-enable.** Only the owner can; the drill is blocked.
2. **Pick one for the schema drop.** A: keep it. B: drop it. I suggest A.

If you answer nothing, I do A and leave the subscription alone.
EOF
)"
CONFORMING_OPTS="$(fixture good_opts <<'EOF'
## ⛔ Blocked on you — 1 item, ~1 min

| # | Ask | Unblocks | If you say nothing |
|---|---|---|---|
| 1 | **Pick one for `bstack#102`.** A — split it *(suggested)*. B — keep reviewing. C — close it. | the fix shipping | I do A |

Your call on that one; everything else is done.
EOF
)"

# ── non-conforming (negative arm) — real shapes from the measured corpus ─────
NARRATIVE="$(fixture bad_narrative <<'EOF'
Both PRs open, CI green, CLEAN. Stopping here — neither merge is mine to make.

## Delivered

bstack#102 — 5 commits, 41/41 suite, CI green. Not merged; auto-merge correctly
blocked because the branch class requires a higher review score than it has.

## Your call

Two decisions are yours. bstack#102 could be split — land the core, defer the
hardening. workspace#403 just needs a merge.
EOF
)"
# Shape of the single best message in the measured corpus: right heading, right
# leverage column, but the rows are OPEN QUESTIONS carried by a ticket ID rather
# than imperatives — so it still fails R2/R6 and must still be refused.
QUESTION_NO_IMPERATIVE="$(fixture bad_question <<'EOF'
## What I need from you

Stopping here — three lanes are blocked and one needs access I cannot hold.

| # | Ticket | The question | Unblocks |
|---|---|---|---|
| 1 | **STI-1987** | Should publishing be open to any member, or approver-gated? | PR #630 |

If you answer nothing, I run the other lane.
EOF
)"
NO_DEFAULT="$(fixture bad_nodefault <<'EOF'
## ⛔ Blocked on you — 1 item

| # | Ask | Unblocks |
|---|---|---|
| 1 | **Merge `workspace#403`** — needs a human. | the entry lands |
EOF
)"
NO_HEADING="$(fixture bad_noheading <<'EOF'
I am blocked on you for one thing.

- **Merge `workspace#403`** — `gh pr merge 403 --squash`.
  If you say nothing, it stays open.
EOF
)"
HEALTHY="$(fixture healthy <<'EOF'
## Shipped

Refactored the parser and landed PR #501. All 412 tests pass, CI green,
auto-merged, worktree pruned, tree clean. Next slice is the codec table.
EOF
)"

# ── harness control ──────────────────────────────────────────────────────────
# Every "must NOT block" assertion below is unfalsifiable unless the harness can
# produce a block at all. Prove it can, using the pre-existing no-op sentinel.
echo "== harness control: the rig can produce a block =="
SENTINEL="$(fixture ctl_sentinel <<'EOF'
No response requested.
EOF
)"
"$ARC" set ctl-sid demo >/dev/null
blocks ctl-sid "$SENTINEL" && ok "control: no-op sentinel blocks (rig is live)" \
                           || bad "control: rig CANNOT block — every negative assertion below is vacuous"

echo "== positive arm: a conforming handback must NOT be blocked =="
for f in "$CONFORMING_TABLE:table" "$CONFORMING_BULLET:bullet" "$CONFORMING_WINFY:what-i-need" \
         "$CONFORMING_NUM:numbered" "$CONFORMING_OPTS:options"; do
  path="${f%%:*}"; label="${f##*:}"
  sid="pos-$label"; "$ARC" set "$sid" demo >/dev/null
  blocks "$sid" "$path" && bad "conforming ($label) was BLOCKED — gate fights a correct message" \
                        || ok "conforming ($label) passes"
done

# KNOWN GAP, stated rather than hidden: the predicate is GATED on BLOCKER_RE, so a
# terminal message that strands the arc on a human WITHOUT using any blocker word
# ("blocked", "your call", "stopping here", "needs you", ...) is not caught. Widening
# the trigger to all terminal turns would invert this hook'"'"'s bias-to-safety, so the
# gap is accepted: a miss costs one manual nudge, a false block fights the operator.
echo "== negative arm: blocker language with no answerable ask must be blocked =="
for f in "$NARRATIVE:narrative-your-call" "$QUESTION_NO_IMPERATIVE:open-question-ticket-carrier" \
         "$NO_DEFAULT:no-default" "$NO_HEADING:no-heading"; do
  path="${f%%:*}"; label="${f##*:}"
  sid="neg-$label"; "$ARC" set "$sid" demo >/dev/null
  handbacky "$sid" "$path" && ok "non-conforming ($label) blocked with the handback reason" \
                           || bad "non-conforming ($label) slipped through"
done

echo "== must not fire on a healthy yield with no blocker language =="
"$ARC" set healthy-sid demo >/dev/null
blocks healthy-sid "$HEALTHY" && bad "healthy substantive turn was blocked" || ok "healthy turn passes"

echo "== must not fire when no arc is active =="
NOARC="$TMP/none"; rm -rf "$BROOMVA_AUTONOMOUS_HOME/noarc-sid.json" 2>/dev/null
blocks noarc-sid "$NARRATIVE" && bad "blocked with no active arc" || ok "no active arc → never blocks"

echo "== consecutive cap: one rewrite nudge, never a loop =="
"$ARC" set cap-sid demo >/dev/null
handbacky cap-sid "$NARRATIVE" && ok "first handback nudge blocks" || bad "first nudge did not block"
blocks cap-sid "$NARRATIVE" && bad "second consecutive handback blocked — cap is not 1" \
                            || ok "second consecutive handback does NOT block (HANDBACK_CONSEC_MAX=1)"

echo "== mutation proofs: each condition in has_ask_block must be load-bearing =="
python3 - "$S/arc-continuation-hook.sh" <<'PYMUT'
import re, sys
src = open(sys.argv[1]).read()
block = src[src.index("BLOCKER_RE = re.compile("):src.index("# DRAIN by identity")]

def build(mutation=None):
    ns = {"re": re}
    exec(block, ns)
    if mutation == "head":
        ns["ASK_HEAD_RE"] = re.compile(r"")          # always matches
    elif mutation == "imperative":
        ns["_has_imperative_row"] = lambda t: True   # always true
    elif mutation == "default":
        ns["DEFAULT_RE"] = re.compile(r"")           # always matches
    # rebind has_ask_block against the mutated namespace
    exec("""
def has_ask_block(text):
    return bool(ASK_HEAD_RE.search(text)) and _has_imperative_row(text) and bool(DEFAULT_RE.search(text))
""", ns)
    return ns["has_ask_block"]

# each fixture is missing EXACTLY ONE condition
CASES = {
  "head":       "- **Merge PR 403** now.\nIf you say nothing, it stays open.",
  "imperative": "## ⛔ Blocked on you\n\n| # | Ask | Default |\n|---|---|---|\n| 1 | Should we ship? | I hold |",
  "default":    "## ⛔ Blocked on you\n\n- **Merge PR 403** — `gh pr merge 403`.",
}
base = build()
fails = 0
for cond, text in CASES.items():
    if base(text):
        print(f"  FAIL mutation setup: baseline already ACCEPTS the {cond}-missing fixture"); fails += 1; continue
    if build(cond)(text):
        print(f"  ok   {cond}: gutting it flips REJECT -> ACCEPT (condition is load-bearing)")
    else:
        print(f"  FAIL {cond}: mutant still rejects — condition is decoration, not a check"); fails += 1
sys.exit(1 if fails else 0)
PYMUT
if [ $? -eq 0 ]; then PASS=$((PASS+3)); else FAIL=$((FAIL+1)); fi

echo
echo "handback-contract: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
