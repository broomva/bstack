#!/usr/bin/env bash
# tests/workflow-templates.test.sh — BRO-2542. Runs the shipped workflow templates
# (references/templates/workflows/bands.yml and intent-to-spec.yml) end to end, one
# `run:` step at a time, under tests/fixtures/workflow-sim/sim.py: bash as GitHub runs
# it, a bare local origin, a fake gh and a fake claude.
#
# Why this exists: reverting `--state open` to `--state all`, or dropping `--force` from
# the bot's push, left every other in-repo test green. The template logic has to be able
# to fail somewhere, so these scenarios assert on what the fakes logged (claude's argv,
# cwd, token and stdin; every PR opened) and on origin's refs, not on exit codes alone.
# The fake gh applies each template's real `-q` expression with jq, so the fork filter
# is evaluated rather than assumed.
#
# There is deliberately NO skip path. A missing git, jq or PyYAML FAILS the run, and so
# does a run in which fewer than 8 scenarios reached their assertions.
#
# WF_BANDS / WF_INTENT point the scenarios at another copy of a template;
# tests/fixtures/workflow-sim/mutants.py uses them to show that each reverted fix goes red.
# CI sets neither.
#
# Run from anywhere:
#   bash tests/workflow-templates.test.sh

set -uo pipefail

BSTACK_REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$BSTACK_REPO" || exit 1
SIMDIR="$BSTACK_REPO/tests/fixtures/workflow-sim"
WF_BANDS="${WF_BANDS:-$BSTACK_REPO/references/templates/workflows/bands.yml}"
WF_INTENT="${WF_INTENT:-$BSTACK_REPO/references/templates/workflows/intent-to-spec.yml}"
BANDS_EXAMPLE="$BSTACK_REPO/references/templates/bands.example.yaml"
SCENARIOS="1 2 3 4 5 6 7 8"
MIN_SCENARIOS=8
MIN_ASSERTS=5
NAME="alpha-widget"

PASS=0
FAIL=0
FAILED_TESTS=()
RAN=0
DONE=" "
FAILED_SCENARIOS=""
SC=""
SC_ASSERTS=0
SC_FAILS=0

assert_pass() { PASS=$((PASS + 1)); SC_ASSERTS=$((SC_ASSERTS + 1)); echo "  [pass] $1"; }
assert_fail() {
    FAIL=$((FAIL + 1)); SC_ASSERTS=$((SC_ASSERTS + 1)); SC_FAILS=$((SC_FAILS + 1))
    FAILED_TESTS+=("scenario $SC: $1"); echo "  [FAIL] $1"
    [ -n "${2:-}" ] && echo "         $2"
    return 0
}
# ok DESC CMD... — passes when CMD succeeds.
ok() { local d=$1; shift; if "$@"; then assert_pass "$d"; else assert_fail "$d"; fi; }
# eq DESC WANT GOT
eq() {
    if [ "$3" = "$2" ]; then assert_pass "$1"; else assert_fail "$1" "want: $2 | got: $3"; fi
}
not() { ! "$@"; }
contains() { case "$1" in *"$2"*) return 0 ;; *) return 1 ;; esac; }

echo "── workflow templates, simulated end to end (BRO-2542) ────────────"
echo "  bands.yml:          $WF_BANDS"
echo "  intent-to-spec.yml: $WF_INTENT"

missing=""
command -v git >/dev/null 2>&1 || missing="$missing git"
command -v jq >/dev/null 2>&1 || missing="$missing jq"
python3 -c 'import yaml' >/dev/null 2>&1 || missing="$missing python3-with-PyYAML"
for f in sim.py mkruns.py fakebin/gh fakebin/claude; do
    [ -f "$SIMDIR/$f" ] || missing="$missing $SIMDIR/$f"
done
for f in fakebin/gh fakebin/claude; do
    [ -x "$SIMDIR/$f" ] || missing="$missing executable:$f"
done
for f in "$WF_BANDS" "$WF_INTENT" "$BANDS_EXAMPLE"; do
    [ -f "$f" ] || missing="$missing $f"
done
if [ -n "$missing" ]; then
    echo "  [FAIL] missing:$missing"
    echo "         Not skipping: a green wrapper around scenarios that never ran is worse"
    echo "         than no wrapper. CI's ubuntu-latest has git and jq; ci.yml installs PyYAML."
    exit 1
fi

# Scratch space lives under TMPDIR and is left in place, printed, for debugging.
ROOT="$(mktemp -d "${TMPDIR:-/tmp}/workflow-templates.XXXXXX")" || exit 1
echo "  scratch:            $ROOT"
HHOME="$ROOT/harness-home"
mkdir -p "$HHOME"

