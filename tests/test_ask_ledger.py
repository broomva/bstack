"""Tests for ask_ledger.py (BRO-2179).

Every rule here is a rule the spec states in prose. A rule with no test is prose.
Each negative test is paired with the positive control that proves the checker is
not simply rejecting everything.
"""

import sys
from pathlib import Path

import pytest
import yaml

# The script lives in scripts/, the test in tests/ — bstack keeps them apart,
# unlike the workspace layout this suite was written against.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from ask_ledger import (  # noqa: E402
    ROW_CAP,
    load_sweep,
    main,
    sweep,
    SCENARIO_MIN,
    render_handback_block,
    open_asks,
    runnable_lanes,
    sort_decisions,
    validate,
    validate_decisions,
)


def ledger(**overrides):
    base = {
        "arc": "demo",
        "opened": "2026-08-18T20:58Z",
        "tz": "America/Bogota",
        "lanes": ["gate", "skill", "replay"],
        "asks": [],
    }
    base.update(overrides)
    return base


def ask(**overrides):
    base = {
        "id": "A1",
        "ask": "Merge PR 403",
        "class": "authority",
        "gates": ["gate"],
        "exhausted": [
            "read: no prior decision on disk",
            "preauth: no standing grant covers this",
        ],
        "blocking": False,
        "default": "leave it open",
        "preanswerable": True,
        "answered_at": None,
    }
    base.update(overrides)
    return base


# ── the positive control ─────────────────────────────────────────────────────

def test_a_well_formed_ledger_passes():
    """Without this, every assertion below is satisfied by a checker that fails everything."""
    assert validate(ledger(asks=[ask()])) == []


# ── rule zero: asking is the last resort ─────────────────────────────────────

def test_empty_exhausted_is_an_error():
    errs = validate(ledger(asks=[ask(exhausted=[])]))
    assert any("skipped the autonomy ladder" in e for e in errs)


def test_missing_exhausted_is_reported_as_missing_field():
    a = ask()
    del a["exhausted"]
    errs = validate(ledger(asks=[a]))
    assert any("missing field 'exhausted'" in e for e in errs)


# ── silence must never be fatal ──────────────────────────────────────────────

def test_non_blocking_ask_must_carry_a_default():
    errs = validate(ledger(asks=[ask(blocking=False, default=None)]))
    assert any("silence must never be fatal" in e for e in errs)


@pytest.mark.parametrize("reason", ["irreversible", "externally-visible", "spends-money"])
def test_blocking_ask_is_legitimate_only_with_a_stated_reason(reason):
    a = ask(blocking=True, default=None, no_default_because=reason)
    assert validate(ledger(asks=[a])) == []


def test_blocking_without_a_reason_is_an_error():
    errs = validate(ledger(asks=[ask(blocking=True, default=None)]))
    assert any("no_default_because" in e for e in errs)


def test_blocking_with_a_default_is_a_contradiction():
    a = ask(blocking=True, default="do X", no_default_because="irreversible")
    errs = validate(ledger(asks=[a]))
    assert any("then it is not blocking" in e for e in errs)


# ── schema hygiene ───────────────────────────────────────────────────────────

def test_unknown_class_rejected():
    assert any("not one of" in e for e in validate(ledger(asks=[ask(**{"class": "vibes"})])))


def test_gate_on_unknown_lane_rejected():
    assert any("unknown lane" in e for e in validate(ledger(asks=[ask(gates=["nope"])])))


def test_duplicate_ids_rejected():
    errs = validate(ledger(asks=[ask(), ask()]))
    assert any("duplicate id" in e for e in errs)


# ── standing grants: never re-ask a settled question ─────────────────────────

PREAUTH = {
    "grants": [
        {"match": {"class": "authority", "action": "publish artifacts to a public URL"},
         "granted": False, "note": "always ask"},
        {"match": {"class": "scope"}, "granted": True, "note": "scope is mine to decide"},
    ]
}


def test_ask_already_settled_by_a_standing_grant_is_an_error():
    a = ask(**{"class": "scope"})
    errs = validate(ledger(asks=[a]), PREAUTH)
    assert any("do not re-raise" in e for e in errs)


def test_an_answered_ask_is_not_flagged_against_preauth():
    a = ask(**{"class": "scope", "answered_at": "2026-08-18T21:00Z"})
    assert validate(ledger(asks=[a]), PREAUTH) == []


def test_action_scoped_grant_does_not_match_an_unrelated_ask():
    """The public-URL grant must not swallow every authority-class ask."""
    a = ask(**{"class": "authority", "ask": "Merge PR 403"})
    assert validate(ledger(asks=[a]), PREAUTH) == []


