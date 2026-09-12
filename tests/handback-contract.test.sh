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

# NOTE: this file contains NO literal backticks, and no apostrophe inside a
# $()-nested heredoc — bash 3.2 fails to parse either one. Fixture bodies live inside
# $(fixture ... <<'EOF' ...), and a literal backtick there does not parse under
# bash 3.2 (the system bash on macOS). Markdown code spans are decoration in these
# fixtures — the predicate never reads them — so single quotes are used instead.

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
  local name="$1"                 # NOTE: separate statements — 'local a=$1 b=$TMP/$a' expands
  local f="$TMP/$name.jsonl"      # $a before assigning it, and 'set -u' then aborts the fixture,
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
# A bad fixture must be LOUD, and the loudness must survive. The first version
# incremented FAIL inside cont() while cont ran in a pipeline, so the increment
# died in the subshell and a broken fixture was reported as "did not block" —
# i.e. as a pass. Emit a sentinel on stdout instead and let the CALLER, which
# runs in the parent shell, record it.
# Build a fixture from a file instead of a $()-nested heredoc. bash 3.2 breaks on an
# APOSTROPHE inside $( ... <<'EOF' ... EOF ) exactly as it does on a backtick, and the
# contraction case below needs a real apostrophe — it IS the thing under test. A
# top-level `cat > file <<EOF` is not nested, so it parses.
fixture_file() {
  local name="$1"
  local src="$2"
  local f="$TMP/$name.jsonl"
  python3 -c '
import json,sys
print(json.dumps({"type":"assistant","uuid":sys.argv[1],
  "message":{"role":"assistant","content":[{"type":"text","text":open(sys.argv[2]).read()}]}}))' \
    "$name" "$src" > "$f"
  printf '%s' "$f"
}

cont() {
  if [ -z "${2:-}" ] || [ ! -s "$2" ]; then
    echo "HARNESS_ERROR fixture for '$1' is missing or empty ('${2:-}')"
    return 1
  fi
  echo "{\"session_id\":\"$1\",\"transcript_path\":\"$2\",\"stop_hook_active\":false}" | bash "$CONT"
}
_verdict() {                       # $1=sid $2=path $3=needle → 0 if present
  local out
  out="$(cont "$1" "$2")"
  case "$out" in
    *HARNESS_ERROR*) bad "harness: $out"; return 1 ;;
  esac
  printf '%s' "$out" | grep -q "$3"
}
blocks()     { _verdict "$1" "$2" '"decision": "block"'; }
handbacky()  { _verdict "$1" "$2" 'no answerable ask block'; }

# ── conforming messages (positive arm) ───────────────────────────────────────
CONFORMING_TABLE="$(fixture good_table <<'EOF'
## ⛔ Blocked on you — 2 items, ~2 min

| # | Ask | Unblocks | If you say nothing |
|---|---|---|---|
| 1 | **Merge 'workspace#403'** — 'gh pr merge 403 --squash'. Tests green. | the entry lands | it stays open |

## ✅ Shipped
Both PRs open. Neither merge is mine to make; I am blocked on the row above.
EOF
)"
CONFORMING_BULLET="$(fixture good_bullet <<'EOF'
### ⛔ Blocked on you — 1 item

- **Paste an API key into local env** — 'AI_GATEWAY_API_KEY'. That lane is blocked without it.
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
| 1 | **Pick one for 'bstack#102'.** A — split it *(suggested)*. B — keep reviewing. C — close it. | the fix shipping | I do A |

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
| 1 | **PROJ-1987** | Should publishing be open to any member, or approver-gated? | PR #630 |

If you answer nothing, I run the other lane.
EOF
)"
NO_DEFAULT="$(fixture bad_nodefault <<'EOF'
## ⛔ Blocked on you — 1 item

| # | Ask | Unblocks |
|---|---|---|
| 1 | **Merge 'workspace#403'** — needs a human. | the entry lands |
EOF
)"
NO_HEADING="$(fixture bad_noheading <<'EOF'
I am blocked on you for one thing.

- **Merge 'workspace#403'** — 'gh pr merge 403 --squash'.
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
echo "== harness self-check: a bad fixture is loud, not silently a pass =="
case "$(cont bogus-sid /nonexistent/path.jsonl)" in
  *HARNESS_ERROR*) ok "missing fixture surfaces HARNESS_ERROR (not a silent pass)" ;;
  *)               bad "missing fixture did NOT surface an error" ;;
esac

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
assert "def has_ask_block" in block, "predicate block did not include has_ask_block"

