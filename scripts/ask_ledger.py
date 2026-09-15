#!/usr/bin/env python3
"""ask_ledger.py — the human dependency chain, made queryable (BRO-2179).

Dep-Chain (P14) makes an agent enumerate what the CODE depends on before writing.
This does the same for what the ARC depends on a HUMAN for, and turns two prose
disciplines into queries:

    "did this ask earn the right to exist?"   -> validate
    "which lanes can still run without you?"  -> lanes --runnable

Why it exists: across 100 long autonomous sessions, 18 halted the arc on a human,
none of the 18 carried an ask block a person could act on, 5% said what happens
if the human stays silent, and 81% named a dependency knowable before the arc even
started. The ledger is what makes "I only asked when I had to" auditable instead
of a claim.

Spec: docs/specs/2026-08-18-agent-handback-contract.html
"""

from __future__ import annotations

import argparse
import datetime as _dt
import re
import sys
from pathlib import Path

import yaml

CLASSES = {"credential", "authority", "decision", "artifact", "scope", "external"}
# A blocking ask has NO default, which means silence stalls a lane. That is only
# ever legitimate for these three, and the row must say which one it is.
NO_DEFAULT_REASONS = {"irreversible", "externally-visible", "spends-money"}
REQUIRED_ASK_FIELDS = ("id", "ask", "class", "gates", "exhausted", "blocking", "preanswerable")
# The ladder rungs, in order. An `exhausted` entry names the rung it reports on, so
# "I climbed the ladder" is checkable rather than assertable: the first draft accepted
# `exhausted: ["placeholder"]` as proof the ask had earned its place.
RUNGS = ("read", "preauth", "delegate", "reroute", "loop", "default")
_RUNG_RE = re.compile(r"^\s*(%s)\s*:" % "|".join(RUNGS), re.I)
MIN_RUNGS = 2

# --- decisions: the rung-6 record (BRO-2199) -------------------------------
# Rung 6 of the ladder says "take the reversible default, log it, tell the human
# afterwards" — and until now nothing defined where "log it" writes. A choice taken
# on the user's behalf that leaves no record is indistinguishable from one nobody
# made; the user still inherits it. These entries are that record.
#
# Note the asymmetry with `asks`: a decision here NEVER became an ask, because it
# died at rung 6. So there is deliberately no `exhausted`-to-decision cross-check —
# `default:` appearing in an ask's `exhausted` means the rung was tried and FAILED
# (no safe default existed), which is the opposite of a decision being recorded.
VERDICTS = {"sound", "unsound", "needs-user"}
# Ordered, because confidence RANKS the report: least-confident first is the whole
# point. A set would lose the order and silently re-sort by hash.
CONFIDENCE = ("low", "medium", "high")
REQUIRED_DECISION_FIELDS = ("id", "decision", "scenario", "gap", "reach", "verdict", "confidence")
# The measured failure mode is not a missing scenario, it is a scenario COMPRESSED
# back to its headline by a closeout rewrite. This floor is a PROXY for "walked
# scenario", and a weak one: 120 characters of padding defeats it. It catches the
# compression it was built for and claims nothing more. See the docstring.
SCENARIO_MIN = 120


class LedgerError(Exception):
    pass


def load(path: Path) -> dict:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise LedgerError(f"no ledger at {path}")
    except yaml.YAMLError as e:
        raise LedgerError(f"{path}: unparseable YAML: {e}")
    if not isinstance(data, dict):
        raise LedgerError(f"{path}: top level must be a mapping")
    return data


def sweep(path: Path) -> list[Path]:
    """Every ledger a path denotes: a directory yields its *.yaml, sorted; a file
    yields itself.

    Arcs are per-ledger but VISIBILITY is not — "what is this repo waiting on a human
    for" spans every open arc, and answering it by running the CLI once per file is a
    loop nobody runs. Measured in SRI 2026-09-11: six ledgers, four schema-invalid and
    two unparseable, undetected since 2026-09-08 because no command ever read more
    than one.
    """
    if path.is_dir():
        return sorted(path.glob("*.yaml"))
    return [path]


