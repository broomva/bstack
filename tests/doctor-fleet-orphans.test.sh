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
SKIP=0
skip() { SKIP=$((SKIP + 1)); echo "  ~ SKIPPED (not run here): $1"; }
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
# The section's CENTRAL invariant, and the only thing separating clean from
# not-clean: when there is a finding, the clean line must be ABSENT. Every other
# assertion greps for presence, so a mutation of the `found` flag prints an
# orphan AND "nothing outstanding here" in the same breath and passes them all.
if grep -q "nothing outstanding here" <<< "$OUT"; then
    assert_fail "a named orphan suppresses the clean line" "$OUT"
else
    assert_pass "a named orphan suppresses the clean line"
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
    if grep -q "nothing outstanding here" <<< "$OUT"; then
        assert_fail "a malformed record suppresses the clean line ($bad)" "$OUT"
    else
        assert_pass "a malformed record suppresses the clean line ($bad)"
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
    # This root holds ONLY the malformed record — no valid FLEET row. It is the
    # one place a mutant that sets `found` on the FLEET path alone is visible;
    # every other negative assertion runs on a root that also has a real orphan,
    # so the valid row satisfies them and the mutant survives.
    if grep -q "nothing outstanding here" <<< "$OUT"; then
        assert_fail "an UNKNOWN-only finding suppresses the clean line ($bad)" "$OUT"
    else
        assert_pass "an UNKNOWN-only finding suppresses the clean line ($bad)"
    fi
done

# 5b-ter. A fleet.json that is not valid UTF-8 raises in read_text — before
#     json.loads, so the shape guard cannot see it. This is the input that
#     reaches the per-entry handler, and it proves the ISOLATION property: the
#     orphan sorting after it must still be named.
BINROOT="$TMP/binjson"
mkdir -p "$BINROOT/fleet_1000000000_aaaa" "$BINROOT/fleet_2000000000_zzzz"
printf '{"peers":[{"name":"\xff\xfe"}]}' > "$BINROOT/fleet_1000000000_aaaa/fleet.json"
cat > "$BINROOT/fleet_2000000000_zzzz/fleet.json" <<'EOF'
{"schema_version":1,"peers":[{"name":"wt-a","session_id":"a1","removed":null}]}
EOF
OUT="$(section28 "$BINROOT")"
if grep -q "fleet_1000000000_aaaa" <<< "$OUT"; then
    assert_pass "a non-UTF-8 fleet.json is reported"
else
    assert_fail "a non-UTF-8 fleet.json is reported" "$OUT"
fi
if grep -q "fleet_2000000000_zzzz" <<< "$OUT"; then
    assert_pass "a non-UTF-8 record does not suppress the fleet sorting after it"
else
    assert_fail "a non-UTF-8 record does not suppress the fleet sorting after it" "$OUT"
fi

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
    skip "unreadable root (running as root)"
fi

# 5d. A fleet_* entry that is not a directory must not be skipped into clean.
ND="$TMP/notdir"; mkdir -p "$ND"; ln -s /nonexistent "$ND/fleet_1788000000_link"
OUT="$(section28 "$ND")"
if grep -q "not a directory" <<< "$OUT"; then
    assert_pass "a fleet_* entry that is not a directory reports unknown"
else
    assert_fail "a fleet_* entry that is not a directory reports unknown" "$OUT"
fi

# 5d-bis. ROW INJECTION via the absent-root branch. The report is line-based, so
#     a newline in the state-root path can forge an entire FLEET row — a wholly
#     invented orphan with an invented remedy, which is the worst thing an
#     advisory section can print. clean() must be applied on EVERY branch that
#     echoes a path, including the one that reports the root as absent.
INJ="$TMP/absent-root"$'\n'"FLEET"$'\t'"fleet_INJECTED_9999"$'\t'"99/99 peer(s) unreclaimed"$'\t'"0.0"
OUT="$(section28 "$INJ")"
# Discriminating form: the payload string legitimately survives as INERT TEXT
# inside the sanitised NOROOT line, so grepping for it anywhere fails on the
# fixed code. What must not exist is a forged ROW — an [info] line whose subject
# is the injected id. (The first version of this assertion got that wrong.)
if grep -qE '^[[:space:]]*\[info\] fleet_INJECTED_9999' <<< "$OUT"; then
    assert_fail "a newline in the state-root path cannot forge a fleet row" "$OUT"