# The harness's own git, isolated from the caller's config (hooks, LFS, signing).
hgit() {
    HOME="$HHOME" XDG_CONFIG_HOME="$HHOME/.config" GIT_CONFIG_NOSYSTEM=1 GIT_TERMINAL_PROMPT=0 \
        git -c user.name=seed -c user.email=seed@example.invalid -c core.hooksPath=/dev/null \
            -c commit.gpgsign=false -c init.defaultBranch=main "$@"
}
oref() { hgit --git-dir "$1/origin.git" rev-parse --verify -q "refs/heads/$2"; }
lines() { if [ -f "$1" ]; then wc -l < "$1" | tr -d ' '; else echo 0; fi; }
real() { (cd "$1" 2>/dev/null && pwd -P); }
lastcall() { tail -n 1 "$S/claude.log" | jq -r "$1"; }
lastpr() { tail -n 1 "$S/pr_create.log" | jq -r "$1"; }
job() { jq -r "$1" "$S/job.json" 2>/dev/null; }
step_stdout() { jq -r '[.steps[] | .stdout // empty] | join("\n")' "$S/job.json" 2>/dev/null; }
open_prs() {
    jq --arg h "$1" '[.[] | select(.headRefName == $h and .state == "OPEN"
                                    and (.isCrossRepository | not))] | length' "$S/prs.json"
}
close_pr() {
    jq --arg h "$1" 'map(if .headRefName == $h then .state = "CLOSED" else . end)' \
        "$S/prs.json" > "$S/prs.tmp" && mv "$S/prs.tmp" "$S/prs.json"
}

new_sim() {
    mkdir -p "$1" &&
        hgit init -q --bare -b main "$1/origin.git" &&
        hgit init -q -b main "$1/seed" &&
        echo "# fixture repo" > "$1/seed/README.md" &&
        cp "$ROOT/runs.json" "$1/runs.json" &&
        echo '[]' > "$1/prs.json" &&
        echo ok > "$1/claude.mode" &&
        : > "$1/gh.log" && : > "$1/claude.log" && : > "$1/pr_create.log" &&
        : > "$1/violations.log"
}
seed_commit() {
    hgit -C "$1/seed" add -A && hgit -C "$1/seed" commit -q -m "$2" &&
        hgit -C "$1/seed" push -q "$1/origin.git" main
}
# checkout SIM DIR DEPTH — actions/checkout with persist-credentials: false; DEPTH 1 is
# its default (bands.yml), 0 is fetch-depth: 0 (intent-to-spec.yml).
checkout() {
    if [ "$3" = 1 ]; then
        hgit clone -q --depth 1 "file://$1/origin.git" "$1/$2"
    else
        hgit clone -q "$1/origin.git" "$1/$2"
    fi
}
run_sim() {
    local wf=$1 sim=$2 co=$3
    shift 3
    python3 "$SIMDIR/sim.py" "$wf" "$sim" "$sim/$co" "$@" > "$sim/sim.out" 2>&1
    SIM_RC=$?
}
setup_bands() {
    new_sim "$1" &&
        mkdir -p "$1/seed/.control/bands" &&
        cp "$BANDS_EXAMPLE" "$1/seed/.control/bands/ci-failure-rate.yaml" &&
        seed_commit "$1" seed &&
        checkout "$1" co1 1
}
write_intent() {
    mkdir -p "$(dirname "$1")" && cat > "$1" <<EOF
# Intent: Alpha widget

Author: Fixture Author. Status: accepted.

## Problem

$2

## Proposed outcome

Operators see the widget's state on the dashboard.

## Affected users and systems

The dashboard and the operators who read it.

## Constraints

None beyond the existing API.

## Open questions

None.
EOF
}
# setup_intent SIM — main holds a seed commit (INTENT_BEFORE), then a push adds one
# accepted intent; the checkout is at the pushed commit.
setup_intent() {
    new_sim "$1" && seed_commit "$1" seed &&
        INTENT_BEFORE="$(oref "$1" main)" &&
        write_intent "$1/seed/intent/$NAME.md" "Operators cannot see the widget's state." &&
        seed_commit "$1" "intent: $NAME" &&
        checkout "$1" co1 0
}
no_violations() {
    eq "no forbidden or unmodelled gh/claude call (auth setup-git, ...)" 0 \
        "$(lines "$S/violations.log")"
}