def load_sweep(path: Path) -> tuple[list[tuple[Path, dict]], list[tuple[Path, str]]]:
    """Load every ledger under `path`, returning (loaded, broken).

    A broken ledger is COLLECTED, never raised. The single-file path exits 2 on an
    unparseable file, which is right for one arc and wrong for a sweep: the first bad
    file would hide every ledger after it, and the bad file is exactly the one whose
    neighbours you now want to check.
    """
    loaded: list[tuple[Path, dict]] = []
    broken: list[tuple[Path, str]] = []
    for f in sweep(path):
        try:
            loaded.append((f, load(f)))
        except LedgerError as e:
            broken.append((f, str(e)))
    return loaded, broken


def _preauth_hit(ask: dict, preauth: dict) -> dict | None:
    """Return the standing grant matching this ask, if any.

    A grant matches when every key it names matches the ask. Whether a match should
    SUPPRESS the ask depends on `granted`, and the caller must check it — see below.
    """
    for grant in preauth.get("grants") or []:
        match = grant.get("match") or {}
        if not match:
            continue                      # an empty match would swallow everything
        if all(ask.get(k) == v for k, v in match.items() if k != "action"):
            action = match.get("action")
            if action is None or action.lower() in str(ask.get("ask", "")).lower():
                return grant
    return None


def _suppresses(grant: dict | None) -> bool:
    """Only `granted: true` settles a question.

    `granted: false` records the OPPOSITE — "always ask" — so treating it as a hit
    would silently swallow the one class of ask the operator most wants to see. The
    first draft did exactly that, and the shipped preauth file's only row is a
    `granted: false` for publishing to a public URL: the inversion would have
    suppressed every public-publish approval. Found by cross-model review.
    """
    return bool(grant) and grant.get("granted") is True


