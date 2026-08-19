#!/usr/bin/env bash
# arc-continuation-hook.sh — Stop hook. THE machine-checkable core of BRO-1700
# (loop-stall rejection, disturbances #3 "No response requested." and #4
# parent-never-resumes). When an autonomous arc is active and the agent's final
# turn is an UNAMBIGUOUS no-op terminal, returns a Stop-hook block decision so the
# harness continues the arc instead of parking on a dead turn.
#
# DESIGN: bias-to-safety. The two failure modes are asymmetric — a false positive
# (force-continue a legitimate stop) fights the user and burns tokens; a false
# negative (miss a stall) just costs one manual nudge (the status quo). So this
# blocks ONLY on the two unambiguous no-ops — an empty final turn, or the literal
# CC sentinel "No response requested." as the whole message — and accepts every
# false negative. Bounded by BOTH a consecutive cap (reconcile_count<2, reset on a
# productive turn) AND a lifetime cap (total_blocks<5, never reset) so an
# interleaved-trivial-tool loop still terminates. Honors CC's stop_hook_active.
#
# Transcript race (BRO-1616): CC flushes the final assistant entry ~125ms AFTER
# Stop fires, so a single read judges the PREVIOUS turn. We drain by IDENTITY —
# poll until an assistant entry with a *different* signature than the one present
# at hook start appears (not a timestamp window, which misfires on <2s-apart turns).
set -uo pipefail

SELF_DIR="$(cd "$(dirname "$0")" 2>/dev/null && pwd)"
ARC_HELPER="$SELF_DIR/autonomous-arc.sh"
INPUT="$(cat 2>/dev/null || echo '{}')"
CONSEC_MAX=2
LIFE_MAX=5
# HANDBACK (BRO-2179) is a broader trigger than the two no-op sentinels, so it gets
# its own, tighter consecutive cap: one rewrite nudge, never a loop. Its asymmetry is
# the same as the rest of this hook — a false accept costs nothing (status quo), a
# false block fights the user — so the ask-block predicate is deliberately generous.
HANDBACK_CONSEC_MAX=1

command -v python3 >/dev/null 2>&1 || exit 0
[ -x "$ARC_HELPER" ] || exit 0

# session_id (l1) + transcript_path (l2) + stop_hook_active (l3)
{ read -r SID; read -r TRANSCRIPT; read -r STOP_ACTIVE; } < <(python3 - "$INPUT" <<'PY'
import sys, json
try:
    d = json.loads(sys.argv[1])
except Exception:
    d = {}
print(d.get("session_id") or d.get("sessionId") or "")
print(d.get("transcript_path") or d.get("transcriptPath") or "")
print("1" if d.get("stop_hook_active") or d.get("stopHookActive") else "0")
PY
)

[ -n "${SID:-}" ] || exit 0
[ -n "${TRANSCRIPT:-}" ] && [ -f "$TRANSCRIPT" ] || exit 0
"$ARC_HELPER" active "$SID" >/dev/null 2>&1 || exit 0   # arc active + not stale

VERDICT="$(python3 - "$TRANSCRIPT" <<'PY'
import sys, json, re, time, os, hashlib

path = sys.argv[1]
BUDGET = float(os.environ.get("ARC_DRAIN_MS", "1200")) / 1000.0
INTERVAL = 0.05

SENTINEL_RE = re.compile(r"^\s*no response requested[.\s]*$", re.I)   # whole-message only
# completion = the WHOLE final message is a completion phrase (fully anchored ^…$,
# modulo trailing punctuation). Neither a mid-arc mention ("the first task is complete,
# moving on") nor a keep-going clause ("task complete; continuing with the next slice"
# — a CodeRabbit finding) releases the arc; those keep loop-stall protection active.
COMPLETE_RE = re.compile(
    r"^\s*(the\s+)?(arc (is )?complete|arc[- ]done|"
    r"all milestones?\b.{0,40}?\b(shipped|done|complete)|milestones? complete|"
    r"task complete|all done|everything(?:'s| is)?\s+(?:shipped|done|merged|complete))"
    r"[.!\s]*$", re.I)

