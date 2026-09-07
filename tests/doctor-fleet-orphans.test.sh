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
if grep -q "every fleet raised here was reclaimed" <<< "$OUT"; then
    assert_pass "an empty root reports every fleet reclaimed"
else
    assert_fail "an empty root reports every fleet reclaimed" "$OUT"
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

BROOMVA_WORKSPACE="$GAPPY" BSTACK_FLEET_STATE_DIR="$TMP/empty" bash "$DOCTOR" --quiet --strict >/dev/null 2>&1
RC_CLEAN=$?
BROOMVA_WORKSPACE="$GAPPY" BSTACK_FLEET_STATE_DIR="$TMP/live" bash "$DOCTOR" --quiet --strict >/dev/null 2>&1
RC_DIRTY=$?
if [ "$RC_CLEAN" -eq "$RC_DIRTY" ]; then
    assert_pass "an unreclaimed fleet does not change the --strict verdict (rc=$RC_DIRTY both ways)"
else
    assert_fail "an unreclaimed fleet does not change the --strict verdict" "clean rc=$RC_CLEAN dirty rc=$RC_DIRTY"
fi

echo ""
if [ "$FAIL" -eq 0 ]; then
    echo "  ✓ doctor §28: $PASS/$PASS passed"
    exit 0
fi
echo "  ✗ doctor §28: $FAIL failed, $PASS passed"
for t in "${FAILED_TESTS[@]}"; do echo "    - $t"; done
exit 1