else
    assert_pass "a newline in the state-root path cannot forge a fleet row"
fi
# ...and the payload must be neutralised rather than echoed raw.
if grep -q "absent-root?FLEET" <<< "$OUT"; then
    assert_pass "the injected path is sanitised into one inert line"
else
    assert_fail "the injected path is sanitised into one inert line" "$OUT"
fi

# 5d-ter. The OTHER two root-echoing branches. clean() is applied on three
#     branches; only NOROOT was pinned, so a mutation of either of the others
#     could forge a full row from a newline in the root path and no case noticed.
FORGE=$'\n'"FLEET"$'\t'"fleet_FORGED_0001"$'\t'"99/99 peer(s) unreclaimed"$'\t'"0.0"

# (a) CLEAN branch: an existing, readable, EMPTY root whose name carries the payload.
EMPTY_FORGE="$TMP/base$FORGE"
mkdir -p "$EMPTY_FORGE"
OUT="$(section28 "$EMPTY_FORGE")"
if grep -q "nothing outstanding here" <<< "$OUT"; then
    assert_pass "the CLEAN forge fixture actually reaches the clean branch"
else
    assert_fail "the CLEAN forge fixture actually reaches the clean branch" "$OUT"
fi
if grep -qE '^[[:space:]]*\[info\] fleet_FORGED_0001' <<< "$OUT"; then
    assert_fail "the CLEAN branch cannot forge a fleet row" "$OUT"
else
    assert_pass "the CLEAN branch cannot forge a fleet row"
fi

# (b) unreadable-root branch: same payload, plus mode 000 so scandir raises.
if [ "$(id -u)" -ne 0 ]; then
    UNREAD_FORGE="$TMP/locked$FORGE"
    mkdir -p "$UNREAD_FORGE"; chmod 000 "$UNREAD_FORGE"
    OUT="$(section28 "$UNREAD_FORGE")"
    chmod 755 "$UNREAD_FORGE"
    if grep -q "not readable" <<< "$OUT"; then
        assert_pass "the unreadable-root forge fixture actually reaches its branch"
    else
        assert_fail "the unreadable-root forge fixture actually reaches its branch" "$OUT"
    fi
    if grep -qE '^[[:space:]]*\[info\] fleet_FORGED_0001' <<< "$OUT"; then
        assert_fail "the unreadable-root branch cannot forge a fleet row" "$OUT"
    else
        assert_pass "the unreadable-root branch cannot forge a fleet row"
    fi
else
    skip "unreadable-root forge (running as root)"
fi

# 5d-quinquies. The os.stat fix ADDED two root-echoing branches ("cannot be
#     read" and "is not a directory"), and the chmod-000 forge above does not
#     reach either: stat on a mode-000 directory succeeds (it needs +x on the
#     PARENT), so that case lands on the scandir branch. Every branch that
#     echoes the root gets its own forge fixture, or the sanitiser is pinned on
#     some branches and free on others — which is how (c) stayed open.
if [ "$(id -u)" -ne 0 ]; then
    # (c) stat-OSError branch: untraversable PARENT, payload in the root path.
    LP="$TMP/lockedparent2"; mkdir -p "$LP"; chmod 000 "$LP"
    OUT="$(section28 "$LP/root$FORGE")"
    chmod 755 "$LP"
    if grep -q "cannot be read" <<< "$OUT"; then
        assert_pass "the stat-unreadable forge fixture actually reaches its branch"
    else
        assert_fail "the stat-unreadable forge fixture actually reaches its branch" "$OUT"
    fi
    if grep -qE '^[[:space:]]*\[info\] fleet_FORGED_0001' <<< "$OUT"; then
        assert_fail "the stat-unreadable branch cannot forge a fleet row" "$OUT"
    else
        assert_pass "the stat-unreadable branch cannot forge a fleet row"
    fi