def test_granted_false_means_ALWAYS_ASK_and_must_not_suppress():
    """Regression: the first draft treated `granted: false` as authorization.

    The shipped preauth file's only row is a `granted: false` for publishing to a
    public URL. Suppressing it would have swallowed exactly the approval the
    operator most wants to see. `granted: false` records "always ask", so a matching
    ask is CORRECT and must produce no error.
    """
    a = ask(**{"class": "authority", "ask": "Publish artifacts to a public URL for the release"})
    assert validate(ledger(asks=[a]), PREAUTH) == []


def test_granted_true_does_suppress_a_matching_ask():
    """The positive control for the rule above — without it, the checker could simply
    never suppress anything and still pass."""
    a = ask(**{"class": "scope"})
    assert any("do not re-raise" in e for e in validate(ledger(asks=[a]), PREAUTH))


def test_empty_match_grant_is_ignored_not_a_wildcard():
    """A grant with no match must not silently authorise everything."""
    assert validate(ledger(asks=[ask()]), {"grants": [{"match": {}, "granted": True}]}) == []


# ── blocked is a LANE state, not an ARC state ────────────────────────────────

def test_a_non_blocking_ask_parks_nothing():
    run, parked = runnable_lanes(ledger(asks=[ask(blocking=False)]))
    assert parked == {}
    assert run == ["gate", "skill", "replay"]


def test_a_blocking_ask_parks_only_the_lanes_it_gates():
    a = ask(blocking=True, default=None, no_default_because="irreversible", gates=["gate"])
    run, parked = runnable_lanes(ledger(asks=[a]))
    assert parked == {"gate": ["A1"]}
    assert run == ["skill", "replay"], "an ask must never park a lane it does not gate"


def test_an_answered_blocking_ask_releases_its_lane():
    a = ask(blocking=True, default=None, no_default_because="irreversible",
            answered_at="2026-08-18T22:00Z")
    run, parked = runnable_lanes(ledger(asks=[a]))
    assert parked == {}
    assert run == ["gate", "skill", "replay"]


def test_the_arc_only_stops_when_every_lane_is_parked():
    asks = [
        ask(id="A1", blocking=True, default=None, no_default_because="irreversible", gates=["gate"]),
        ask(id="A2", blocking=True, default=None, no_default_because="spends-money",
            gates=["skill", "replay"]),
    ]
    run, parked = runnable_lanes(ledger(asks=asks))
    assert run == []
    assert set(parked) == {"gate", "skill", "replay"}


def test_open_asks_excludes_answered_ones():
    asks = [ask(id="A1"), ask(id="A2", answered_at="2026-08-18T22:00Z")]
    assert [a["id"] for a in open_asks(ledger(asks=asks))] == ["A1"]


# ── the shipped preauth file must itself be valid ────────────────────────────

def test_shipped_preauth_example_parses_and_grants_nothing_unscoped():
    p = Path(__file__).resolve().parents[1] / "references" / "preauth.example.yaml"
    data = yaml.safe_load(p.read_text(encoding="utf-8"))
    grants = data.get("grants") or []
    # Without this, an example that lost its grants would pass the loop vacuously —
    # the assertion would run zero times and still report green.
    assert grants, "the shipped example must carry >=1 grant or the loop asserts nothing"
    for grant in grants:
        assert grant.get("match"), "a grant with an empty match would authorise everything"
        assert "granted" in grant and "note" in grant and "since" in grant


# ── regressions from cross-model review (BRO-2179 round 1) ───────────────────

def test_a_finished_lane_is_neither_runnable_nor_parked():
    """Without `done`, every declared lane counted as runnable forever, so
    `lanes --runnable` could never go empty and a finished arc could never stop."""
    l = ledger(asks=[], done=["gate", "skill"])
    run, parked = runnable_lanes(l)
    assert run == ["replay"]
    assert parked == {}


def test_a_blocking_ask_cannot_park_a_finished_lane():
    a = ask(blocking=True, default=None, no_default_because="irreversible", gates=["gate"])
    run, parked = runnable_lanes(ledger(asks=[a], done=["gate"]))
    assert parked == {}
    assert run == ["skill", "replay"]


def test_every_lane_done_means_the_arc_may_stop():
    run, parked = runnable_lanes(ledger(asks=[], done=["gate", "skill", "replay"]))
    assert run == [] and parked == {}


def test_done_naming_an_undeclared_lane_is_an_error():
    assert any("not a declared lane" in e for e in validate(ledger(asks=[], done=["ghost"])))


@pytest.mark.parametrize("bad", ["true", "false", 1, 0, None])
def test_blocking_must_be_a_real_boolean(bad):
    """A quoted 'true' reads as a string and silently takes the non-blocking branch."""
    errs = validate(ledger(asks=[ask(blocking=bad)]))
    assert any("must be a real boolean" in e for e in errs)