# -- handback contract (BRO-2179) ---------------------------------------------
# NO LITERAL BACKTICKS BELOW. This python is inside a $()-nested quoted heredoc,
# where bash 3.2 (the system bash) fails to parse one. Write \x60 in regexes and
# single quotes in prose. tests/bash32-parse-safety is the gate; it has now caught
# this twice in this file, the second time in the comment explaining the first.
# Measured over 100 long sessions: 18 HALTED THE ARC ON A HUMAN (terminal-stance
# phrasing) and not one of the 18 carried an ask block a person could act on. A
# broader regex flags 31, but 13 of those merely MENTION being blocked ("the
# pre-commit hook blocked it") and are healthy receipts. This predicate refuses a
# terminal turn that halts on a human but does not ASK them anything answerable.
# Deliberately narrow: only TERMINAL-STANCE phrasing, never an incidental mention.
# The first draft matched bare "blocked"/"awaiting"/"escalat", so a healthy receipt
# ("deployment succeeded after CI was blocked briefly") would have been refused —
# a Stop hook that fights the operator on a good turn. Found by cross-model review.
BLOCKER_RE = re.compile(
    r"(blocked on (?:you|a human|the operator|your)|what i need from you|your call|"
    r"stopping here|not mine to (?:make|merge)|only you can|"
    r"needs? (?:you|your)\b|await(?:ing)? (?:your|you|a human|an? [\w-]+ from you)|"
    r"requires? (?:a )?human|(?:can'?t|cannot|unable to) proceed without|"
    r"escalat\w* to (?:you|the user|the operator))", re.I)
# "This workflow no longer requires a human" matched requires a human and refused a
# healthy receipt. A trigger is only a trigger if nothing negates it just before.
# \bnt\b could never fire: in "doesnt" the n follows a word character, so the
# leading \b fails and every contraction slipped through. Written as an explicit
# alternation with no leading boundary.
NEGATOR_RE = re.compile(
    r"(\bno longer\b|\bnot\b|\bnever\b|\bwithout\b|\bnothing\b|\bnone of\b|"
    r"n['\u2019]t\b|\bcannot\b|\bno\b)[^.;:\n]{0,40}$", re.I)

FENCE_RE = re.compile(r"^\s{0,3}(\x60{3}|~~~).*?^\s{0,3}\1", re.M | re.S)   # \x60 = backtick; never literal (bash 3.2)

def blocker_stance(text):
    """True only for a NON-negated terminal-stance phrase outside a code fence.

    Fenced content is stripped first: a healthy message that QUOTES a bad terminal
    message (a template, a worked example, a review finding) would otherwise trip
    the trigger on the quotation. Self-found while probing, not reported.
    """
    body = FENCE_RE.sub(" ", text)
    for m in BLOCKER_RE.finditer(body):
        if not NEGATOR_RE.search(body[max(0, m.start() - 60):m.start()]):
            return True
    return False
ASK_HEAD_RE = re.compile(
    r"^\s{0,3}#{1,4}[^\n]*?(⛔|blocked on you|what i need from you)", re.I | re.M)
DEFAULT_RE = re.compile(
    r"if you (?:say|answer|do|reply) nothing|^\|[^\n]*\bdefault\b[^\n]*\|", re.I | re.M)
# Cell-level twin. DEFAULT_RE is line-anchored (^\|...\|) so it can never match a bare
# header CELL like "Default" — using it to identify the default column silently
# returned None for every table, which made even conforming messages unanswerable.
DEFAULT_COL_RE = re.compile(r"if you (?:say|answer|do|reply) nothing|\bdefault\b", re.I)
# Generous on purpose: a missed imperative means we do NOT block, which is the safe arm.
IMPERATIVES = {
    "run", "merge", "approve", "decide", "choose", "pick", "confirm", "paste",
    "provide", "grant", "answer", "reply", "set", "publish", "enable", "install",
    "tell", "send", "create", "open", "close", "review", "sign", "add", "remove",
    "click", "select", "upload", "rotate", "assign", "verify", "check", "unblock",
    "restore", "delete", "disable", "invite", "authorize", "share", "export",
}

def _lead_word(cell):
    # NOTE: the backtick below is written \x60, never literally. This python lives
    # inside a $()-nested quoted heredoc, where a literal backtick does not parse
    # under bash 3.2 — the system bash this hook actually runs on. Caught by
    # tests/bash32-parse-safety; it would have shipped a hook that fails to parse.
    c = re.sub(r"[*_\x60\[\]()#>~]", " ", cell)        # strip markdown emphasis / links
    c = re.sub(r"^\s*\d+[.)]?\s*", "", c)           # strip leading numbering
    m = re.match(r"\s*([A-Za-z']+)", c)
    return m.group(1).lower() if m else ""

def _imperative_cells(cells):
    """Any of these cells leads with an imperative. Single mutation surface for both
    the table and bullet paths, so a mutation proof covers both."""
    return any(_lead_word(c) in IMPERATIVES for c in cells)

def _nonempty(value):
    """A Default COLUMN with an empty cell is not a default. Factored out so the rule
    has its own mutation proof."""
    return bool(str(value).strip())

def _has_imperative_row(text):
    for ln in text.splitlines():
        t = ln.strip()
        if t.startswith("|"):
            if _imperative_cells(t.strip("|").split("|")):
                return True
        elif re.match(r"^([-*+]|\d+[.)])\s", t):
            body = t[1:] if t[:1] in "-*+" else t
            if _imperative_cells([body]):
                return True
    return False