def validate(ledger: dict, preauth: dict | None = None,
             require_decisions: bool = False) -> list[str]:
    """Return a list of errors. Empty list means the ledger is well-formed."""
    errs: list[str] = []
    preauth = preauth or {}

    for key in ("arc", "opened", "tz", "lanes", "asks"):
        if key not in ledger:
            errs.append(f"missing top-level key: {key}")
    lanes = ledger.get("lanes") or []
    if not isinstance(lanes, list):
        errs.append("lanes must be a list")
        lanes = []
    elif "lanes" in ledger and not lanes:
        errs.append("lanes is empty — an arc with no lanes has nothing to park or run")
    done = ledger.get("done") or []
    if not isinstance(done, list):
        errs.append("done must be a list")
        done = []
    for d in done:
        if d not in lanes:
            errs.append(f"done names '{d}', which is not a declared lane")
    asks = ledger.get("asks") or []
    if not isinstance(asks, list):
        errs.append("asks must be a list")
        return errs

    seen: set[str] = set()
    for i, ask in enumerate(asks):
        where = f"asks[{i}]"
        if not isinstance(ask, dict):
            errs.append(f"{where}: must be a mapping")
            continue
        aid = ask.get("id")
        where = f"ask {aid}" if aid else where
        for f in REQUIRED_ASK_FIELDS:
            if f not in ask:
                errs.append(f"{where}: missing field '{f}'")
        if aid in seen:
            errs.append(f"{where}: duplicate id")
        seen.add(aid)

        for flag in ("blocking", "preanswerable"):
            if flag in ask and not isinstance(ask.get(flag), bool):
                errs.append(
                    f"{where}: '{flag}' must be a real boolean, got {ask.get(flag)!r} — "
                    f"a quoted 'true' reads as a string and silently takes the wrong branch"
                )
        answered = ask.get("answered_at")
        # YAML parses an unquoted 2026-08-18T21:25:00Z into a datetime, so a real
        # timestamp was being rejected as malformed. Accept both shapes.
        if isinstance(answered, (_dt.datetime, _dt.date)):
            answered = answered.isoformat()
        if answered is not None and not (isinstance(answered, str) and answered.strip()):
            errs.append(
                f"{where}: 'answered_at' must be null or a non-empty timestamp, got "
                f"{answered!r} — an empty string would release a genuinely unanswered lane"
            )

        klass = ask.get("class")
        if klass is not None and klass not in CLASSES:
            errs.append(f"{where}: class '{klass}' not one of {sorted(CLASSES)}")

        gates = ask.get("gates") or []
        if not isinstance(gates, list):
            errs.append(f"{where}: gates must be a list")
        else:
            for g in gates:
                # Unconditional: guarding this on `if lanes` made it vacuous exactly when
                # the ledger was most malformed (an empty lanes list accepted any gate).
                if g not in lanes:
                    errs.append(f"{where}: gates unknown lane '{g}'")

        # THE load-bearing rule: asking is the last resort, not the interface.
        exhausted = ask.get("exhausted")
        if "exhausted" in ask:
            if not isinstance(exhausted, list) or not exhausted:
                errs.append(
                    f"{where}: 'exhausted' is empty — an ask that skipped the autonomy ladder "
                    f"is a defect, not a question."
                )
            else:
                named = {m.group(1).lower() for m in
                         (_RUNG_RE.match(str(e)) for e in exhausted) if m}
                unlabelled = [str(e)[:40] for e in exhausted if not _RUNG_RE.match(str(e))]
                if unlabelled:
                    errs.append(
                        f"{where}: 'exhausted' entries must name the rung they report on "
                        f"({'|'.join(RUNGS)}), e.g. 'read: no prior decision on disk'. "
                        f"Unlabelled: {unlabelled}"
                    )
                if len(named) < MIN_RUNGS:
                    errs.append(
                        f"{where}: 'exhausted' records {len(named)} rung(s), needs >= {MIN_RUNGS}. "
                        f"Reaching the ask means more than one route was tried and failed; a "
                        f"single line certifies nothing."
                    )
        blocking = ask.get("blocking")
        has_default = ask.get("default") not in (None, "")
        if blocking is True:
            if has_default:
                errs.append(f"{where}: blocking:true but a default is set — then it is not blocking")
            reason = ask.get("no_default_because")
            if reason not in NO_DEFAULT_REASONS:
                errs.append(
                    f"{where}: blocking:true requires no_default_because in "
                    f"{sorted(NO_DEFAULT_REASONS)} (got {reason!r}). Every other choice has a "
                    f"reversible default and must not stall a lane."
                )
        elif blocking is False and not has_default:
            errs.append(f"{where}: blocking:false requires a default — silence must never be fatal")

        hit = _preauth_hit(ask, preauth)
        if _suppresses(hit) and ask.get("answered_at") is None:
            errs.append(
                f"{where}: already settled by a standing grant "
                f"({hit.get('note') or hit.get('match')}) — do not re-raise it"
            )
    errs.extend(validate_decisions(ledger))
    # Absent `decisions:` is legal by default (backward compatibility, and an arc
    # that genuinely decided nothing must validate). But that leaves the headline
    # claim — "every choice taken on your behalf is recorded" — unenforced, so the
    # caller that CAN assert it (a closing handback, CI on an arc branch) opts in.
    if require_decisions and not (ledger.get("decisions") or []):
        errs.append(
            "no 'decisions' recorded, but --require-decisions was set — an arc that "
            "took reversible defaults and logged none is indistinguishable from one that "
            "decided nothing, and the user still inherits the difference"
        )
    return errs