begin() {
    SC=$1
    SC_ASSERTS=0
    SC_FAILS=0
    echo
    echo "── scenario $1: $2"
}
finish() {
    [ "$SC_ASSERTS" -ge "$MIN_ASSERTS" ] ||
        assert_fail "at least $MIN_ASSERTS assertions ran" "only $SC_ASSERTS"
    RAN=$((RAN + 1))
    DONE="$DONE$SC "
    if [ "$SC_FAILS" -gt 0 ]; then
        FAILED_SCENARIOS="$FAILED_SCENARIOS $SC"
        echo "  ── simulator transcript (tail) ──"
        tail -n 40 "$S/sim.out" 2>/dev/null | sed 's/^/    /'
        [ -s "$S/violations.log" ] && sed 's/^/    violation: /' "$S/violations.log"
        echo "  scenario $SC: FAIL"
    else
        echo "  scenario $SC: PASS"
    fi
}

# ── the breach every bands scenario sees ─────────────────────────────────
python3 "$SIMDIR/mkruns.py" "$ROOT/runs.json" &&
    python3 scripts/bands.py series ci-failure-rate --runs-json "$ROOT/runs.json" \
        > "$ROOT/series.json" &&
    python3 scripts/bands.py check "$BANDS_EXAMPLE" --series "$ROOT/series.json" --json \
        > "$ROOT/result.json"
TIER="$(jq -r '.tier // empty' "$ROOT/result.json" 2>/dev/null)"
KEY="$(jq -r '"\(.metric)-\(.tier)"' "$ROOT/result.json" 2>/dev/null)"
case "$TIER" in
    2sigma | 3sigma) echo "  fixture breach:     $KEY" ;;
    *)
        echo "  [FAIL] the run-list fixture does not breach the shipped band (tier: '${TIER}')"
        echo "         every bands scenario would be vacuous; fix tests/fixtures/workflow-sim/mkruns.py"
        exit 1
        ;;
esac

# ── bands.yml ────────────────────────────────────────────────────────────
S="$ROOT/bands"
begin 1 "bands: fresh breach"
setup_bands "$S" || assert_fail "fixture setup"
run_sim "$WF_BANDS" "$S" co1
eq "job succeeds" success "$(job .result)"
eq "the check step keys the breach" "$KEY" "$(job '.outputs["check.key"] // ""')"
eq "exactly 1 claude call" 1 "$(lines "$S/claude.log")"
RT="$(job .runner_temp)"
eq "claude's cwd is the throwaway clone" "$(real "$RT/diag")" "$(lastcall .cwd)"
eq "claude's cwd is not the checkout" false \
    "$([ "$(lastcall .cwd)" = "$(real "$S/co1")" ] && echo true || echo false)"
eq "claude sees no GH_TOKEN" false "$(lastcall .GH_TOKEN)"
eq "claude's stdin is empty" 0 "$(lastcall .stdin_bytes)"
eq "claude's stdin is /dev/null" true "$(lastcall .stdin_devnull)"
eq "the clone's git config holds no credential" false "$(lastcall .gitconfig_has_cred)"
eq "the clone holds .bands/intent.md" true "$(lastcall '.bands_files | index("intent.md") != null')"
eq "exactly 1 pr create" 1 "$(lines "$S/pr_create.log")"
eq "the PR's head is bands/<key>" "bands/$KEY" "$(lastpr .head)"
TIP="$(oref "$S" "bands/$KEY")"
ok "bands/<key> is on origin" test -n "$TIP"
eq "the PR's head is origin's bands/<key>" "$TIP" "$(lastpr .tip)"
FILE="$(job '.outputs["diag.file"] // ""')"
ok "the committed intent carries claude's reply, appended by the workflow" \
    contains "$(hgit --git-dir "$S/origin.git" show "$TIP:$FILE" 2>/dev/null)" "FAKE-DIAGNOSIS call-1"
no_violations
finish

begin 2 "bands: re-run while that PR is open"
eq "precondition: one open non-fork PR on bands/<key>" 1 "$(open_prs "bands/$KEY")"
C0="$(lines "$S/claude.log")"
P0="$(lines "$S/pr_create.log")"
TIP0="$(oref "$S" "bands/$KEY")"
checkout "$S" co2 1 || assert_fail "fixture setup"
run_sim "$WF_BANDS" "$S" co2
eq "job succeeds" success "$(job .result)"
eq "the open-PR check reports it open" true "$(job '.outputs["open.open"] // ""')"
eq "0 new claude calls" "$C0" "$(lines "$S/claude.log")"
eq "0 new PRs" "$P0" "$(lines "$S/pr_create.log")"
eq "origin's bands/<key> is untouched" "$TIP0" "$(oref "$S" "bands/$KEY")"
no_violations
finish