else
    skip "stat-unreadable forge (running as root)"
fi

# (d) not-a-directory branch: a FILE at a path carrying the payload. The payload
#     must be SLASH-FREE here — a filename cannot contain "/", so the usual
#     "99/99 peer(s)" form silently fails to create the fixture and the case
#     then tests the absent-root branch instead, passing for the wrong reason.
FORGE_FILE=$'\n'"FLEET"$'\t'"fleet_FORGED_0001"$'\t'"99 peers unreclaimed"$'\t'"0.0"
NOTDIR_FORGE="$TMP/rootfile$FORGE_FILE"
: > "$NOTDIR_FORGE"
if [ ! -e "$NOTDIR_FORGE" ]; then
    assert_fail "not-a-directory forge fixture was created" "creation failed — the case would test the wrong branch"
fi
OUT="$(section28 "$NOTDIR_FORGE")"
if grep -qE '^[[:space:]]*\[info\] fleet_FORGED_0001' <<< "$OUT"; then
    assert_fail "the not-a-directory branch cannot forge a fleet row" "$OUT"
else
    assert_pass "the not-a-directory branch cannot forge a fleet row"
fi

# 5d-quater. BLOCKER from round 3: the root check must classify the same way on
#     every interpreter. Path.is_dir() re-raises PermissionError on CPython
#     <= 3.12 and returns False on >= 3.13, so a root whose PARENT is not
#     traversable either crashed the section into an empty body or was reported
#     as absent. os.stat raises everywhere, so this asserts BEHAVIOUR, not a
#     version: unreadable is unknown, and it is never silently clean or absent.
if [ "$(id -u)" -ne 0 ]; then
    LOCKED="$TMP/lockedparent"
    mkdir -p "$LOCKED/root"; chmod 000 "$LOCKED"
    OUT="$(section28 "$LOCKED/root")"
    chmod 755 "$LOCKED"
    if grep -q "state root cannot be read" <<< "$OUT"; then
        assert_pass "a root under an untraversable parent reports unknown"
    else
        assert_fail "a root under an untraversable parent reports unknown" "$OUT"
    fi
    if grep -q "nothing to check here" <<< "$OUT" || grep -q "nothing outstanding here" <<< "$OUT"; then
        assert_fail "an unreadable root is never reported absent or clean" "$OUT"
    else
        assert_pass "an unreadable root is never reported absent or clean"
    fi
    # The section must have a BODY: an empty body is the crash signature.
    if [ "$(grep -c '\[info\]' <<< "$OUT")" -ge 1 ]; then
        assert_pass "the section still renders a body (no interpreter crash)"
    else
        assert_fail "the section still renders a body (no interpreter crash)" "$OUT"
    fi
else
    skip "untraversable parent (running as root)"
fi

# A file where a directory is expected is also not clean.
NOTDIR_ROOT="$TMP/rootisfile"; : > "$NOTDIR_ROOT"
OUT="$(section28 "$NOTDIR_ROOT")"
if grep -q "not a directory" <<< "$OUT"; then
    assert_pass "a state root that is a file reports unknown"
else
    assert_fail "a state root that is a file reports unknown" "$OUT"
fi

# 5f. TOTALITY. The section's headline promise is that no input empties the
#     report, because an empty body reads as clean. Four review rounds each
#     found one more input that broke it, so these assert the PROPERTY, on both
#     of the two surfaces a root can arrive from.
#
#     (i) an undecodable byte in the state-root path. Env vars decode with
#     surrogateescape, so a lone surrogate reaches print and raises
#     UnicodeEncodeError — and the handler's own print raised again, so the
#     exception escaped the loop entirely.
NONUTF8=$'/tmp/bstack-p28-nonutf8-\xff/fleet'
OUT="$(section28 "$NONUTF8")"
if [ "$(grep -c '\[info\]' <<< "$OUT")" -ge 1 ]; then
    assert_pass "an undecodable byte in the root path still renders a body"
else
    assert_fail "an undecodable byte in the root path still renders a body" "EMPTY BODY"