@pytest.mark.parametrize("bad", ["", "   "])
def test_empty_answered_at_would_release_an_unanswered_lane(bad):
    errs = validate(ledger(asks=[ask(answered_at=bad)]))
    assert any("answered_at" in e for e in errs)


def test_unknown_gate_is_caught_even_when_lanes_is_empty():
    """The check was conditional on `if lanes`, making it vacuous exactly when the
    ledger was most malformed."""
    errs = validate(ledger(lanes=[], asks=[ask(gates=["deploy"])]))
    assert any("unknown lane 'deploy'" in e for e in errs)


def test_empty_lanes_is_itself_an_error():
    assert any("lanes is empty" in e for e in validate(ledger(lanes=[], asks=[])))


def test_open_asks_are_ordered_by_leverage_not_yaml_order():
    asks = [
        ask(id="A1", unblocks="1 lane"),
        ask(id="A2", unblocks="unblocks 9 of 18 rows"),
        ask(id="A3", unblocks="3 lanes"),
    ]
    assert [a["id"] for a in open_asks(ledger(asks=asks))] == ["A2", "A3", "A1"]


def test_blocking_outranks_non_blocking_at_equal_leverage():
    asks = [
        ask(id="A1", unblocks="2 lanes", blocking=False),
        ask(id="A2", unblocks="2 lanes", blocking=True, default=None,
            no_default_because="irreversible"),
    ]
    assert [a["id"] for a in open_asks(ledger(asks=asks))] == ["A2", "A1"]


# ── the ladder must be recorded, not merely non-empty (round 2) ──────────────

def test_a_single_arbitrary_line_does_not_certify_the_ladder():
    """`exhausted: ["placeholder"]` used to prove an ask had earned its place."""
    errs = validate(ledger(asks=[ask(exhausted=["placeholder"])]))
    assert any("must name the rung" in e for e in errs)


def test_one_labelled_rung_is_not_enough():
    errs = validate(ledger(asks=[ask(exhausted=["read: nothing on disk"])]))
    assert any("needs >= 2" in e for e in errs)


def test_two_labelled_rungs_pass():
    a = ask(exhausted=["read: nothing on disk", "reroute: no other lane reaches it"])
    assert validate(ledger(asks=[a])) == []


def test_the_same_rung_twice_is_still_one_rung():
    a = ask(exhausted=["read: checked the ticket", "read: checked the handoff"])
    assert any("needs >= 2" in e for e in validate(ledger(asks=[a])))


def test_a_yaml_datetime_answered_at_is_accepted():
    """An unquoted 2026-08-18T21:25:00Z parses to a datetime, not a string, and was
    being rejected as malformed."""
    import datetime
    a = ask(answered_at=datetime.datetime(2026, 8, 18, 21, 25, tzinfo=datetime.timezone.utc))
    assert validate(ledger(asks=[a])) == []


def test_a_yaml_date_answered_at_is_accepted():
    import datetime
    assert validate(ledger(asks=[ask(answered_at=datetime.date(2026, 8, 18))])) == []


# ── decisions: the rung-6 record (BRO-2199) ──────────────────────────────────
#
# Rung 6 says "take the reversible default, log it, tell the human afterwards".
# These rules make "log it" checkable. As above, every negative test is paired
# with a positive control, so none of them is satisfied by a checker that simply
# rejects everything.

_SCENARIO = (
    "When a run finishes and the branch is still open, the arc now leaves it open and "
    "reports the URL. The alternative would have merged it on green CI without telling "
    "anyone, which is the behaviour we are choosing against here."
)


def decision(**overrides):
    base = {
        "id": "D1",
        "decision": "Leave the branch open rather than auto-merging",
        "scenario": _SCENARIO,
        "gap": "the spec never said what to do when CI is green but review is absent",
        "reach": "every later arc inherits open-by-default; changing it means changing p9",
        "verdict": "sound",
        "confidence": "medium",
        "reversal": "merge the branch manually; nothing else depends on it",
    }
    base.update(overrides)
    return base


def test_a_well_formed_decision_passes():
    """The positive control for every negative test below."""
    assert validate_decisions(ledger(decisions=[decision()])) == []


def test_absent_decisions_key_is_legal_and_silent():
    """Backward compatibility: ledgers written before this existed must keep validating."""
    assert validate_decisions(ledger()) == []
    assert validate(ledger(asks=[ask()])) == []


def test_decisions_errors_reach_the_top_level_validate():
    """A rule that validate_decisions enforces but validate never calls is decoration."""
    errs = validate(ledger(asks=[ask()], decisions=[decision(reversal="")]))
    assert any("reversal" in e for e in errs)


# ── the load-bearing rule: not asking is only defensible if it can be undone ──

def test_a_decision_with_no_reversal_and_no_reason_is_rejected():
    errs = validate_decisions(ledger(decisions=[decision(reversal="  ")]))
    assert any("reversal" in e for e in errs)