begin 3 "bands: PR closed, its branch left on origin, fresh checkout"
close_pr "bands/$KEY"
echo "main moves on" >> "$S/seed/README.md"
seed_commit "$S" "an unrelated change lands on main" || assert_fail "fixture setup"
OLD="$(oref "$S" "bands/$KEY")"
eq "precondition: no open PR on bands/<key>" 0 "$(open_prs "bands/$KEY")"
ok "precondition: the left-behind branch has a commit main does not" \
    not hgit --git-dir "$S/origin.git" merge-base --is-ancestor "$OLD" refs/heads/main
C0="$(lines "$S/claude.log")"
P0="$(lines "$S/pr_create.log")"
checkout "$S" co3 1 || assert_fail "fixture setup"
run_sim "$WF_BANDS" "$S" co3
eq "job succeeds" success "$(job .result)"
eq "1 new claude call" $((C0 + 1)) "$(lines "$S/claude.log")"
eq "1 new pr create" $((P0 + 1)) "$(lines "$S/pr_create.log")"
eq "the new PR's head is bands/<key>" "bands/$KEY" "$(lastpr .head)"
NEW="$(oref "$S" "bands/$KEY")"
ok "origin's bands/<key> was replaced" test "$NEW" != "$OLD"
ok "the replacement is not a fast-forward of the old tip (the push needed --force)" \
    not hgit --git-dir "$S/origin.git" merge-base --is-ancestor "$OLD" "$NEW"
eq "the replacement is built on the current main" "$(oref "$S" main)" \
    "$(hgit --git-dir "$S/origin.git" rev-parse -q --verify "$NEW^" 2>/dev/null)"
no_violations
finish

S="$ROOT/bands-fork"
begin 4 "bands: only a FORK has an open PR on the same head"
setup_bands "$S" || assert_fail "fixture setup"
jq -n --arg h "bands/$KEY" \
    '[{number: 41, headRefName: $h, state: "OPEN", isCrossRepository: true}]' > "$S/prs.json"
run_sim "$WF_BANDS" "$S" co1
eq "job succeeds" success "$(job .result)"
eq "the open-PR check ignores the fork" false "$(job '.outputs["open.open"] // ""')"
eq "exactly 1 claude call" 1 "$(lines "$S/claude.log")"
eq "exactly 1 pr create" 1 "$(lines "$S/pr_create.log")"
eq "the PR's head is bands/<key>" "bands/$KEY" "$(lastpr .head)"
ok "bands/<key> is on origin" test -n "$(oref "$S" "bands/$KEY")"
eq "the new PR is from this repo" 1 "$(open_prs "bands/$KEY")"
no_violations
finish

# ── intent-to-spec.yml ───────────────────────────────────────────────────
S="$ROOT/intent"
begin 5 "intent-to-spec: one accepted intent without a spec"
setup_intent "$S" || assert_fail "fixture setup"
run_sim "$WF_INTENT" "$S" co1 vars.SDLC_INTENT_TO_SPEC=true "github.event.before=$INTENT_BEFORE"
eq "job succeeds" success "$(job .result)"
eq "exactly 1 claude call" 1 "$(lines "$S/claude.log")"
RT="$(job .runner_temp)"
eq "claude's cwd is the throwaway clone" "$(real "$RT/src-$NAME")" "$(lastcall .cwd)"
ok "claude's prompt names the intent" contains "$(lastcall '.argv[1]')" "intent/$NAME.md"
eq "claude sees no GH_TOKEN" false "$(lastcall .GH_TOKEN)"
eq "claude's stdin is empty" 0 "$(lastcall .stdin_bytes)"
eq "claude's stdin is /dev/null, not the loop's input" true "$(lastcall .stdin_devnull)"
eq "the clone's git config holds no credential" false "$(lastcall .gitconfig_has_cred)"
eq "exactly 1 pr create" 1 "$(lines "$S/pr_create.log")"
eq "the PR's head is spec/<name>" "spec/$NAME" "$(lastpr .head)"
TIP="$(oref "$S" "spec/$NAME")"
ok "spec/<name> is on origin" test -n "$TIP"
eq "docs/specs/<name>.md on the branch is claude's reply, written by the workflow" \
    "$(lastcall .reply)" "$(hgit --git-dir "$S/origin.git" show "$TIP:docs/specs/$NAME.md" 2>/dev/null)"
eq "spec/<name> is one commit on main" "$(oref "$S" main)" \
    "$(hgit --git-dir "$S/origin.git" rev-parse -q --verify "$TIP^" 2>/dev/null)"
no_violations
finish