fi
# Content, not just presence: without the stdout reconfigure the print raises
# and the OUTER guard emits a generic "the fleet scan failed" row — also a
# body. Only naming the root proves the encoding fix itself is doing the work.
if grep -q "no fleet state root at" <<< "$OUT"; then
    assert_pass "the undecodable root is classified, not swallowed by the outer guard"
else
    assert_fail "the undecodable root is classified, not swallowed by the outer guard" "$OUT"
fi
ERR="$(BSTACK_FLEET_STATE_DIR="$NONUTF8" bash "$DOCTOR" --quiet 2>&1 >/dev/null)"
if grep -q "Traceback" <<< "$ERR"; then
    assert_fail "no traceback leaks to stderr under --quiet (undecodable root)" "$ERR"
else
    assert_pass "no traceback leaks to stderr under --quiet (undecodable root)"
fi

#     (ii) a NUL byte in the CONFIG's fleet_state_dir. os.stat raises
#     ValueError, which is not an OSError, so an OSError-only handler missed it.
#     This route needs BSTACK_STATE_DIR + BROOMVA_WORKSPACE, which section28()
#     does not set — so it gets its own invocation rather than being skipped.
NULSD="$TMP/nulstate"; mkdir -p "$NULSD"
printf 'fleet_state_dir: /tmp/bstack-p28-nul-\000-root\n' > "$NULSD/config.yaml"
NULWS="$TMP/nulws"; mkdir -p "$NULWS/.control" "$NULWS/.git"
OUT="$(BROOMVA_WORKSPACE="$NULWS" BSTACK_STATE_DIR="$NULSD" bash "$DOCTOR" 2>/dev/null \
        | sed -n '/28. Unreclaimed fleets/,/^$/p')"
if [ "$(grep -c '\[info\]' <<< "$OUT")" -ge 1 ]; then
    assert_pass "a NUL in the config state-dir still renders a body"
else
    assert_fail "a NUL in the config state-dir still renders a body" "EMPTY BODY"
fi
if grep -q "unknown, not clean" <<< "$OUT"; then
    assert_pass "a NUL in the config state-dir reports unknown, not clean"
else
    assert_fail "a NUL in the config state-dir reports unknown, not clean" "$OUT"
fi
# The SPECIFIC branch: os.stat raises ValueError on an embedded NUL, which is
# not an OSError. Narrowing that handler back to OSError still renders a body
# (the outer guard catches it), so only the branch text discriminates.
if grep -q "state root cannot be read" <<< "$OUT"; then
    assert_pass "the NUL root is classified by the stat handler, not the outer guard"
else
    assert_fail "the NUL root is classified by the stat handler, not the outer guard" "$OUT"
fi
# And the NUL itself must be neutralised in the emitted line.
if grep -q 'p28-nul-?-root' <<< "$OUT"; then
    assert_pass "the NUL byte is sanitised out of the emitted record"
else
    assert_fail "the NUL byte is sanitised out of the emitted record" "$OUT"
fi
ERR="$(BROOMVA_WORKSPACE="$NULWS" BSTACK_STATE_DIR="$NULSD" bash "$DOCTOR" --quiet 2>&1 >/dev/null)"
if grep -q "Traceback" <<< "$ERR"; then
    assert_fail "no traceback leaks to stderr under --quiet (NUL config root)" "$ERR"
else
    assert_pass "no traceback leaks to stderr under --quiet (NUL config root)"
fi

# 5g. DELETED CWD. os.getcwd() raises FileNotFoundError when the invoking
#     directory has been removed — routine here, since make janitor removes
#     worktrees while sessions are live, and doctor runs from a SessionStart
#     hook in an arbitrary directory. The prologue used to run OUTSIDE the
#     scan guard, so this emptied the section and leaked a traceback while
#     doctor still reported the workspace fully compliant.
#     BROOMVA_WORKSPACE is pinned here on purpose. With a deleted cwd, doctor's
#     OWN bootstrap cannot resolve a workspace on Linux and exits before any
#     section runs — so without the pin this case tests doctor's bootstrap, not
#     §28, and it passed on macOS while failing on Linux for a reason that had
#     nothing to do with the fix. Whether doctor itself should survive a deleted
#     cwd is a separate question, outside this section's scope.
DEADCWD="$TMP/deadcwd"
DEADWS="$TMP/deadws"; mkdir -p "$DEADWS/.control" "$DEADWS/.git"
mkdir -p "$DEADCWD"
OUT="$(cd "$DEADCWD" && rmdir "$DEADCWD" \
        && BROOMVA_WORKSPACE="$DEADWS" BSTACK_FLEET_STATE_DIR="$TMP/live" bash "$DOCTOR" 2>/dev/null \
        | sed -n '/28. Unreclaimed fleets/,/^$/p')"
