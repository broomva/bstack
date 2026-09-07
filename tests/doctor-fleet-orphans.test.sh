#!/usr/bin/env bash
# tests/doctor-fleet-orphans.test.sh — doctor §28, unreclaimed fleets (BRO-2473)
#
# The section leans on one invariant `bstack fleet down` guarantees: a fleet's
# state directory is deleted ONLY when every peer was removed or was already
# gone. So a surviving fleet_* directory IS an unreclaimed fleet. These cases
# pin the three answers the section can give — named, clean, unknown — and the
# neutrality that keeps it advisory.
set -uo pipefail

BSTACK_REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DOCTOR="$BSTACK_REPO/scripts/doctor.sh"

PASS=0
FAIL=0
FAILED_TESTS=()

assert_pass() { PASS=$((PASS + 1)); echo "  ✓ $1"; }
assert_fail() {
    FAIL=$((FAIL + 1))
    FAILED_TESTS+=("$1")
    echo "  ✗ $1"
    [ -n "${2:-}" ] && echo "    ${2}"
}

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# §28's output only, so an unrelated section can never satisfy an assertion.
section28() {
    BSTACK_FLEET_STATE_DIR="$1" bash "$DOCTOR" 2>/dev/null \
        | sed -n '/28. Unreclaimed fleets/,/^$/p'
}

write_fleet() {  # <root> <id> <removed-for-peer-b>
    mkdir -p "$1/$2"
    cat > "$1/$2/fleet.json" <<EOF
{"schema_version":1,"fleet_id":"$2","created_at":"2026-09-07T00:00:00Z",
 "base_worktree":"/w","peers":[
  {"name":"wt-a","slug":"a","session_id":"aaaa1111","removed":true},
  {"name":"wt-b","slug":"b","session_id":"bbbb2222","removed":$3}]}
EOF
}

echo "doctor §28 — unreclaimed fleets"

# 1. POSITIVE CONTROL. A surviving directory with an unreclaimed peer must be
#    named. A section that stays silent here is broken by construction.
write_fleet "$TMP/live" "fleet_1788000000_aaaa" "null"
OUT="$(section28 "$TMP/live")"
if grep -q "fleet_1788000000_aaaa" <<< "$OUT"; then
    assert_pass "names a fleet whose state directory survives"
else
    assert_fail "names a fleet whose state directory survives" "$OUT"
fi
if grep -q "1/2 peer(s) unreclaimed" <<< "$OUT"; then
    assert_pass "counts the unreclaimed peers, not just the fleet"
else
    assert_fail "counts the unreclaimed peers, not just the fleet" "$OUT"
fi
if grep -q "bstack fleet down --fleet fleet_1788000000_aaaa" <<< "$OUT"; then
    assert_pass "prints the remedy naming the fleet id"
else
    assert_fail "prints the remedy naming the fleet id" "$OUT"
fi

# 2. NEGATIVE CONTROL. An absent root is reported, never passed over in silence.
OUT="$(section28 "$TMP/absent")"
if grep -q "no fleet state root" <<< "$OUT"; then
    assert_pass "an absent state root is stated, not silently clean"
else
    assert_fail "an absent state root is stated, not silently clean" "$OUT"
fi

# 3. An existing but empty root is the only genuinely clean answer.
mkdir -p "$TMP/empty"
OUT="$(section28 "$TMP/empty")"
if grep -q "nothing outstanding here" <<< "$OUT"; then
    assert_pass "an empty root reports nothing outstanding"
else
    assert_fail "an empty root reports nothing outstanding" "$OUT"
fi
# ...and must NOT claim reclamation: `down --force` deletes the record with peers
# still unreclaimed, so absence of a record is not proof that anything was
# reclaimed. The clean line has to say what it knows, not what it hopes.
if grep -q "was reclaimed" <<< "$OUT"; then
    assert_fail "the clean line does not claim every fleet was reclaimed (--force leaves no record)" "$OUT"