def test_a_decision_may_be_irreversible_only_with_a_stated_reason():
    """Superseded by the rung-6 rule below: a stated reason is necessary but no
    longer sufficient — an irreversible choice must also have reached the user.
    Kept as the narrower assertion that the REASON enum is still honoured."""
    d = decision(reversal=None, irreversible_because="spends-money",
                 verdict="needs-user", ask="A1")
    del d["reversal"]
    assert validate_decisions(ledger(asks=[ask()], decisions=[d])) == []


def test_an_irreversible_reason_outside_the_enum_is_rejected():
    d = decision(irreversible_because="seemed-fine")
    del d["reversal"]
    errs = validate_decisions(ledger(decisions=[d]))
    assert any("irreversible_because" in e for e in errs)


def test_both_reversal_and_irreversible_is_a_contradiction():
    """Mirrors the blocking+default contradiction on asks: it cannot be both."""
    errs = validate_decisions(
        ledger(decisions=[decision(irreversible_because="irreversible")])
    )
    assert any("both" in e for e in errs)


# ── unsound entries name the decision to redo from, not a patch ──────────────

def test_unsound_without_a_corrected_decision_is_rejected():
    errs = validate_decisions(ledger(decisions=[decision(verdict="unsound")]))
    assert any("corrected_decision" in e for e in errs)


def test_unsound_with_a_corrected_decision_passes():
    errs = validate_decisions(
        ledger(decisions=[decision(verdict="unsound",
                                   corrected_decision="the lock must be held across the whole read")])
    )
    assert errs == []


# ── needs-user must actually have reached the user ───────────────────────────

def test_needs_user_without_an_ask_is_a_dropped_ask():
    errs = validate_decisions(ledger(asks=[ask()], decisions=[decision(verdict="needs-user")]))
    assert any("dropped ask" in e for e in errs)


def test_needs_user_naming_a_nonexistent_ask_is_rejected():
    errs = validate_decisions(
        ledger(asks=[ask()], decisions=[decision(verdict="needs-user", ask="A99")])
    )
    assert any("not an ask in this ledger" in e for e in errs)


def test_needs_user_pointing_at_a_real_ask_passes():
    errs = validate_decisions(
        ledger(asks=[ask()], decisions=[decision(verdict="needs-user", ask="A1")])
    )
    assert errs == []


# ── anti-compression: the entry must walk the case, not restate the headline ──

def test_a_scenario_compressed_to_its_headline_is_rejected():
    errs = validate_decisions(ledger(decisions=[decision(scenario="Left the branch open.")]))
    assert any("floor" in e for e in errs)


def test_a_scenario_no_longer_than_the_headline_is_rejected():
    """Padding to the floor is not enough if it says nothing the headline did not."""
    long_headline = "x" * (SCENARIO_MIN + 20)
    errs = validate_decisions(
        ledger(decisions=[decision(decision=long_headline, scenario="y" * (SCENARIO_MIN + 5))])
    )
    assert any("no longer than" in e for e in errs)


# ── banked is settled ────────────────────────────────────────────────────────

def test_duplicate_decision_ids_are_rejected():
    errs = validate_decisions(ledger(decisions=[decision(), decision()]))
    assert any("banked is settled" in e for e in errs)


# ── enums ────────────────────────────────────────────────────────────────────

def test_unknown_verdict_rejected():
    errs = validate_decisions(ledger(decisions=[decision(verdict="fine")]))
    assert any("verdict" in e for e in errs)


def test_unknown_confidence_rejected():
    errs = validate_decisions(ledger(decisions=[decision(confidence="quite-sure")]))
    assert any("confidence" in e for e in errs)


def test_missing_required_fields_are_named():
    d = decision()
    del d["gap"]
    errs = validate_decisions(ledger(decisions=[d]))
    assert any("missing field 'gap'" in e for e in errs)


def test_decisions_must_be_a_list():
    assert validate_decisions(ledger(decisions={"id": "D1"})) == ["decisions must be a list"]


# ── ordering: confidence ranks, verdict groups ───────────────────────────────

def test_flat_ordering_is_least_confident_first():
    ds = [decision(id="hi", confidence="high"),
          decision(id="lo", confidence="low"),
          decision(id="mid", confidence="medium")]
    assert [d["id"] for d in sort_decisions(ledger(decisions=ds), grouped=False)] == ["lo", "mid", "hi"]


def test_grouped_ordering_puts_needs_user_first_then_unsound_then_sound():
    ds = [
        decision(id="s", verdict="sound", confidence="low"),
        decision(id="u", verdict="unsound", confidence="high",
                 corrected_decision="redo from the invariant"),
        decision(id="n", verdict="needs-user", confidence="high", ask="A1"),
    ]
    got = [d["id"] for d in sort_decisions(ledger(asks=[ask()], decisions=ds), grouped=True)]
    assert got == ["n", "u", "s"]