def validate_decisions(ledger: dict) -> list[str]:
    """Errors in the `decisions:` list. Empty list means well-formed.

    Absent `decisions:` is legal and silent — a ledger written before this existed,
    or an arc that genuinely decided nothing, must keep validating. What this cannot
    detect is the arc that DID take defaults and recorded none; no property of this
    file distinguishes that from an honest empty. That gap belongs to the handback
    skill, which renders the block, not to the schema.
    """
    errs: list[str] = []
    decisions = ledger.get("decisions")
    if decisions is None:
        return errs
    if not isinstance(decisions, list):
        return ["decisions must be a list"]

    ask_ids = {
        str(a.get("id")) for a in (ledger.get("asks") or []) if isinstance(a, dict)
    }
    seen: set[str] = set()
    for i, d in enumerate(decisions):
        where = f"decisions[{i}]"
        if not isinstance(d, dict):
            errs.append(f"{where}: must be a mapping")
            continue
        did = d.get("id")
        # str(), not the raw value: a list or dict id is unhashable and would
        # crash `seen.add` — the validator falling over on malformed input is the
        # one failure mode a validator may not have.
        did_key = str(did)
        where = f"decision {did}" if isinstance(did, str) and did.strip() else where
        for f in REQUIRED_DECISION_FIELDS:
            if f not in d:
                errs.append(f"{where}: missing field '{f}'")
                continue
            # Presence is not content. An empty string satisfied every rule below
            # while saying nothing, which is precisely the disclosure this file
            # exists to force. Enum fields are checked separately just after.
            if f in ("verdict", "confidence"):
                # Their VALUES are enum-checked below, but that check is guarded
                # on `is not None`, so a present-but-null field passed both here
                # and there. Type them.
                if not isinstance(d.get(f), str) or not d.get(f).strip():
                    errs.append(
                        f"{where}: '{f}' must be a non-empty string, got {d.get(f)!r}"
                    )
                continue
            v = d.get(f)
            if not isinstance(v, str) or not v.strip():
                errs.append(
                    f"{where}: '{f}' must be a non-empty string, got {v!r} — a present-but-blank "
                    f"field discloses nothing while passing every check"
                )
        # Banked is settled: a choice already in the ledger is a given for later
        # passes. Two entries under one id means one of them silently re-decided it.
        if did_key in seen:
            errs.append(f"{where}: duplicate id — banked is settled, a decision is recorded once")
        seen.add(did_key)

        verdict = d.get("verdict")
        # `verdict not in VERDICTS` raises TypeError on an unhashable value (a
        # dict or list from hand-edited YAML), so the validator crashed on
        # exactly the malformed input it exists to reject. Membership is only
        # asked once the value is known to be a string.
        if not isinstance(verdict, str):
            verdict = None
        if verdict is not None and verdict not in VERDICTS:
            errs.append(f"{where}: verdict {verdict!r} not one of {sorted(VERDICTS)}")
        conf = d.get("confidence")
        if not isinstance(conf, str):
            conf = None
        if conf is not None and conf not in CONFIDENCE:
            errs.append(f"{where}: confidence {conf!r} not one of {list(CONFIDENCE)}")

        # THE load-bearing rule, and the exact mirror of the blocking/default rule
        # on asks: not asking is only defensible when the choice can be undone.
        # A decision with neither a reversal nor a named reason is the arc claiming
        # a right it did not earn.
        reversal = d.get("reversal")
        has_reversal = isinstance(reversal, str) and reversal.strip() != ""
        reason = d.get("irreversible_because")
        if has_reversal and reason is not None:
            errs.append(
                f"{where}: has both 'reversal' and 'irreversible_because' — if it can be "
                f"undone it is not irreversible. Say which."
            )
        elif not has_reversal:
            # Rung 6 licenses REVERSIBLE defaults only. An irreversible choice
            # taken on the user's behalf is not a logged default — it is an ask
            # that should have reached them. Recording it is still right (better
            # disclosed than not), but it must not read as licensed: it has to
            # carry the needs-user verdict and point at the ask.
            if reason in NO_DEFAULT_REASONS and verdict != "needs-user":
                errs.append(
                    f"{where}: irreversible_because={reason!r} but verdict is {verdict!r} — "
                    f"rung 6 licenses reversible defaults only, so an irreversible choice must "
                    f"be recorded as 'needs-user' and name the ask that carried it to the human"
                )
            if reason not in NO_DEFAULT_REASONS:
                errs.append(
                    f"{where}: needs a non-empty 'reversal' (how the user undoes this), or "
                    f"'irreversible_because' in {sorted(NO_DEFAULT_REASONS)} (got {reason!r}). "
                    f"An irreversible choice taken without asking is the one thing rung 6 "
                    f"never licensed."
                )

        # A patch layered on a bad decision preserves the decision. The entry names
        # the property that must hold in general, or it has not said anything.
        if verdict == "unsound":
            corrected = d.get("corrected_decision")
            if not (isinstance(corrected, str) and corrected.strip()):
                errs.append(
                    f"{where}: verdict 'unsound' requires 'corrected_decision' — the decision "
                    f"to redo from, not an edit to layer on top"
                )
        # needs-user is reserved for genuinely user-only calls. One that never
        # reached the user is not a verdict, it is a dropped ask.
        if verdict == "needs-user":
            ref = d.get("ask")
            if ref is None:
                errs.append(
                    f"{where}: verdict 'needs-user' requires 'ask' naming the ask that carries "
                    f"it — a user-only call that never became an ask is a dropped ask"
                )
            elif str(ref) not in ask_ids:
                errs.append(f"{where}: 'ask' names {ref!r}, which is not an ask in this ledger")

        scenario = d.get("scenario")
        headline = str(d.get("decision") or "")
        if isinstance(scenario, str):
            if len(scenario.strip()) < SCENARIO_MIN:
                errs.append(
                    f"{where}: 'scenario' is {len(scenario.strip())} chars, floor is "
                    f"{SCENARIO_MIN} — an entry compressed to its headline makes the reader "
                    f"interrogate it, which is the failure this field exists to prevent"
                )
            elif len(scenario.strip()) <= len(headline.strip()):
                errs.append(
                    f"{where}: 'scenario' is no longer than 'decision' — it must walk the "
                    f"case (trigger, what the work does today, what the alternative would do), "
                    f"not restate the headline"
                )
    return errs