def _tables(block):
    """Every markdown table in the block, each as its own [header, *rows].

    Flattening them into one list let a second table's rows be read against the FIRST
    table's header and default column, so 'Ask | Default' followed by an unrelated
    'Check | Status' table could fake an answerable row.
    """
    tables, cur = [], []
    for ln in block.splitlines():
        t = ln.strip()
        if not t.startswith("|"):
            if cur:
                tables.append(cur); cur = []
            continue
        cells = [c.strip() for c in t.strip("|").split("|")]
        if all(set(c) <= set("-: ") for c in cells):
            continue                                  # separator row
        cur.append(cells)
    if cur:
        tables.append(cur)
    return tables

def _default_col(header):
    for i, c in enumerate(header):
        if DEFAULT_COL_RE.search(c):
            return i
    return None

def _answerable(block):
    """At least one row a person can actually act on.

    Table form: an imperative leads a cell that is NOT the default column, AND that
    same row's default cell is non-empty. Checking the two independently let
    '| Should we ship? | Run the current build |' pass on an imperative that was the
    default, and '| **Merge PR 403.** | |' pass on an empty default. Both were
    supplied by cross-model review.
    """
    tables = _tables(block)
    if tables:
        # AUTHORITATIVE when a table exists — no falling through to the looser prose
        # rule. Falling through re-opened both holes this rule closes, because the
        # prose rule finds an imperative in ANY cell and matches a Default header
        # anywhere in the block. Each table is judged against its OWN header.
        for tbl in tables:
            header, data = tbl[0], tbl[1:]
            dcol = _default_col(header)
            for r in data:
                dval = r[dcol] if (dcol is not None and dcol < len(r)) else ""
                others = [c for i, c in enumerate(r) if i != dcol]
                if _imperative_cells(others) and _nonempty(dval):
                    return True
        return False
    # Bullet / prose form: an imperative bullet plus an explicit silence clause.
    return _has_imperative_row(block) and bool(DEFAULT_RE.search(block))

def _ask_block(text):
    """The ask-block REGION: the ask heading through to the next heading of the same or
    higher level (or EOF), or None.

    Scoping matters. Checking the three conditions across the whole message lets an
    imperative in the receipt ("Run tests: passed") and a stray "Default" column
    elsewhere combine to fake an ask block that asks nothing. Cross-model review
    produced exactly that input. R1 is enforced here too: if any heading precedes the
    ask heading, the ask is not first, and the whole point was that it leads.
    """
    m = ASK_HEAD_RE.search(text)
    if not m:
        return None
    if re.search(r"^\s{0,3}#{1,6}\s", text[:m.start()], re.M):
        return None                                   # a heading came first
    hashes = re.match(r"^\s{0,3}(#+)", text[m.start():])
    level = len(hashes.group(1)) if hashes else 2
    rest = text[m.end():]
    nxt = re.search(r"^\s{0,3}#{1,%d}\s" % level, rest, re.M)
    return rest[:nxt.start()] if nxt else rest

def has_ask_block(text):
    """A leading ask block containing at least one row a person can act on."""
    block = _ask_block(text)
    if block is None:
        return False
    return _answerable(block)

def last_assistant(p):
    try:
        with open(p, "rb") as f:
            f.seek(0, 2); size = f.tell()
            f.seek(max(0, size - 1048576))   # 1MB tail — holds even a large thinking block
            data = f.read().decode("utf-8", "replace")
    except OSError:
        return None
    last = None
    for ln in data.splitlines():
        ln = ln.strip()
        if not ln:
            continue
        try:
            o = json.loads(ln)
        except Exception:
            continue
        role = o.get("type") or (o.get("message") or {}).get("role") or o.get("role")
        if role == "assistant":
            last = o
    return last

def sig(entry):
    if entry is None:
        return None
    for k in ("uuid", "id", "messageId", "requestId"):
        v = entry.get(k)
        if v:
            return "id:" + str(v)
    msg = entry.get("message") if isinstance(entry.get("message"), dict) else entry
    body = json.dumps(msg.get("content"), sort_keys=True, default=str)[:800]
    return "h:" + hashlib.md5(body.encode()).hexdigest()

def parse_entry(entry):
    # returns (has_tool_use, has_thinking, text). CC writes each extended-thinking
    # block as its OWN assistant entry (content=[{type:thinking}]) emitted BEFORE the
    # turn's text/tool — a thinking-only entry is NOT an empty no-op and must never
    # be force-continued.
    msg = entry.get("message") if isinstance(entry.get("message"), dict) else entry
    content = msg.get("content")
    has_tool = has_think = False
    parts = []
    if isinstance(content, list):
        for b in content:
            if not isinstance(b, dict):
                continue
            t = b.get("type")
            if t == "tool_use":
                has_tool = True
            elif t in ("thinking", "redacted_thinking"):
                has_think = True
            elif t == "text":
                parts.append(b.get("text", ""))
    elif isinstance(content, str):
        parts.append(content)
    return has_tool, has_think, " ".join(p for p in parts if p).strip()