def test_grouping_does_not_silently_reorder_within_a_verdict():
    ds = [decision(id="b", confidence="high"), decision(id="a", confidence="low")]
    assert [d["id"] for d in sort_decisions(ledger(decisions=ds))] == ["a", "b"]


# ── P20 round 1 (codex, 5/10 FAIL): the boundary cases the first pass missed ──

def test_a_blank_required_field_is_not_a_present_field():
    """Presence was checked, content was not — a blank entry discloses nothing
    while satisfying every other rule."""
    for field in ("id", "decision", "scenario", "gap", "reach"):
        errs = validate_decisions(ledger(decisions=[decision(**{field: "   "})]))
        assert any(f"'{field}'" in e for e in errs), f"{field} accepted blank"


def test_a_wrong_typed_required_field_is_rejected():
    errs = validate_decisions(ledger(decisions=[decision(gap=42)]))
    assert any("'gap'" in e for e in errs)


def test_an_unhashable_id_does_not_crash_the_validator():
    """A validator that falls over on malformed input has one failure mode it may
    not have. `seen.add(<list>)` used to raise TypeError straight out of validate."""
    errs = validate_decisions(ledger(decisions=[decision(id=["not", "hashable"])]))
    assert any("'id'" in e for e in errs)


def test_two_unhashable_ids_still_collide_rather_than_crashing():
    errs = validate_decisions(ledger(decisions=[decision(id={"a": 1}), decision(id={"a": 1})]))
    assert any("duplicate id" in e for e in errs)


# ── rung 6 licenses REVERSIBLE defaults only ─────────────────────────────────

def test_an_irreversible_decision_must_be_recorded_as_needs_user():
    """Recording it is right; letting it read as a licensed rung-6 default is not."""
    d = decision(irreversible_because="spends-money", verdict="sound")
    del d["reversal"]
    errs = validate_decisions(ledger(decisions=[d]))
    assert any("licenses reversible defaults only" in e for e in errs)


def test_an_irreversible_decision_escalated_to_the_user_is_accepted():
    d = decision(irreversible_because="spends-money", verdict="needs-user", ask="A1")
    del d["reversal"]
    assert validate_decisions(ledger(asks=[ask()], decisions=[d])) == []


# ── the vacuity is closeable by the caller that can assert it ────────────────

def test_absent_decisions_passes_by_default_but_fails_under_require_decisions():
    lg = ledger(asks=[ask()])
    assert validate(lg) == []
    errs = validate(lg, require_decisions=True)
    assert any("--require-decisions" in e for e in errs)


def test_require_decisions_is_satisfied_by_a_real_entry():
    lg = ledger(asks=[ask()], decisions=[decision()])
    assert validate(lg, require_decisions=True) == []


# ── the CLI must not render a partial list from a broken ledger ──────────────

def _run_cli(tmp_path, ledger_obj, *args):
    import subprocess
    f = tmp_path / "l.yaml"
    f.write_text(yaml.safe_dump(ledger_obj))
    return subprocess.run(
        [sys.executable, str(Path(__file__).resolve().parent.parent / "scripts" / "ask_ledger.py"), *args, str(f)],
        capture_output=True, text=True,
    )


def test_decisions_cli_lists_a_well_formed_ledger(tmp_path):
    """Positive control: without this, the rejection test below is satisfied by a
    CLI that refuses everything."""
    r = _run_cli(tmp_path, ledger(decisions=[decision()]), "decisions")
    assert r.returncode == 0
    assert "D1" in r.stdout


def test_decisions_cli_refuses_a_malformed_ledger_rather_than_dropping_rows(tmp_path):
    """It used to filter non-mappings out silently and exit 0, so a partial list
    was indistinguishable from a complete one."""
    r = _run_cli(tmp_path, ledger(decisions=[decision(), "not-a-mapping"]), "decisions")
    assert r.returncode == 2
    assert "refusing to render a partial list" in r.stderr
    assert "D1" not in r.stdout


def test_validate_cli_reports_decision_count(tmp_path):
    r = _run_cli(tmp_path, ledger(asks=[ask()], decisions=[decision()]), "validate",
                 "--preauth", "/nonexistent")
    assert r.returncode == 0
    assert "1 decision(s)" in r.stdout


def _live_pipes(row: str) -> int:
    """Count pipes markdown will treat as cell delimiters.

    A pipe is live iff preceded by an EVEN number of backslashes: in "\\\\|" the
    first backslash escapes the second, leaving the pipe bare. The obvious
    `(?<!\\\\)\\|` gets this wrong, scores "\\\\|" as escaped, and made the
    escaping test pass with the escaping removed.
    """
    import re as _re
    n = 0
    for m in _re.finditer(r"\|", row):
        before = row[:m.start()]
        if (len(before) - len(before.rstrip("\\"))) % 2 == 0:
            n += 1
    return n