def sort_decisions(ledger: dict, grouped: bool = True) -> list[dict]:
    """Least-confident first. Grouped by verdict when asked, flat otherwise.

    Confidence ranks; verdict groups. Flat ordering exists because the report opens
    with the least-confident few OVERALL, whatever their group — a high-confidence
    `needs-user` must not outrank a low-confidence `sound` in that opening.
    """
    raw = ledger.get("decisions") or []
    ds = [d for d in raw if isinstance(d, dict)]

    def conf_rank(d: dict) -> int:
        c = d.get("confidence")
        return CONFIDENCE.index(c) if c in CONFIDENCE else -1

    if not grouped:
        return sorted(ds, key=lambda d: (conf_rank(d), str(d.get("id"))))
    order = {"needs-user": 0, "unsound": 1, "sound": 2}
    return sorted(
        ds, key=lambda d: (order.get(d.get("verdict"), 3), conf_rank(d), str(d.get("id")))
    )


ROW_CAP = 7


def render_handback_block(ledger: dict, cap: int = ROW_CAP) -> str:
    """The 🔀 Decided for you block, rendered — not described.

    The handback contract states four properties of this block: least-confident
    first, every row shows its undo, hard cap 7, overflow counted in the header.
    Stated in prose those are four things an agent may forget one at a time. This
    function is the executable form, so a test can delete any one of them and go
    red. (Workspace invariant: a discipline that maps to no machine-checkable
    behaviour is not a discipline.)

    Returns "" for an empty ledger: the block is omitted, not rendered empty. The
    contract's "always in this order" constrains ORDER, never presence — a block
    with no rows would assert that nothing was decided, which is a claim this
    function has no standing to make.
    """
    # needs-user rows are excluded: they reached the human and belong in the ⛔
    # ask block, keyed by their `ask`. Rendering them here too would print the
    # same item twice under contradictory headings — "decided for you" and
    # "blocked on you" — which was visible in this repo's own arc ledger.
    # Validate the cap FIRST. Placing this after the empty-row return made the
    # check path-dependent: an empty or all-needs-user ledger accepted cap=999
    # and only a populated one rejected it, so the bad call site would be found
    # by whichever ledger happened to be non-empty.
    # `isinstance(True, int)` is True in Python, so bool is excluded explicitly —
    # cap=True would silently mean cap=1 and hide six rows.
    if isinstance(cap, bool) or not isinstance(cap, int) or not 0 <= cap <= ROW_CAP:
        raise ValueError(f"cap must be an int in 0..{ROW_CAP}, got {cap!r}")
    rows = [d for d in sort_decisions(ledger, grouped=False)
            if d.get("verdict") != "needs-user"]
    if not rows:
        return ""
    total = len(rows)
    shown = rows[:cap]
    head = f"## 🔀 Decided for you — {total} choice{'s' if total != 1 else ''}, least-confident first"
    if total > cap:
        # Never silently truncate: a capped list that does not say it is capped
        # reads as the complete one.
        head = (f"## 🔀 Decided for you — {len(shown)} of {total} shown, least-confident first"
                f" (full list in the ledger)")
    out = [head, "", "| # | I decided | Why it was mine to take | Undo |", "|---|---|---|---|"]
    for i, d in enumerate(shown, 1):
        undo = d.get("reversal")
        if not (isinstance(undo, str) and undo.strip()):
            # validate() rejects this shape, but the renderer must not quietly
            # print an empty cell if it is ever handed one — the undo IS the
            # justification for not having asked.
            undo = f"**IRREVERSIBLE** ({d.get('irreversible_because')}) — see ask {d.get('ask')}"
        what = str(d.get("decision") or "")
        # An unsound row that says only "this was wrong" is the shape the ledger
        # exists to replace. validate_decisions REQUIRES corrected_decision on
        # every unsound entry, and it is the actionable half — the property that
        # must hold in general, not an edit to layer on top — so it has to reach
        # the reader, not just the schema.
        if d.get("verdict") == "unsound":
            fix = str(d.get("corrected_decision") or "").strip()
            if fix:
                what += f" — **redo from:** {fix}"
            else:
                # validate() rejects this shape. Rendering a tidy "(unstated)"
                # normalised a contract violation into ordinary-looking output,
                # so a direct caller that skipped validation saw a plausible
                # block instead of a broken one. Mark it unmistakably and say so
                # on stderr — a missing redo is the entry's whole point missing.
                what += " — **redo from:** ⚠ MISSING (invalid entry: validate the ledger)"
                print(f"render_handback_block: decision {d.get('id')!r} is unsound with no "
                      f"corrected_decision — the ledger is invalid", file=sys.stderr)
        cells = [str(i), _md(what), _md(d.get("gap")), _md(undo)]
        out.append("| " + " | ".join(cells) + " |")
    return "\n".join(out)