def build(mutation=None):
    ns = {"re": re}
    exec(block, ns)
    if mutation == "head":
        ns["ASK_HEAD_RE"] = re.compile(r"")          # always matches
    elif mutation == "imperative":
        ns["_imperative_cells"] = lambda cells: True # always true (both paths)
    elif mutation == "default":
        ns["DEFAULT_RE"] = re.compile(r"")           # always matches
    elif mutation == "nonempty":
        ns["_nonempty"] = lambda v: True             # an empty default counts
    # Return the SHIPPING has_ask_block, not a re-declaration of it. Python resolves
    # globals at call time, so overwriting ASK_HEAD_RE / _has_imperative_row /
    # DEFAULT_RE in ns mutates the real function. An earlier version re-declared a
    # 3-condition copy here; when the shipping predicate gained region scoping, the
    # mutation proofs silently began testing a function that no longer existed.
    return ns["has_ask_block"]

# each fixture is missing EXACTLY ONE condition
CASES = {
  "head":       "- **Merge PR 403** now.\nIf you say nothing, it stays open.",
  "imperative": "## ⛔ Blocked on you\n\n| # | Ask | Default |\n|---|---|---|\n| 1 | Should we ship? | I hold |",
  "default":    "## ⛔ Blocked on you\n\n- **Merge PR 403** — 'gh pr merge 403'.",
  "nonempty":   "## ⛔ Blocked on you\n\n| # | Ask | Default |\n|---|---|---|\n| 1 | **Merge PR 403.** | |",
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
if [ $? -eq 0 ]; then PASS=$((PASS+4)); else FAIL=$((FAIL+1)); fi

echo "== round-1 cross-model regressions =="

# BLOCKER: BLOCKER_RE matched any blocker-ish word, so a healthy receipt was refused.
INCIDENTAL="$(fixture incidental <<'EOF'
## Shipped

Deployment succeeded after CI was blocked briefly. All 412 tests pass, auto-merged,
worktree pruned, tree clean. Awaiting nothing; the next slice is the codec table.
EOF
)"
"$ARC" set inc-sid demo >/dev/null
blocks inc-sid "$INCIDENTAL" && bad "incidental 'blocked'/'awaiting' in a healthy receipt was refused" \
                             || ok "incidental blocker words do NOT trip the gate"

# BLOCKER: the three conditions were checked across the WHOLE message, so an
# imperative in the receipt plus a stray Default column faked an ask block.
FAKED="$(fixture faked <<'EOF'
## What I need from you

| Ticket | Question | Default |
|---|---|---|
| ABC-1 | Should we ship? | |

## Receipt

- Run tests: passed.

Stopping here, your call.
EOF
)"
"$ARC" set fake-sid demo >/dev/null
handbacky fake-sid "$FAKED" && ok "evidence scattered outside the ask block does NOT satisfy it" \
                            || bad "faked ask block slipped through (conditions not region-scoped)"

# R1 is part of the predicate: the ask block must LEAD.
NOTFIRST="$(fixture notfirst <<'EOF'
## What happened

A long narrative about the arc, the review rounds, and the corrections.

## Blocked on you

| # | Ask | If you say nothing |
|---|---|---|
| 1 | **Merge PR 403** | it stays open |
EOF
)"
"$ARC" set nf-sid demo >/dev/null
handbacky nf-sid "$NOTFIRST" && ok "an ask block that does not lead is refused (R1 enforced)" \
                             || bad "ask block behind a narrative heading was accepted"

# MAJOR: HANDBACK shared reconcile_count with the no-op path, so their caps interfered.
"$ARC" set ctr-sid demo >/dev/null
cont ctr-sid "$SENTINEL" >/dev/null            # consume one NO-OP block
handbacky ctr-sid "$NARRATIVE" && ok "a prior no-op block does not consume the handback budget" \
                               || bad "counters still shared — caps interfere across reasons"

# MAJOR: the imperative allowlist rejected contract language the skill endorses.
CLICKY="$(fixture clicky <<'EOF'
## Blocked on you — 1 item

| # | Ask | If you say nothing |
|---|---|---|
| 1 | **Click Approve on the Azure subscription request.** Only the owner can. | I leave it disabled |
EOF
)"
"$ARC" set click-sid demo >/dev/null
blocks click-sid "$CLICKY" && bad "'Click' rejected — allowlist still too narrow" \
                           || ok "'Click' is accepted as an imperative"

echo "== round-2 cross-model regressions =="

# BLOCKER: a NEGATED terminal phrase still tripped the trigger.
NEGATED="$(fixture negated <<'EOF'
## Shipped