# DRAIN by identity: wait for a NEW entry that is a real yield (text or tool_use),
# skipping thinking-only intermediate entries (which flush before the turn's text).
# If none appears (already flushed), fall through at budget and classify what is last.
sig0 = sig(last_assistant(path))
entry = last_assistant(path)
waited = 0.0
while waited < BUDGET:
    entry = last_assistant(path)
    tu, th, txt = parse_entry(entry) if entry is not None else (False, False, "")
    if sig(entry) != sig0 and (tu or txt):
        break                                # a real (text/tool) yield landed
    time.sleep(INTERVAL); waited += INTERVAL

if entry is None:
    print("SKIP"); sys.exit(0)

has_tool_use, has_thinking, text = parse_entry(entry)

if has_tool_use:
    print("PRODUCTIVE")                      # tool call = progress
elif COMPLETE_RE.match(text):
    print("COMPLETE")                        # finished (completion-dominant) → release
elif has_thinking and not text:
    print("SKIP")                            # thinking-only entry → never a no-op block
elif (not text) or SENTINEL_RE.match(text):
    print("BLOCK")                           # unambiguous no-op → continue the arc
elif blocker_stance(text) and not has_ask_block(text):
    print("HANDBACK")                        # halts on a human, but asks them nothing
else:
    print("PRODUCTIVE")                      # substantive text = a healthy yield
PY
)"

case "$VERDICT" in
    PRODUCTIVE)
        # reset the CONSECUTIVE counter only outside a hook-driven continuation chain,
        # so an interleaved trivial tool_use cannot keep a forced loop alive.
        # Reset BOTH consecutive counters. handback_count was never reset, so its
        # "one rewrite nudge" cap silently became a lifetime cap: after one nudge, a
        # later malformed handback (with productive turns in between) got none.
        [ "${STOP_ACTIVE:-0}" = "1" ] || {
            "$ARC_HELPER" reset "$SID" reconcile_count >/dev/null 2>&1 || true
            "$ARC_HELPER" reset "$SID" handback_count  >/dev/null 2>&1 || true
        }
        exit 0 ;;
    COMPLETE)
        "$ARC_HELPER" complete "$SID" >/dev/null 2>&1 || true   # auto-release
        exit 0 ;;
    HANDBACK)
        [ "$("$ARC_HELPER" try-block "$SID" "$HANDBACK_CONSEC_MAX" "$LIFE_MAX" handback_count 2>/dev/null)" = "BLOCK" ] || exit 0
        HB_REASON="This turn ends the arc on something only the human can resolve, but the message contains no answerable ask block. Before stopping: (1) climb the autonomy ladder - is the answer already on disk, in .control/preauth.yaml, resolvable by a fresh agent, reachable by another lane, or a REVERSIBLE default you should just take and log? (2) if any unblocked lane still exists, run it instead of stopping. (3) only if neither holds, rewrite per the handback skill: a '## Blocked on you' heading FIRST, every row an imperative addressed to the reader with options and a recommendation, each row carrying 'if you say nothing, I do X', plain language, ranked by what it unblocks - then the 9-item receipt underneath."
        python3 - "$HB_REASON" <<'PYHB'
import sys, json
print(json.dumps({"decision": "block", "reason": sys.argv[1]}))
PYHB
        exit 0 ;;
    BLOCK)
        [ "$("$ARC_HELPER" try-block "$SID" "$CONSEC_MAX" "$LIFE_MAX" 2>/dev/null)" = "BLOCK" ] || exit 0
        NEXT="$("$ARC_HELPER" next "$SID" 2>/dev/null)"
        SLUG="$("$ARC_HELPER" status "$SID" 2>/dev/null | awk '{print $2}')"
        REASON="Autonomous arc${SLUG:+ $SLUG} is active and this turn ended without continuing it. 'No response requested' / an empty terminal is never a valid mid-arc stop. Reconcile git/PR/watcher state, then continue"
        [ -n "${NEXT:-}" ] && REASON="$REASON the next slice: $NEXT"
        REASON="$REASON. If the arc is genuinely finished, run \`autonomous-arc.sh complete $SID\` so this stops firing."
        python3 - "$REASON" <<'PY'
import sys, json
print(json.dumps({"decision": "block", "reason": sys.argv[1]}))
PY
        exit 0 ;;
    *)
        exit 0 ;;
esac