# Guard first: if doctor never reached §28 the assertions below would be
# testing nothing, which is how this case failed in CI while passing locally.
if grep -q "28. Unreclaimed fleets" <<< "$OUT"; then
    assert_pass "doctor reaches §28 with a deleted cwd (the case is not vacuous)"
else
    assert_fail "doctor reaches §28 with a deleted cwd (the case is not vacuous)" "section never rendered"
fi
if [ "$(grep -c '\[info\]' <<< "$OUT")" -ge 1 ]; then
    assert_pass "a deleted working directory still renders a body"
else
    assert_fail "a deleted working directory still renders a body" "EMPTY BODY"
fi
if grep -q "fleet_1788000000_aaaa" <<< "$OUT"; then
    assert_pass "a deleted working directory still names the orphan"
else
    assert_fail "a deleted working directory still names the orphan" "$OUT"
fi
mkdir -p "$DEADCWD"
ERR="$(cd "$DEADCWD" && rmdir "$DEADCWD" \
        && BROOMVA_WORKSPACE="$DEADWS" BSTACK_FLEET_STATE_DIR="$TMP/live" bash "$DOCTOR" --quiet 2>&1 >/dev/null)"
if grep -q "Traceback" <<< "$ERR"; then
    assert_fail "no traceback leaks under --quiet with a deleted cwd" "$ERR"
else
    assert_pass "no traceback leaks under --quiet with a deleted cwd"
fi

# 5h. NON-TERMINATION. A FIFO passes exists() and then BLOCKS at open() until a
#     writer appears. This is not an exception, so no handler can catch it and
#     the outer guard is irrelevant — doctor would hang forever under a hook
#     with no timeout above it. The assertion is wrapped in `timeout` on
#     purpose: without it a regression HANGS the CI job instead of failing it,
#     which is a worse outcome than the bug.
if command -v mkfifo >/dev/null 2>&1 && command -v timeout >/dev/null 2>&1; then
    FIFOROOT="$TMP/fiforoot"; mkdir -p "$FIFOROOT/fleet_1788000000_fff"
    mkfifo "$FIFOROOT/fleet_1788000000_fff/fleet.json"
    if OUT="$(timeout 20 env BSTACK_FLEET_STATE_DIR="$FIFOROOT" bash "$DOCTOR" 2>/dev/null \
                | sed -n '/28. Unreclaimed fleets/,/^$/p')"; then
        if grep -q "not a regular file" <<< "$OUT"; then
            assert_pass "a FIFO fleet.json is refused, not opened"
        else
            assert_fail "a FIFO fleet.json is refused, not opened" "$OUT"
        fi
    else
        assert_fail "a FIFO fleet.json is refused, not opened" "doctor HUNG (timeout) — non-termination"
    fi
    rm -f "$FIFOROOT/fleet_1788000000_fff/fleet.json"
else
    skip "FIFO non-termination guard (mkfifo or timeout unavailable)"
fi

# 5i. ESCAPE SEQUENCES. clean() keeps only printable characters, so a name
#     carrying ESC cannot erase or overwrite a line already printed above it.
#     Entries are emitted sorted, so an erase-line + cursor-up in a name that
#     sorts LATER can overwrite a real orphan — forging a row by deleting one.
ESCROOT="$TMP/escroot"
ESCNAME="fleet_1788000000_a$(printf '\033')[2K$(printf '\033')[1Ab"
mkdir -p "$ESCROOT/$ESCNAME"
printf '{"schema_version":1,"peers":[{"name":"a","removed":null}]}' > "$ESCROOT/$ESCNAME/fleet.json"
OUT="$(section28 "$ESCROOT")"
if printf '%s' "$OUT" | LC_ALL=C grep -q "$(printf '\033')"; then
    assert_fail "an ESC in a fleet id never reaches the terminal" "raw ESC survived into the output"