# ── the block is rendered, not described (P20 round 1 on skills#181) ─────────
#
# Four properties the handback contract states in prose. Prose is four things an
# agent forgets one at a time; each of these dies if the renderer drops one.

def test_block_is_ordered_least_confident_first():
    """Verdicts differ on purpose. With all-`sound` rows, grouped and flat orders
    are identical and this test cannot tell them apart — it passed with the
    renderer switched to grouped until the mutation sweep said so."""
    ds = [
        decision(id="u", verdict="unsound", confidence="high", decision="U",
                 corrected_decision="redo from the invariant"),
        decision(id="lo", verdict="sound", confidence="low", decision="L"),
        decision(id="mid", verdict="sound", confidence="medium", decision="M"),
    ]
    out = render_handback_block(ledger(decisions=ds))
    # flat/least-confident-first: L, M, U.  grouped would put U first.
    assert out.index("| L ") < out.index("| M ") < out.index("| U ")


def test_block_shows_the_undo_for_every_row():
    out = render_handback_block(ledger(decisions=[decision()]))
    assert "merge the branch manually" in out


def test_a_needs_user_row_is_not_duplicated_into_the_decided_block():
    """It reached the human, so it belongs in the ⛔ ask block keyed by its ask.
    Printing it here too would show one item twice under contradictory headings —
    "decided for you" and "blocked on you". This repo's own arc ledger did exactly
    that before the fix."""
    d = decision(id="n", verdict="needs-user", confidence="low", decision="ASKED", ask="A1")
    out = render_handback_block(ledger(asks=[ask()], decisions=[d, decision()]))
    assert "ASKED" not in out
    assert "Leave the branch open" in out          # the real decision still renders


def test_an_irreversible_row_never_reaches_the_block_via_a_valid_ledger():
    """validate() forces irreversible -> needs-user, and needs-user is excluded
    here, so no VALID ledger can render one. The fallback below is therefore the
    defensive path for an unvalidated ledger, not a normal one."""
    d = decision(irreversible_because="spends-money", verdict="needs-user", ask="A1")
    del d["reversal"]
    lg = ledger(asks=[ask()], decisions=[d])
    assert validate_decisions(lg) == []
    assert render_handback_block(lg) == ""


def test_a_missing_undo_never_renders_as_a_blank_cell():
    """Defensive: the renderer may be handed a ledger nobody validated. An empty
    undo column would read as 'no undo needed', the opposite of the truth."""
    d = decision(irreversible_because="spends-money", verdict="sound")
    del d["reversal"]
    out = render_handback_block(ledger(decisions=[d]))
    assert "IRREVERSIBLE" in out and "spends-money" in out


def test_block_caps_at_seven_rows():
    ds = [decision(id=f"D{i}") for i in range(10)]
    out = render_handback_block(ledger(decisions=ds))
    assert len([l for l in out.splitlines() if l.startswith("| ") and "---" not in l]) == ROW_CAP + 1


def test_an_overflowing_block_says_it_is_capped():
    """A capped list that does not say it is capped reads as the complete one."""
    ds = [decision(id=f"D{i}") for i in range(10)]
    out = render_handback_block(ledger(decisions=ds))
    assert "7 of 10 shown" in out


def test_a_block_within_the_cap_does_not_claim_to_be_capped():
    out = render_handback_block(ledger(decisions=[decision()]))
    assert "shown" not in out.splitlines()[0]
    assert "1 choice," in out.splitlines()[0]


def test_no_decisions_renders_nothing_rather_than_an_empty_block():
    """An empty block would assert that nothing was decided — a claim the
    renderer has no standing to make."""
    assert render_handback_block(ledger()) == ""


def test_a_pipe_in_prose_cannot_shift_the_columns():
    """An unescaped pipe splits the row and silently reattributes every later
    cell — an undo would end up printed against the wrong decision."""
    out = render_handback_block(
        ledger(decisions=[decision(decision="use A | not B", reversal="revert x")])
    )
    row = [l for l in out.splitlines() if l.startswith("| 1 ")][0]
    # count UNESCAPED pipes: the escaped one is still a "|" character, so a naive
    # .count() would fail on correct output (it did).
    assert _live_pipes(row) == 5, row
    assert "revert x" in row


def test_a_pre_escaped_pipe_does_not_become_a_delimiter_again():
    """Escaping pipes before backslashes turns an input containing "\\|" into
    "\\\\|" — a literal backslash followed by a live delimiter."""
    out = render_handback_block(
        ledger(decisions=[decision(decision=r"already \| escaped", reversal="revert y")])
    )
    row = [l for l in out.splitlines() if l.startswith("| 1 ")][0]
    assert _live_pipes(row) == 5, row
    assert "revert y" in row