begin 6 "intent-to-spec: PR closed, branch left on origin, intent revised"
close_pr "spec/$NAME"
OLD="$(oref "$S" "spec/$NAME")"
BEFORE="$(oref "$S" main)"
write_intent "$S/seed/intent/$NAME.md" "Revised: operators cannot see the widget's state or its history." &&
    seed_commit "$S" "intent: revise $NAME" || assert_fail "fixture setup"
eq "precondition: no open PR on spec/<name>" 0 "$(open_prs "spec/$NAME")"
ok "precondition: the left-behind branch has a commit main does not" \
    not hgit --git-dir "$S/origin.git" merge-base --is-ancestor "$OLD" refs/heads/main
C0="$(lines "$S/claude.log")"
P0="$(lines "$S/pr_create.log")"
checkout "$S" co2 0 || assert_fail "fixture setup"
run_sim "$WF_INTENT" "$S" co2 vars.SDLC_INTENT_TO_SPEC=true "github.event.before=$BEFORE"
eq "job succeeds" success "$(job .result)"
eq "1 new claude call" $((C0 + 1)) "$(lines "$S/claude.log")"
eq "1 new pr create" $((P0 + 1)) "$(lines "$S/pr_create.log")"
eq "the new PR's head is spec/<name>" "spec/$NAME" "$(lastpr .head)"
NEW="$(oref "$S" "spec/$NAME")"
ok "the replacement is not a fast-forward of the old tip (the push needed --force)" \
    not hgit --git-dir "$S/origin.git" merge-base --is-ancestor "$OLD" "$NEW"
eq "the replaced spec is the new reply" "$(lastcall .reply)" \
    "$(hgit --git-dir "$S/origin.git" show "$NEW:docs/specs/$NAME.md" 2>/dev/null)"
no_violations
finish

S="$ROOT/intent-open"
begin 7 "intent-to-spec: an open non-fork PR on spec/<name>"
setup_intent "$S" || assert_fail "fixture setup"
jq -n --arg h "spec/$NAME" \
    '[{number: 5, headRefName: $h, state: "OPEN", isCrossRepository: false}]' > "$S/prs.json"
run_sim "$WF_INTENT" "$S" co1 vars.SDLC_INTENT_TO_SPEC=true "github.event.before=$INTENT_BEFORE"
eq "job succeeds" success "$(job .result)"
ok "the skip is logged" contains "$(step_stdout)" "skip intent/$NAME.md: spec/$NAME already has an open PR"
eq "0 claude calls" 0 "$(lines "$S/claude.log")"
eq "0 pr creates" 0 "$(lines "$S/pr_create.log")"
ok "no spec/<name> pushed" not oref "$S" "spec/$NAME"
no_violations
finish

S="$ROOT/intent-error"
begin 8 "intent-to-spec: claude replies with an error"
setup_intent "$S" || assert_fail "fixture setup"
echo error > "$S/claude.mode"
run_sim "$WF_INTENT" "$S" co1 vars.SDLC_INTENT_TO_SPEC=true "github.event.before=$INTENT_BEFORE"
eq "job succeeds" success "$(job .result)"
eq "exactly 1 claude call, answered with an error" "1 error" \
    "$(lines "$S/claude.log") $(lastcall .mode)"
ok "no spec file written" test ! -e "$S/co1/docs/specs/$NAME.md"
ok "a warning names the intent" contains "$(step_stdout)" "::warning::no spec drafted for intent/$NAME.md"
eq "0 pr creates" 0 "$(lines "$S/pr_create.log")"
ok "no spec/<name> pushed" not oref "$S" "spec/$NAME"
no_violations
finish

# ── accounting: every scenario ran, none was skipped ─────────────────────
SKIPPED=""
for n in $SCENARIOS; do
    case "$DONE" in *" $n "*) ;; *) SKIPPED="$SKIPPED $n" ;; esac
done
echo
if [ "$RAN" -ge "$MIN_SCENARIOS" ] && [ -z "$SKIPPED" ]; then
    assert_pass "all $MIN_SCENARIOS scenarios reached their assertions (ran $RAN)"
else
    SC="accounting"
    assert_fail "all $MIN_SCENARIOS scenarios reached their assertions" \
        "ran $RAN; skipped:${SKIPPED:- none}"
fi

echo
echo "── Summary ────────────────────────────────────────────────────────"
echo "  scenarios: $RAN run, failed:${FAILED_SCENARIOS:- none}"
echo "  passed: $PASS"
echo "  failed: $FAIL"
if [ "$FAIL" -gt 0 ]; then
    echo "  failed tests:"
    for t in "${FAILED_TESTS[@]}"; do
        echo "    - $t"
    done
    exit 1
fi
exit 0