else
    assert_pass "an ESC in a fleet id never reaches the terminal"
fi
if grep -q "1/1 peer(s) unreclaimed" <<< "$OUT"; then
    assert_pass "the ESC-bearing fleet is still reported"
else
    assert_fail "the ESC-bearing fleet is still reported" "$OUT"
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

# 5j. THE PROCESS BOUNDARY. `command -v python3` proves presence, not that the
#     interpreter runs: a pyenv shim for an uninstalled version is +x and exits
#     127. The substitution used to discard the status and leak stderr, so §28
#     rendered a header with no body while an orphan sat on disk — the same
#     class as every prior round, one scope wider.
BADPY="$TMP/badpy"; mkdir -p "$BADPY"
printf '#!/bin/sh\necho "pyenv: version 3.99.0 is not installed" >&2\nexit 127\n' > "$BADPY/python3"
chmod +x "$BADPY/python3"
OUT="$(PATH="$BADPY:$PATH" BSTACK_FLEET_STATE_DIR="$TMP/live" bash "$DOCTOR" 2>/dev/null \
        | sed -n '/28. Unreclaimed fleets/,/^$/p')"
if [ "$(grep -c '\[info\]' <<< "$OUT")" -ge 1 ]; then
    assert_pass "a broken python3 still renders a body"
else
    assert_fail "a broken python3 still renders a body" "EMPTY BODY"
fi
if grep -q "the fleet probe did not run" <<< "$OUT"; then
    assert_pass "a broken python3 is reported as a probe failure, not as clean"
else
    assert_fail "a broken python3 is reported as a probe failure, not as clean" "$OUT"
fi
# The stderr axis, asserted against the REAL line. A behavioural check cannot
# isolate it — other sections call python3 too, and with a broken interpreter
# their noise is pre-existing and swamps the measurement — and the first
# version of this assertion ran the pattern in a standalone shell, so it tested
# a copy of the code rather than the code and survived mutation. Source-level
# is the honest form here.
if grep -qE 'python3 - "\$BSTACK_REPO/scripts" +2>/dev/null <<' "$BSTACK_REPO/scripts/doctor.sh"; then
    assert_pass "§28's own python invocation redirects its stderr"
else
    assert_fail "§28's own python invocation redirects its stderr" \
        "$(grep -n 'python3 - "\$BSTACK_REPO/scripts"' "$BSTACK_REPO/scripts/doctor.sh")"
fi

# 5k. ENCODING. sys.stdout.reconfigure was unpinned once clean() became
#     printable-only: a printable non-ASCII id survives clean() and then fails
#     to encode under an ASCII stdout. Without reconfigure the outer guard fires
#     and the body is non-empty — but the ORPHAN IS NOT NAMED, which is the
#     discriminating property, not "a body rendered".
ACCROOT="$TMP/accented"; mkdir -p "$ACCROOT/fleet_1788000000_caf$(printf '\303\251')"
printf '{"schema_version":1,"peers":[{"name":"a","removed":null}]}' \
    > "$ACCROOT/fleet_1788000000_caf$(printf '\303\251')/fleet.json"
OUT="$(PYTHONIOENCODING=ascii BSTACK_FLEET_STATE_DIR="$ACCROOT" bash "$DOCTOR" 2>/dev/null \
        | sed -n '/28. Unreclaimed fleets/,/^$/p')"
if grep -q "fleet_1788000000_caf" <<< "$OUT"; then
    assert_pass "an ASCII stdout still names a non-ASCII fleet id"
else
    assert_fail "an ASCII stdout still names a non-ASCII fleet id" "$OUT"
fi
if grep -q "the fleet scan failed" <<< "$OUT"; then
    assert_fail "an ASCII stdout does not fall through to the outer guard" "$OUT"