else
    assert_pass "the clean line does not claim every fleet was reclaimed (--force leaves no record)"
fi
if grep -q "force" <<< "$OUT"; then
    assert_pass "the clean line names --force as the reason absence is not proof"
else
    assert_fail "the clean line names --force as the reason absence is not proof" "$OUT"
fi

# 4. UNKNOWN CONTROL. A check that cannot tell must say so — never clean.
mkdir -p "$TMP/broken/fleet_1788000000_bbbb"
printf '{"schema_version":1,"peers":[' > "$TMP/broken/fleet_1788000000_bbbb/fleet.json"
OUT="$(section28 "$TMP/broken")"
if grep -q "unknown, not clean" <<< "$OUT"; then
    assert_pass "an unreadable fleet.json reports unknown, not clean"
else
    assert_fail "an unreadable fleet.json reports unknown, not clean" "$OUT"
fi
mkdir -p "$TMP/nojson/fleet_1788000000_cccc"
OUT="$(section28 "$TMP/nojson")"
if grep -q "no fleet.json" <<< "$OUT"; then
    assert_pass "a directory with no fleet.json reports unknown"
else
    assert_fail "a directory with no fleet.json reports unknown" "$OUT"
fi

# 5. A fully reclaimed fleet still has its directory only if `down` failed; when
#    every peer carries removed=true the count must read 0 unreclaimed rather
#    than suppressing the row (the directory itself is still the anomaly).
write_fleet "$TMP/allremoved" "fleet_1788000000_dddd" "true"
OUT="$(section28 "$TMP/allremoved")"
if grep -q "0/2 peer(s) unreclaimed" <<< "$OUT"; then
    assert_pass "a directory whose peers are all removed still surfaces, at 0 unreclaimed"
else
    assert_fail "a directory whose peers are all removed still surfaces, at 0 unreclaimed" "$OUT"
fi

# 5b. SHAPE CONTROL. Valid JSON of the WRONG shape parses fine and then raises
#     on .get(). A raise empties the whole report and the section renders as a
#     header with no body — which reads as clean, and (because entries are
#     sorted) suppresses every fleet after it. Each shape must report unknown,
#     and a good fleet sorting AFTER a bad one must still be named.
i=0
for bad in '[]' 'null' '"a string"' '{"peers":{}}' '{"peers":["x"]}' '{"peers":[["nested"]]}' '{"no_peers_key":1}'; do
    i=$((i + 1))
    R="$TMP/shape$i"
    mkdir -p "$R/fleet_1000000000_aaaa" "$R/fleet_2000000000_zzzz"
    printf '%s' "$bad" > "$R/fleet_1000000000_aaaa/fleet.json"
    # a genuine orphan that sorts AFTER the malformed one
    cat > "$R/fleet_2000000000_zzzz/fleet.json" <<'EOF'
{"schema_version":1,"peers":[{"name":"wt-a","session_id":"a1","removed":null}]}
EOF
    OUT="$(section28 "$R")"
    if grep -q "fleet_1000000000_aaaa" <<< "$OUT" && ! grep -q "0 unreclaimed" <<< "$OUT"; then
        assert_pass "wrong-shape fleet.json ($bad) is reported, not crashed past"
    else
        assert_fail "wrong-shape fleet.json ($bad) is reported, not crashed past" "$OUT"
    fi
    if grep -q "fleet_2000000000_zzzz" <<< "$OUT"; then
        assert_pass "a real orphan sorting after a malformed record is still named ($bad)"
    else
        assert_fail "a real orphan sorting after a malformed record is still named ($bad)" "$OUT"
    fi
done