def test_render_handback_cli(tmp_path):
    r = _run_cli(tmp_path, ledger(decisions=[decision()]), "decisions", "--render-handback")
    assert r.returncode == 0
    assert r.stdout.startswith("## 🔀 Decided for you")


# ── P20 round 2: defects introduced BY the round-1 fixes ─────────────────────

def test_a_cap_above_the_contract_is_refused():
    """--cap 100 would defeat the very rule the cap implements."""
    import pytest as _pt
    with _pt.raises(ValueError):
        render_handback_block(ledger(decisions=[decision()]), cap=ROW_CAP + 1)


def test_a_negative_cap_is_refused_rather_than_slicing_from_the_end():
    """rows[:-1] silently drops the LAST row — and rows are least-confident
    first, so a negative cap hides the row the block most exists to show."""
    import pytest as _pt
    with _pt.raises(ValueError):
        render_handback_block(ledger(decisions=[decision()]), cap=-1)


def test_cap_zero_is_legal_and_renders_no_rows():
    out = render_handback_block(ledger(decisions=[decision()]), cap=0)
    assert "0 of 1 shown" in out


def test_a_carriage_return_cannot_break_the_table():
    """Only \\n was stripped, so a lone \\r (or U+2028) still ended the row."""
    for sep in ("\r", "\u2028", "\u2029"):
        out = render_handback_block(
            ledger(decisions=[decision(decision=f"before{sep}after")])
        )
        rows = [l for l in out.splitlines() if l.startswith("| 1 ")]
        assert len(rows) == 1, f"{sep!r} split the row"
        assert "before after" in rows[0]


def test_a_null_verdict_or_confidence_is_rejected():
    """Present-but-null slipped past the content loop (which skipped these two)
    and the enum check (guarded on `is not None`)."""
    for f in ("verdict", "confidence"):
        errs = validate_decisions(ledger(decisions=[decision(**{f: None})]))
        assert any(f"'{f}'" in e for e in errs), f"null {f} accepted"


# ── P20 round 3: the last boundary defects ──────────────────────────────────

def test_an_invalid_cap_is_refused_even_when_nothing_would_render():
    """The check used to sit AFTER the empty-row return, so an empty or
    all-needs-user ledger accepted cap=999 and only a populated one rejected it.
    A bad call site would then be caught by whichever ledger happened to be
    non-empty — i.e. not reliably at all."""
    import pytest as _pt
    for lg in (ledger(),
               ledger(asks=[ask()],
                      decisions=[decision(verdict="needs-user", ask="A1")])):
        with _pt.raises(ValueError):
            render_handback_block(lg, cap=999)


def test_a_boolean_cap_is_refused():
    """isinstance(True, int) is True in Python, so cap=True meant cap=1 and
    silently hid six rows."""
    import pytest as _pt
    with _pt.raises(ValueError):
        render_handback_block(ledger(decisions=[decision()]), cap=True)


def test_an_unhashable_verdict_or_confidence_does_not_crash_the_validator():
    """`x not in VERDICTS` raises TypeError on a dict or list, so the validator
    crashed on exactly the hand-edited YAML it exists to reject."""
    for field in ("verdict", "confidence"):
        errs = validate_decisions(ledger(decisions=[decision(**{field: {"un": "hashable"}})]))
        assert any(f"'{field}'" in e for e in errs), f"unhashable {field} not reported"


# ── BRO-2204: an unsound row must carry its corrected decision ──────────────

def test_an_unsound_row_renders_its_corrected_decision():
    """The contract promises `unsound` appears "with its corrected decision".
    The renderer emitted decision/gap/reversal only, so the one field the schema
    REQUIRES on every unsound entry never reached the reader."""
    d = decision(verdict="unsound", confidence="low",
                 corrected_decision="the lock must be held across the whole read")
    out = render_handback_block(ledger(decisions=[d]))
    assert "redo from:" in out
    assert "the lock must be held across the whole read" in out


def test_a_sound_row_does_not_claim_a_redo():
    """NEGATIVE control (asserts absence): without it, the assertion above is
    satisfied by a renderer that stamps 'redo from:' on every row."""
    out = render_handback_block(ledger(decisions=[decision(verdict="sound")]))
    assert "redo from:" not in out


def test_an_unsound_row_with_a_blank_corrected_decision_is_marked_invalid(capsys):
    """validate() rejects this shape. The renderer must not normalise it into
    ordinary-looking output — a direct caller that skipped validation would then
    see a plausible block instead of a broken one."""
    d = decision(verdict="unsound", confidence="low", corrected_decision="   ")
    out = render_handback_block(ledger(decisions=[d]))
    assert "MISSING" in out and "invalid entry" in out
    assert "the ledger is invalid" in capsys.readouterr().err