else
    assert_pass "an ASCII stdout does not fall through to the outer guard"
fi

# 5l. SCHEMA. fleet.py refuses a schema_version it does not know; reading v1
#     fields out of such a record and printing a count is guessing, and the
#     remedy would name a command that errors.
V9="$TMP/schema9"; mkdir -p "$V9/fleet_1788000000_v99"
printf '{"schema_version":999,"peers":[{"name":"a"},{"name":"b"}]}' > "$V9/fleet_1788000000_v99/fleet.json"
OUT="$(section28 "$V9")"
if grep -q "schema_version=999" <<< "$OUT"; then
    assert_pass "an unknown schema_version reports unknown, not a peer count"
else
    assert_fail "an unknown schema_version reports unknown, not a peer count" "$OUT"
fi
if grep -q "peer(s) unreclaimed" <<< "$OUT"; then
    assert_fail "an unknown schema_version does not print a fabricated count" "$OUT"
else
    assert_pass "an unknown schema_version does not print a fabricated count"
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
# Comment lines are stripped first: with the quote anchor loosened (so
# `gap 'single'` cannot slip past), ordinary prose containing "ok " or "gap "
# inside the section would otherwise fail the suite. Calls survive the strip.
SECTION_SRC="$(sed -n '/^section "28\./,/^# ── /p' "$BSTACK_REPO/scripts/doctor.sh" | grep -v '^[[:space:]]*#')"
if [ -z "$SECTION_SRC" ]; then
    assert_fail "§28 source range is extractable" "sed range matched nothing"
elif grep -qE '(^|[^_[:alnum:]])(gap|ok)[[:space:]]|^[[:space:]]*(GAPS|PASSES)=' <<< "$SECTION_SRC"; then
    assert_fail "§28 calls neither gap() nor ok() — it cannot move the totals" \
        "$(grep -nE '(^|[^_[:alnum:]])(gap|ok)[[:space:]]|^[[:space:]]*(GAPS|PASSES)=' <<< "$SECTION_SRC" | head -2)"
else
    assert_pass "§28 calls neither gap() nor ok() — it cannot move the totals"
fi

# The outer scan guard is the backstop for inputs nobody anticipated. Every
# input this suite can construct is already classified by an inner guard, so it
# is deliberately unreachable by black-box test — and an unreachable guard is
# exactly the kind that gets deleted in a refactor. Assert it structurally, and
# declare the remainder rather than implying coverage that does not exist.
if awk '/^try:$/{t=NR} /^    scan\(\)$/{if (NR==t+1) s=NR} /^except Exception as exc:/{if (s && NR==s+1) print "WRAPS"}' \
        "$BSTACK_REPO/scripts/doctor.sh" | grep -q WRAPS; then
    assert_pass "the outer scan guard is present (structural; not reachable by any constructible input)"
else
    assert_fail "the outer scan guard is present (structural; not reachable by any constructible input)"
fi

# Same class, one layer down: emit() is total on the python side, so the shell
# read loop's catch-all arm cannot be reached by any input either. A read side
# that enumerates four kinds and silently drops the rest is the same silence
# this section exists to prevent, so its presence is asserted structurally too.
if awk '/^section "28\./{f=1} f && /^ *\*\)$/{d=1} d && /echo/ && /\$_k/{print "EMITS"} /^# \xe2\x94\x80\xe2\x94\x80 summary/{exit}' \
        "$BSTACK_REPO/scripts/doctor.sh" | grep -q EMITS; then
    assert_pass "the shell read loop has a catch-all arm (structural; unreachable while emit() is total)"
else
    assert_fail "the shell read loop has a catch-all arm (structural; unreachable while emit() is total)"
fi

echo ""
if [ "$FAIL" -eq 0 ]; then
    echo "  ✓ doctor §28: $PASS/$PASS passed${SKIP:+ ($SKIP skipped — not coverage)}"
    exit 0
fi
echo "  ✗ doctor §28: $FAIL failed, $PASS passed"
for t in "${FAILED_TESTS[@]}"; do echo "    - $t"; done
exit 1