# 5b-bis. The shape guard and the total-body except are DEFENCE IN DEPTH: either
#     alone keeps a wrong-shape record from emptying the report, so neither is
#     pinned by outcome alone (a mutation dropping the guard survives on the
#     except). Pin the guard by its message: a shape it can classify must read
#     "not a fleet record", never the generic exception fallback. Now both
#     layers are independently gated.
for bad in '[]' '{"peers":{}}' '{"peers":["x"]}'; do
    R="$TMP/shapemsg$(echo "$bad" | cksum | cut -d' ' -f1)"
    mkdir -p "$R/fleet_1788000000_aaaa"
    printf '%s' "$bad" > "$R/fleet_1788000000_aaaa/fleet.json"
    OUT="$(section28 "$R")"
    if grep -q "is not a fleet record" <<< "$OUT"; then
        assert_pass "the shape guard classifies $bad (not the generic exception fallback)"
    else
        assert_fail "the shape guard classifies $bad (not the generic exception fallback)" "$OUT"
    fi
done

# 5c. UNREADABLE ROOT. pathlib.glob swallows PermissionError, which would render
#     the most confident clean line the section can print. It must say unknown.
if [ "$(id -u)" -ne 0 ]; then
    UR="$TMP/unreadable"; mkdir -p "$UR/fleet_1788000000_aaaa"
    echo '{"schema_version":1,"peers":[{"name":"a","removed":null}]}' > "$UR/fleet_1788000000_aaaa/fleet.json"
    chmod 000 "$UR"
    OUT="$(section28 "$UR")"
    chmod 755 "$UR"
    if grep -q "not readable" <<< "$OUT"; then
        assert_pass "an unreadable state root reports unknown, never clean"
    else
        assert_fail "an unreadable state root reports unknown, never clean" "$OUT"
    fi
else
    assert_pass "unreadable-root case skipped (running as root)"
fi

# 5d. A fleet_* entry that is not a directory must not be skipped into clean.
ND="$TMP/notdir"; mkdir -p "$ND"; ln -s /nonexistent "$ND/fleet_1788000000_link"
OUT="$(section28 "$ND")"
if grep -q "not a directory" <<< "$OUT"; then
    assert_pass "a fleet_* entry that is not a directory reports unknown"
else
    assert_fail "a fleet_* entry that is not a directory reports unknown" "$OUT"
fi

# 5e. IMPORT HYGIENE (security). `python3 -` puts the CWD at sys.path[0], and
#     fleet.py's own `from scripts import peer` finds no `scripts` package beside
#     it — so without stripping the cwd, doctor EXECUTES a foreign
#     scripts/peer.py from whatever directory it was invoked in. `bstack doctor`
#     is documented to run from an arbitrary directory, so that cwd is untrusted.
#     Nothing else pins this, and a refactor could drop it silently.
DECOY="$TMP/decoy"
mkdir -p "$DECOY/.control" "$DECOY/.git" "$DECOY/scripts"
: > "$DECOY/scripts/__init__.py"
cat > "$DECOY/scripts/peer.py" <<EOF
import pathlib
pathlib.Path("$TMP/HIJACKED").write_text("foreign scripts/peer.py executed")
EOF
rm -f "$TMP/HIJACKED"
( cd "$DECOY" && BSTACK_FLEET_STATE_DIR="$TMP/live" bash "$DOCTOR" >/dev/null 2>&1 )
if [ -f "$TMP/HIJACKED" ]; then
    assert_fail "doctor does not execute a scripts/peer.py from the audited cwd" "$(cat "$TMP/HIJACKED")"
else
    assert_pass "doctor does not execute a scripts/peer.py from the audited cwd"
fi

# 5f. FIELD SANITISER. The report is tab-delimited and line-based; `--fleet <id>`
#     is unvalidated, so a tab in a directory name would shift every field and
#     print a remedy naming a truncated id that resolves to nothing.
TABDIR="$TMP/tabby"
mkdir -p "$TABDIR/$(printf 'fleet_1788000000_a\tb')"
printf '{"schema_version":1,"peers":[{"name":"a","removed":null}]}' > "$TABDIR/$(printf 'fleet_1788000000_a\tb')/fleet.json"
OUT="$(section28 "$TABDIR")"
# Discriminating form: the detail must sit immediately after the em dash and be
# followed by a NUMERIC age. Grepping for the detail alone passes even when the
# fields have shifted, because the substring survives in the wrong column —
# which is how the first version of this assertion went vacuous.
if grep -qE '— 1/1 peer\(s\) unreclaimed, [0-9?.]+h since last write' <<< "$OUT"; then
    assert_pass "a tab in a fleet id does not shift the record's fields"