def test_table_structure_survives_hostile_markdown_in_a_corrected_decision():
    """The trust contract is TABLE STRUCTURE, not markdown sanitisation: N cells
    in, N cells out, whatever the prose contains."""
    hostile = "x | y \n | evil | row | here | --- |"
    d = decision(verdict="unsound", confidence="low", corrected_decision=hostile)
    out = render_handback_block(ledger(decisions=[d]))
    rows = [l for l in out.splitlines() if l.startswith("| 1 ")]
    assert len(rows) == 1, out
    assert _live_pipes(rows[0]) == 5, rows[0]


# ── sweep: one command must see every arc ────────────────────────────────────
#
# The gap these cover, measured in SRI on 2026-09-11: six ledgers, four schema-
# invalid and two unparseable, undetected since 2026-09-08. Not because any check
# failed — because every subcommand took exactly one path, so no check ever ran
# over more than the ledger a human happened to name.

def _dir_with(tmp_path, **files):
    """Write {stem: yaml-text-or-object} into a directory and return it."""
    d = tmp_path / "asks"
    d.mkdir()
    for stem, body in files.items():
        text = body if isinstance(body, str) else yaml.safe_dump(body)
        (d / f"{stem}.yaml").write_text(text)
    return d


def test_sweep_on_a_directory_returns_its_ledgers_sorted(tmp_path):
    d = _dir_with(tmp_path, zeta=ledger(arc="z"), alpha=ledger(arc="a"))
    assert [p.stem for p in sweep(d)] == ["alpha", "zeta"]


def test_sweep_on_a_file_returns_just_that_file(tmp_path):
    """Positive control for the directory case: without it, a sweep() that always
    globbed would pass the test above and silently break every single-file caller."""
    d = _dir_with(tmp_path, only=ledger())
    f = d / "only.yaml"
    assert sweep(f) == [f]


def test_a_broken_ledger_does_not_hide_the_ones_after_it(tmp_path):
    """THE regression this feature exists for. `load` raises on unparseable YAML and
    the single-file CLI exits 2 — correct for one arc, fatal for a sweep: the first
    bad file would suppress every ledger sorted after it, and those are exactly the
    ones you now want to read."""
    d = _dir_with(tmp_path,
                  aaa_broken="arc: x\nbad: value: here\n",
                  zzz_fine=ledger(arc="fine"))
    loaded, broken = load_sweep(d)
    assert [lg.get("arc") for _, lg in loaded] == ["fine"]
    assert [p.stem for p, _ in broken] == ["aaa_broken"]


def test_validate_all_is_clean_on_clean_ledgers(tmp_path):
    """Positive control: without it, the two failure tests below are satisfied by a
    sweep that reports errors unconditionally."""
    d = _dir_with(tmp_path, one=ledger(), two=ledger(arc="second"))
    assert main(["validate", str(d), "--all", "--preauth", str(d / "nope.yaml")]) == 0


def test_validate_all_exits_1_on_schema_errors(tmp_path):
    bad = ledger(asks=[ask(exhausted=["read: only one rung"])])
    d = _dir_with(tmp_path, one=ledger(), two=bad)
    assert main(["validate", str(d), "--all", "--preauth", str(d / "nope.yaml")]) == 1


def test_validate_all_exits_2_when_any_ledger_is_unparseable(tmp_path):
    """2 outranks 1: a file that did not parse is the only failure that HIDES
    content rather than describing it, so it must not be reported as a mere
    schema error alongside ledgers that were actually read."""
    d = _dir_with(tmp_path, fine=ledger(), broken="arc: x\nbad: value: here\n")
    assert main(["validate", str(d), "--all", "--preauth", str(d / "nope.yaml")]) == 2


def test_open_all_pools_asks_across_arcs_and_names_the_arc(tmp_path, capsys):
    """An ask is only actionable with its arc attached — pooling two arcs into an
    untagged list tells the reader what is open and not where to go."""
    d = _dir_with(tmp_path,
                  one=ledger(arc="alpha", asks=[ask(id="A1")]),
                  two=ledger(arc="beta", asks=[ask(id="B1")]))
    assert main(["open", str(d), "--all"]) == 0
    out = capsys.readouterr().out
    assert "alpha\tA1" in out and "beta\tB1" in out


def test_open_all_sends_a_broken_ledger_to_stderr_not_stdout(tmp_path, capsys):
    """`open --all` is meant to be piped into a report. An error line on stdout
    would be ingested as if it were an ask."""
    d = _dir_with(tmp_path, fine=ledger(asks=[ask(id="A1")]), broken="arc: x\nbad: value: here\n")
    assert main(["open", str(d), "--all"]) == 2
    cap = capsys.readouterr()
    assert "A1" in cap.out
    assert "unparseable" in cap.err and "unparseable" not in cap.out