def _md(v) -> str:
    """Cell-safe: an unescaped pipe in prose would split the row and shift every
    later column, silently reattributing an undo to the wrong decision.

    TRUST CONTRACT, and it is narrow: this guarantees TABLE STRUCTURE only —
    that N cells in produce N cells out. It does NOT sanitise markdown or HTML.
    A ledger is authored by the agent that ran the arc, and the block renders
    `**bold**` on purpose, so emphasis inside a cell is by design; but an entry
    quoting content from a file the agent read can carry arbitrary markdown into
    the handback. Every cell has always had this property — `decision`, `gap`
    and `reversal` alike — and `corrected_decision` is one more, not a new class.
    If a ledger ever accepts third-party text, escaping has to move up to
    markdown semantics and this docstring is the place that stops being true."""
    # Backslashes FIRST. Escaping pipes alone turns an input that already
    # contains "\\|" into "\\\\|", which markdown renders as a literal backslash
    # and then splits on the pipe — reintroducing the exact bug at one remove.
    return (str(v if v is not None else "")
            .replace("\\", "\\\\")
            .replace("|", "\\|")
            .replace("\r", " ")
            .replace("\n", " ")
            .replace("\u2028", " ")
            .replace("\u2029", " ")
            .strip())


def runnable_lanes(ledger: dict) -> tuple[list[str], dict[str, list[str]]]:
    """(lanes that can still run, {parked lane: [ask ids that park it]}).

    A lane is parked only by an OPEN ask that is BLOCKING. An ask with a default
    does not park anything — the arc takes the default and keeps going.

    A lane listed in the ledger's top-level `done:` is finished and is neither
    runnable nor parked. Without it every declared lane counted as runnable forever,
    so `lanes --runnable` could never go empty and a finished arc could never
    legitimately stop — the exact opposite of the invariant it encodes. Found by
    cross-model review against this file's own shipped example.
    """
    done = set(ledger.get("done") or [])
    lanes = [l for l in (ledger.get("lanes") or []) if l not in done]
    parked: dict[str, list[str]] = {}
    for ask in ledger.get("asks") or []:
        if not isinstance(ask, dict):
            continue
        if ask.get("answered_at") is not None:
            continue
        if ask.get("blocking") is not True:
            continue
        for lane in ask.get("gates") or []:
            if lane in done:
                continue                  # a finished lane cannot be parked
            parked.setdefault(lane, []).append(str(ask.get("id")))
    return [l for l in lanes if l not in parked], parked