This workflow no longer requires a human; deployment shipped and all tests pass.
Nothing here needs you. Merged, pruned, clean.
EOF
)"
"$ARC" set neg-sid demo >/dev/null
blocks neg-sid "$NEGATED" && bad "negated terminal phrasing still refused a healthy receipt" \
                          || ok "negated phrasing does NOT trip the gate"

# MAJOR: the imperative was allowed to BE the default.
IMP_IN_DEFAULT="$(fixture impdefault <<'EOF'
## ⛔ Blocked on you — 1 item

| # | Ask | If you say nothing |
|---|---|---|
| 1 | Should we ship? | Run the current build |

Stopping here, your call.
EOF
)"
"$ARC" set impdef-sid demo >/dev/null
handbacky impdef-sid "$IMP_IN_DEFAULT" && ok "an imperative in the DEFAULT cell does not make a row answerable" \
                                       || bad "imperative-as-default slipped through"

# MAJOR: a Default HEADER with an empty cell satisfied the default requirement.
EMPTY_DEFAULT="$(fixture emptydefault <<'EOF'
## ⛔ Blocked on you — 1 item

| # | Ask | Default |
|---|---|---|
| 1 | **Merge PR 403.** | |

Your call.
EOF
)"
"$ARC" set empty-sid demo >/dev/null
handbacky empty-sid "$EMPTY_DEFAULT" && ok "an empty default cell is not a default" \
                                     || bad "empty default cell slipped through"

# MAJOR: handback_count was never reset, so the consecutive cap became a lifetime cap.
"$ARC" set rst-sid demo >/dev/null
handbacky rst-sid "$NARRATIVE" >/dev/null            # nudge 1
cont rst-sid "$HEALTHY" >/dev/null                   # a productive turn resets it
handbacky rst-sid "$NARRATIVE" && ok "a productive turn resets the handback cap (consecutive, not lifetime)" \
                               || bad "handback cap never resets — it is a lifetime cap"

# And the counters must stay isolated from each other.
[ "$("$ARC" get rst-sid reconcile_count)" = "0" ] \
  && ok "handback blocks do not increment reconcile_count" \
  || bad "counters still bleed into each other"

# Self-found while probing: a healthy message QUOTING a bad terminal message (a
# template, a worked example, a review finding) tripped the trigger on the quote.
FENCED="$(fixture fenced <<'EOF'
## Shipped

Added the handback template. The bad shape it replaces looks like this:

~~~
Stopping here, your call. Blocked on you for the merge.
~~~

All 412 tests pass, auto-merged, tree clean.
EOF
)"
"$ARC" set fence-sid demo >/dev/null
blocks fence-sid "$FENCED" && bad "a quoted bad message inside a code fence tripped the gate" \
                           || ok "fenced quotations do not trip the trigger"

echo "== round-3 cross-model regressions =="

# BLOCKER: the contraction alternative could never match, because in a word like
# doesnt the n follows a word character and the leading word-boundary fails.
cat > "$TMP/contraction.txt" <<'EOF'
## Shipped

This workflow doesn't require a human; deployment shipped and all tests pass.
Merged, pruned, clean.
EOF
CONTRACTION="$(fixture_file contraction "$TMP/contraction.txt")"
"$ARC" set contr-sid demo >/dev/null
blocks contr-sid "$CONTRACTION" && bad "a contraction negation still force-blocked a healthy receipt" \
                                || ok "contraction negations are recognised"

# MAJOR: all tables in the region were flattened, so a second table's rows were read
# against the FIRST table's header and default column.
TWO_TABLES="$(fixture twotables <<'EOF'
## Blocked on you — 1 item

| # | Ask | Default |
|---|---|---|
| 1 | Should we ship? | |

| Check | Status |
|---|---|
| Merge gate | green |

Stopping here, your call.
EOF
)"
"$ARC" set two-sid demo >/dev/null
handbacky two-sid "$TWO_TABLES" && ok "a second table cannot supply the first table's missing default" \
                                || bad "cross-table row leakage still fakes an answerable ask"

# MAJOR: reset accepted any field, including the lifetime runaway ceiling.
"$ARC" set rs-sid demo >/dev/null
"$ARC" try-block rs-sid 1 5 handback_count >/dev/null
"$ARC" reset rs-sid total_blocks >/dev/null 2>&1
[ "$("$ARC" get rs-sid total_blocks)" = "1" ] \
  && ok "reset refuses total_blocks (the lifetime ceiling holds)" \
  || bad "total_blocks was reset — the runaway backstop can be cleared"

echo
echo "handback-contract: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