else
    assert_fail "a tab in a fleet id does not shift the record's fields" "$OUT"
fi

# 6. ADVISORY NEUTRALITY, pinned to a workspace with KNOWN gaps.
#    Both assertions are differential and both run against a synthetic gappy
#    workspace, for one reason: on a machine that is already at N/N with zero
#    gaps, `--strict` exits 0 whatever §28 does, so an absolute assertion passes
#    vacuously and a neutrality regression ships. CI runs against a scaffold
#    that HAS gaps — which is how the absolute form was caught. Pinning the
#    workspace makes the check mean the same thing in both places: §28 must not
#    move the totals, and must not change the --strict verdict, when there are
#    real gaps for it to be confused with.
GAPPY="$TMP/gappy"
mkdir -p "$GAPPY/.control" "$GAPPY/.git"   # enough for doctor to accept it; governance files absent ⇒ gaps

T_CLEAN="$(BROOMVA_WORKSPACE="$GAPPY" BSTACK_FLEET_STATE_DIR="$TMP/empty" bash "$DOCTOR" --quiet 2>&1 | tail -1)"
T_DIRTY="$(BROOMVA_WORKSPACE="$GAPPY" BSTACK_FLEET_STATE_DIR="$TMP/live"  bash "$DOCTOR" --quiet 2>&1 | tail -1)"
if [ "$T_CLEAN" = "$T_DIRTY" ]; then
    assert_pass "an unreclaimed fleet does not move the doctor totals (gappy workspace)"
else
    assert_fail "an unreclaimed fleet does not move the doctor totals (gappy workspace)" "clean=[$T_CLEAN] dirty=[$T_DIRTY]"
fi

# Guard against the assertion above going vacuous: the pinned workspace must
# really have gaps, or "totals unchanged" proves nothing.
if grep -q "gap(s)" <<< "$T_DIRTY"; then
    assert_pass "the pinned workspace really has gaps (the neutrality check is not vacuous)"
else
    assert_fail "the pinned workspace really has gaps (the neutrality check is not vacuous)" "$T_DIRTY"
fi

# SOURCE-level neutrality. A runtime --strict assertion cannot discriminate:
# in a gappy workspace --strict exits 1 on both arms whatever §28 does, and in a
# zero-gap workspace it exits 0 on both. Either way it passes on the
# advisory-becomes-a-GAP mutant, so it is not a second gate — it only looked
# like one. PR #105 solved the same problem structurally; this does the same.
# Case 9 (the totals line, which carries the gap count) is the runtime gate;
# this is the independent one, and it fails on the mutant by construction.
SECTION_SRC="$(sed -n '/^section "28\./,/^# ── /p' "$BSTACK_REPO/scripts/doctor.sh")"
if [ -z "$SECTION_SRC" ]; then
    assert_fail "§28 source range is extractable" "sed range matched nothing"
elif grep -qE '(^|[^_[:alnum:]])(gap|ok)[[:space:]]+"' <<< "$SECTION_SRC"; then
    assert_fail "§28 calls neither gap() nor ok() — it cannot move the totals" \
        "$(grep -nE '(^|[^_[:alnum:]])(gap|ok)[[:space:]]+"' <<< "$SECTION_SRC" | head -2)"
else
    assert_pass "§28 calls neither gap() nor ok() — it cannot move the totals"
fi

echo ""
if [ "$FAIL" -eq 0 ]; then
    echo "  ✓ doctor §28: $PASS/$PASS passed"
    exit 0
fi
echo "  ✗ doctor §28: $FAIL failed, $PASS passed"
for t in "${FAILED_TESTS[@]}"; do echo "    - $t"; done
exit 1