def _leverage(ask: dict) -> int:
    """Rank key from `unblocks`: its leading integer, else 0.

    "unblocks 9 of 18 rows" -> 9. The first draft advertised leverage ordering in
    --help and returned YAML insertion order, so the handback could surface the
    cheapest ask first — the opposite of the rule it exists to serve.
    """
    import re as _re
    m = _re.search(r"\d+", str(ask.get("unblocks") or ""))
    return int(m.group()) if m else 0


def open_asks(ledger: dict) -> list[dict]:
    """Open asks, most leverage first; blocking asks outrank non-blocking at equal
    leverage, because those are the ones that actually park a lane."""
    opens = [
        a for a in (ledger.get("asks") or [])
        if isinstance(a, dict) and a.get("answered_at") is None
    ]
    return sorted(
        opens,
        key=lambda a: (-_leverage(a), a.get("blocking") is not True, str(a.get("id"))),
    )


def _sweep_cmd(args) -> int:
    """`validate --all` / `open --all` over a directory of ledgers.

    Exit codes match the single-file contract so a caller can treat them alike:
    0 clean, 1 schema errors, 2 at least one ledger did not parse. A broken file
    ranks above a merely-invalid one because it is the only failure that hides
    content rather than describing it.
    """
    loaded, broken = load_sweep(args.ledger)

    if args.cmd == "open":
        for path, lg in loaded:
            arc = lg.get("arc") or path.stem
            for a in open_asks(lg):
                print(f"{arc}\t{a.get('id')}\t{a.get('class')}\t"
                      f"{a.get('unblocks', '?')}\t{str(a.get('ask', ''))[:80]}")
        for path, err in broken:
            # stderr, not stdout: a consumer piping `open --all` into a report must not
            # silently ingest an error line as if it were an ask.
            print(f"error: {err}", file=sys.stderr)
        return 2 if broken else 0

    # validate --all
    preauth = {}
    if args.preauth and args.preauth.exists():
        try:
            preauth = load(args.preauth)
        except LedgerError as e:
            print(f"error: {e}", file=sys.stderr)
            return 2

    total_errs = 0
    for path, lg in loaded:
        errs = validate(lg, preauth, require_decisions=args.require_decisions)
        total_errs += len(errs)
        for e in errs:
            print(f"  ERROR {path.name}: {e}")
    for path, err in broken:
        print(f"  BROKEN {path.name}: {err}")

    print(f"ask-ledger sweep: {len(loaded) + len(broken)} ledger(s), "
          f"{len(broken)} unparseable, {total_errs} error(s)")
    if broken:
        return 2
    return 1 if total_errs else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    v = sub.add_parser("validate", help="check a ledger is well-formed and every ask earned its place")
    v.add_argument("ledger", type=Path)
    v.add_argument("--preauth", type=Path, default=Path(".control/preauth.yaml"))
    v.add_argument("--require-decisions", action="store_true",
                   help="fail if no decisions were recorded (for a closing handback / arc CI)")
    v.add_argument("--all", action="store_true",
                   help="treat the path as a directory and validate every ledger in it")

    l = sub.add_parser("lanes", help="which lanes can still run with no human input")
    l.add_argument("ledger", type=Path)
    l.add_argument("--runnable", action="store_true")
    l.add_argument("--parked", action="store_true")

    o = sub.add_parser("open", help="open asks, most leverage first")
    o.add_argument("ledger", type=Path)
    o.add_argument("--all", action="store_true",
                   help="treat the path as a directory and pool open asks across every arc")

    d = sub.add_parser("decisions", help="choices taken on the user's behalf, least-confident first")
    d.add_argument("ledger", type=Path)
    d.add_argument("--render-handback", action="store_true",
                   help="emit the 🔀 handback block as markdown")
    d.add_argument("--cap", type=int, default=ROW_CAP)
    d.add_argument("--flat", action="store_true",
                   help="least-confident first overall, ignoring verdict grouping")

    args = ap.parse_args(argv)

    if getattr(args, "all", False):
        return _sweep_cmd(args)

    try:
        ledger = load(args.ledger)
    except LedgerError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    if args.cmd == "validate":
        preauth = {}
        if args.preauth and args.preauth.exists():
            try:
                preauth = load(args.preauth)
            except LedgerError as e:
                print(f"error: {e}", file=sys.stderr)
                return 2
        errs = validate(ledger, preauth, require_decisions=args.require_decisions)
        for e in errs:
            print(f"  ERROR {e}")
        n = len(ledger.get("asks") or [])
        # Printed even when zero. "No decisions were recorded" and "decisions were
        # never a thing here" must not look alike to a reader auditing an arc.
        nd = len(ledger.get("decisions") or [])
        print(f"ask-ledger: {n} ask(s), {nd} decision(s), {len(errs)} error(s)")
        return 1 if errs else 0

    if args.cmd == "lanes":
        run, parked = runnable_lanes(ledger)
        if args.parked and not args.runnable:
            for lane, ids in sorted(parked.items()):
                print(f"{lane}\tparked by {','.join(ids)}")
            return 0
        for lane in run:
            print(lane)
        if not args.runnable:
            for lane, ids in sorted(parked.items()):
                print(f"# parked: {lane} (by {','.join(ids)})", file=sys.stderr)
        # An arc must not stop while a lane can still run; exit 3 says "keep going".
        return 3 if run else 0

    if args.cmd == "decisions":
        # Print nothing from a ledger we know is malformed. Listing the readable
        # rows of a broken file and exiting 0 is how a partial list gets mistaken
        # for the whole one — the reader cannot tell which entries were dropped.
        derrs = validate_decisions(ledger)
        if derrs:
            for e in derrs:
                print(f"  ERROR {e}", file=sys.stderr)
            print(
                f"decisions: ledger has {len(derrs)} error(s); refusing to render a "
                f"partial list. Fix it or run `validate` to see everything.",
                file=sys.stderr,
            )
            return 2
        if args.render_handback:
            block = render_handback_block(ledger, cap=args.cap)
            if block:
                print(block)
            else:
                print("# no decisions recorded — block omitted, not rendered empty",
                      file=sys.stderr)
            return 0
        rows = sort_decisions(ledger, grouped=not args.flat)
        if not rows:
            # An arc that recorded nothing says so on stderr. Silence would read as
            # "nothing was decided for you", which is a claim this file cannot make.
            print("# no decisions recorded in this ledger", file=sys.stderr)
        for d in rows:
            undo = d.get("reversal") or f"IRREVERSIBLE ({d.get('irreversible_because')})"
            print(
                f"{d.get('id')}\t{d.get('verdict')}\t{d.get('confidence')}\t"
                f"{str(d.get('decision', ''))[:70]}\tundo: {str(undo)[:60]}"
            )
        return 0

    if args.cmd == "open":
        for a in open_asks(ledger):
            print(f"{a.get('id')}\t{a.get('class')}\t{a.get('unblocks','?')}\t{a.get('ask','')[:100]}")
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
