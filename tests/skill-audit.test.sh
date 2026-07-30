#!/usr/bin/env bash
# tests/skill-audit.test.sh — Smoke tests for `bstack skills audit`.
#
# Fully hermetic: builds fake skill roots + registry + session logs in a
# tmpdir, points the auditor at them via BSTACK_AUDIT_ROOTS / BSTACK_DIR /
# BSTACK_AUDIT_LOG_GLOB. No real filesystem roots or network touched.
#
# Run from the bstack repo root:
#   bash tests/skill-audit.test.sh

set -uo pipefail

BSTACK_REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SKILLS_BIN="$BSTACK_REPO/bin/bstack-skills"
AUDIT_PY="$BSTACK_REPO/scripts/skill-audit.py"

PASS=0; FAIL=0; FAILED=()
ap() { PASS=$((PASS+1)); echo "  [pass] $1"; }
af() { FAIL=$((FAIL+1)); FAILED+=("$1"); echo "  [FAIL] $1"; [ -n "${2:-}" ] && echo "         $2"; }

echo "── skill-audit CLI smoke tests ────────────────────────────────────"

# Build a hermetic fixture: 2 roots, 1 duplicate (symlink), 1 over-budget desc,
# a registry with one registered-but-missing + ignoring one installed skill,
# and a session log mentioning only one skill.
FX="$(mktemp -d)"
ROOT_A="$FX/rootA"; ROOT_B="$FX/rootB"
mkdir -p "$ROOT_A" "$ROOT_B"

make_skill() {  # <root> <dir> <name> <description>
    mkdir -p "$1/$2"
    printf -- '---\nname: %s\ndescription: %s\n---\nbody\n' "$3" "$4" > "$1/$2/SKILL.md"
}
make_skill "$ROOT_A" alpha   alpha   "Short description for alpha."
make_skill "$ROOT_A" beta    beta    "Beta does beta things and triggers on beta."
make_skill "$ROOT_B" gamma   gamma   "Gamma skill in root B."
# Duplicate: 'alpha' also present in rootB at a DISTINCT path (not a symlink) → should flag as duplicate
make_skill "$ROOT_B" alpha   alpha   "Short description for alpha."
# Symlinked duplicate: rootB/delta -> rootA/beta  (realpath-dedupe should NOT double count)
ln -s "$ROOT_A/beta" "$ROOT_B/delta"

# Fake registry (BSTACK_DIR/references/companion-skills.yaml)
FAKE_BSTACK="$FX/bstack"; mkdir -p "$FAKE_BSTACK/references"
cat > "$FAKE_BSTACK/references/companion-skills.yaml" <<'YEOF'
schema_version: 1
skills:
  - name: alpha
    repo: broomva/skills
    category: meta
  - name: beta
    repo: broomva/skills
    category: meta
  - name: zeta
    repo: broomva/zeta
    category: meta
YEOF
# → 'gamma' is installed-but-unregistered; 'zeta' is registered-but-missing.

# Fake session log mentioning only 'beta' (via --skill beta)
LOGDIR="$FX/logs"; mkdir -p "$LOGDIR"
echo '{"text":"run --skill beta now"}' > "$LOGDIR/session.jsonl"

run_audit() {
    BSTACK_AUDIT_ROOTS="$ROOT_A:$ROOT_B" \
    BSTACK_DIR="$FAKE_BSTACK" \
    BSTACK_AUDIT_LOG_GLOB="$LOGDIR/*.jsonl" \
    python3 "$AUDIT_PY" "$@"
}

# T1: dispatch advertises audit
t="bstack-skills --help advertises audit"
if "$SKILLS_BIN" --help 2>&1 | grep -q 'audit \[--json\]'; then ap "$t"; else af "$t"; fi

# T2: JSON output is valid + counts unique names (alpha, beta, gamma = 3; delta symlink-deduped)
t="audit --json valid + realpath-dedupe (delta symlink not double-counted)"
out=$(run_audit --json --no-logs 2>&1); rc=$?
if [ "$rc" -eq 0 ] && echo "$out" | python3 -c "import json,sys; d=json.load(sys.stdin); assert d['unique_names']==3, d['unique_names']" 2>/dev/null; then
    ap "$t"
else
    af "$t" "rc=$rc unique_names mismatch: $(echo "$out" | python3 -c 'import json,sys;print(json.load(sys.stdin).get(\"unique_names\"))' 2>/dev/null)"
fi

# T3: duplicate detection (alpha in two distinct paths)
t="audit detects alpha duplicate (2 distinct realpaths)"
if run_audit --json --no-logs 2>/dev/null | python3 -c "import json,sys; d=json.load(sys.stdin); assert 'alpha' in d['duplicates'], d['duplicates']" 2>/dev/null; then
    ap "$t"
else
    af "$t"
fi

# T4: symlinked delta does NOT appear as a duplicate (realpath-dedupe worked)
t="symlinked delta is NOT flagged duplicate"
if run_audit --json --no-logs 2>/dev/null | python3 -c "import json,sys; d=json.load(sys.stdin); assert 'delta' not in d['duplicates'] and 'beta' not in d['duplicates']" 2>/dev/null; then
    ap "$t"
else
    af "$t"
fi

# T5: registry coherence — gamma unregistered, zeta missing
t="registry coherence (gamma unregistered, zeta missing)"
if run_audit --json --no-logs 2>/dev/null | python3 -c "import json,sys; d=json.load(sys.stdin); r=d['registry']; assert 'gamma' in r['installed_unregistered'] and 'zeta' in r['registered_missing'], r" 2>/dev/null; then
    ap "$t"
else
    af "$t"
fi

# T6: unused detection — only beta used (per log); alpha+gamma unused
t="unused detection (beta used via log, alpha+gamma unused)"
if run_audit --json --months 99 2>/dev/null | python3 -c "import json,sys; d=json.load(sys.stdin); u=d['unused']; assert 'beta' not in u and 'alpha' in u and 'gamma' in u, u" 2>/dev/null; then
    ap "$t"
else
    af "$t"
fi

# T7: token budget — tiny ceiling flags over-budget in human output
t="over-budget flag fires with tiny ceiling"
if run_audit --no-logs --budget-tokens 1 2>/dev/null | grep -q 'OVER BUDGET'; then ap "$t"; else af "$t"; fi

# T8: --no-logs skips usage scan (human output)
t="--no-logs skips usage scan"
if run_audit --no-logs 2>/dev/null | grep -q 'skipped — --no-logs'; then ap "$t"; else af "$t"; fi

# T9b: --chars-per-token 0 does not crash (clamped to >=1)
t="--chars-per-token 0 clamped (no ZeroDivisionError)"
out=$(run_audit --no-logs --chars-per-token 0 2>&1); rc=$?
if [ "$rc" -eq 0 ] && ! echo "$out" | grep -q 'ZeroDivisionError'; then ap "$t"; else af "$t" "rc=$rc"; fi

# T9: human report has all 5 sections
t="human report has 5 sections"
out=$(run_audit --no-logs 2>/dev/null)
if echo "$out" | grep -q '## Budget' && echo "$out" | grep -q '## Duplicates' \
   && echo "$out" | grep -q '## Registry coherence' && echo "$out" | grep -q '## Unused' \
   && echo "$out" | grep -q '## Roots'; then ap "$t"; else af "$t"; fi

# ── --require-tests gate (skillify step 3, BRO-1411) — separate hermetic root ──
FX2="$(mktemp -d)"; RT_ROOT="$FX2/skills"; mkdir -p "$RT_ROOT"
# md-only skill → exempt (no deterministic code)
make_skill "$RT_ROOT" docs docs "Markdown only, no code."
# code, no tests → must be flagged untested
make_skill "$RT_ROOT" coded coded "Script but no tests."
mkdir -p "$RT_ROOT/coded/scripts"; echo 'print(1)' > "$RT_ROOT/coded/scripts/run.py"
# code + tests → must NOT be flagged
make_skill "$RT_ROOT" tested tested "Script and test."
mkdir -p "$RT_ROOT/tested/scripts" "$RT_ROOT/tested/tests"
echo 'print(1)' > "$RT_ROOT/tested/scripts/run.py"
echo 'def test_x(): assert True' > "$RT_ROOT/tested/tests/test_run.py"

rt_audit() { BSTACK_AUDIT_ROOTS="$RT_ROOT" BSTACK_DIR="$FAKE_BSTACK" python3 "$AUDIT_PY" "$@"; }

# T10: untested detection — only 'coded' flagged; 'tested' + md-only 'docs' exempt
t="untested detection (coded flagged; tested + md-only exempt)"
if rt_audit --json --no-logs 2>/dev/null | python3 -c "import json,sys; d=json.load(sys.stdin); u={x['name'] for x in d['untested']}; assert u=={'coded'}, u" 2>/dev/null; then ap "$t"; else af "$t"; fi

# T11: --require-tests gate exits 1 when an untested skill exists
t="--require-tests exits 1 on untested skill"
rt_audit --no-logs --require-tests >/dev/null 2>&1; rc=$?
if [ "$rc" -eq 1 ]; then ap "$t"; else af "$t" "rc=$rc (expected 1)"; fi

# T12: without --require-tests, untested report is informational (exit 0)
t="untested report informational without --require-tests (exit 0)"
rt_audit --no-logs >/dev/null 2>&1; rc=$?
if [ "$rc" -eq 0 ]; then ap "$t"; else af "$t" "rc=$rc (expected 0)"; fi

# T13: human report includes the Untested section
t="human report includes '## Untested deterministic code'"
if rt_audit --no-logs 2>/dev/null | grep -q '## Untested deterministic code'; then ap "$t"; else af "$t"; fi

# ── Report 7: eval coverage / --require-evals (skillify step 5, BRO-2005) ─────
# Classes: none | present_but_vacuous | covered. Presence is NOT assertion, so
# every fixture below distinguishes "an evals/ artifact exists" from "it asserts
# trigger behaviour in both directions".
FX3="$(mktemp -d)"; EC_ROOT="$FX3/skills"; EC_CLEAN="$FX3/clean"
mkdir -p "$EC_ROOT" "$EC_CLEAN"

# none — no evals/ dir at all
make_skill "$EC_ROOT" noevals noevals "No evals directory."
# none — evals/ dir present but EMPTY (must NOT read as covered; anti-shopping)
make_skill "$EC_ROOT" emptydir emptydir "Empty evals dir."
mkdir -p "$EC_ROOT/emptydir/evals"
# present_but_vacuous — prompt set with cases but zero trigger keys
make_skill "$EC_ROOT" vacuous vacuous "Eval artifact asserting nothing."
mkdir -p "$EC_ROOT/vacuous/evals"
cat > "$EC_ROOT/vacuous/evals/prompts.json" <<'JEOF'
{"skill": "vacuous", "cases": [{"id": "c1", "prompt": "do the thing"}]}
JEOF
# present_but_vacuous — evals/ holds only prose (no JSON/YAML at all)
make_skill "$EC_ROOT" proseonly proseonly "Evals dir with only a README."
mkdir -p "$EC_ROOT/proseonly/evals"
echo '# Evals: TODO, will add should_trigger cases later' > "$EC_ROOT/proseonly/evals/README.md"
# present_but_vacuous — malformed JSON must NOT fail open into covered
make_skill "$EC_ROOT" malformed malformed "Unparseable prompt set."
mkdir -p "$EC_ROOT/malformed/evals"
printf '{"cases": [{"should_trigger": true}, {"should_trigger": false},\n' \
    > "$EC_ROOT/malformed/evals/prompts.json"
# present_but_vacuous — positives only (cannot catch over-firing)
make_skill "$EC_ROOT" posonly posonly "Positive cases only."
mkdir -p "$EC_ROOT/posonly/evals"
cat > "$EC_ROOT/posonly/evals/prompts.json" <<'JEOF'
{"cases": [{"prompt": "a", "should_trigger": true}, {"prompt": "b", "should_trigger": true}]}
JEOF
# present_but_vacuous — trigger key mentioned only in a PROSE blob, never a key
make_skill "$EC_ROOT" prosekey prosekey "Trigger key named in prose only."
mkdir -p "$EC_ROOT/prosekey/evals"
cat > "$EC_ROOT/prosekey/evals/prompts.json" <<'JEOF'
{"notes": "11 cases with should_trigger true and 6 with should_trigger false",
 "cases": [{"prompt": "a"}, {"prompt": "b"}]}
JEOF
# present_but_vacuous — trigger keys present but with EMPTY values (both
# polarities), so a placeholder cannot be mistaken for an assertion
make_skill "$EC_ROOT" emptyvals emptyvals "Resolver eval with empty prompt lists."
mkdir -p "$EC_ROOT/emptyvals/evals"
cat > "$EC_ROOT/emptyvals/evals/resolver.yaml" <<'YEOF'
lens: emptyvals
should_fire: []
should_not_fire: []
should_trigger: ""
should_not_trigger: ""
YEOF
# covered — string-encoded booleans (YAML/JSON quoting) still carry polarity
make_skill "$EC_ROOT" strvals strvals "Prompt set with quoted booleans."
mkdir -p "$EC_ROOT/strvals/evals"
cat > "$EC_ROOT/strvals/evals/prompts.json" <<'JEOF'
{"cases": [{"prompt": "a", "should_trigger": "true"}, {"prompt": "b", "should_trigger": "false"}]}
JEOF
# covered — prompt set, polarity in the VALUE (should_trigger true + false).
# 3 positive / 2 negative so the tally itself is assertable (T30).
make_skill "$EC_ROOT" coveredjson coveredjson "Two-sided prompt set."
mkdir -p "$EC_ROOT/coveredjson/evals"
cat > "$EC_ROOT/coveredjson/evals/prompts.json" <<'JEOF'
{"cases": [{"prompt": "a", "should_trigger": true},
           {"prompt": "b", "should_trigger": true},
           {"prompt": "c", "should_trigger": true},
           {"prompt": "d", "should_trigger": false},
           {"prompt": "e", "should_trigger": false}]}
JEOF
# covered — resolver eval YAML, polarity in the KEY (should_fire / should_not_fire)
make_skill "$EC_ROOT" coveredyaml coveredyaml "Two-sided resolver eval."
mkdir -p "$EC_ROOT/coveredyaml/evals"
cat > "$EC_ROOT/coveredyaml/evals/resolver.yaml" <<'YEOF'
lens: coveredyaml
should_fire: ["run the thing", "do it now"]
should_not_fire: ["summarize this PDF"]
YEOF

# covered — one case per file, nested under evals/cases/ (evals/ is scanned
# recursively; skillify's _eval_files does the same)
make_skill "$EC_ROOT" nested nested "One case per file."
mkdir -p "$EC_ROOT/nested/evals/cases"
echo '{"prompt": "a", "should_trigger": true}'  > "$EC_ROOT/nested/evals/cases/pos.json"
echo '{"prompt": "b", "should_trigger": false}' > "$EC_ROOT/nested/evals/cases/neg.json"

# Clean root: only honest absence + real coverage, plus an UNTESTED skill —
# proves the two gates are independent (evals gate must ignore untested code).
make_skill "$EC_CLEAN" cnone cnone "No evals — honest absence."
mkdir -p "$EC_CLEAN/cnone/scripts"; echo 'print(1)' > "$EC_CLEAN/cnone/scripts/run.py"
make_skill "$EC_CLEAN" ccovered ccovered "Two-sided prompt set."
mkdir -p "$EC_CLEAN/ccovered/evals"
cat > "$EC_CLEAN/ccovered/evals/prompts.json" <<'JEOF'
{"cases": [{"prompt": "a", "should_trigger": true}, {"prompt": "b", "should_trigger": false}]}
JEOF

ec_audit()    { BSTACK_AUDIT_ROOTS="$EC_ROOT"  BSTACK_DIR="$FAKE_BSTACK" python3 "$AUDIT_PY" "$@"; }
ec_clean()    { BSTACK_AUDIT_ROOTS="$EC_CLEAN" BSTACK_DIR="$FAKE_BSTACK" python3 "$AUDIT_PY" "$@"; }

# Print the eval-coverage class of one skill (or MISSING / DUPLICATE).
ec_state() {
    ec_audit --json --no-logs 2>/dev/null | python3 -c '
import json, sys
cov = json.load(sys.stdin)["eval_coverage"]
want = sys.argv[1]
hits = [s for s in ("covered", "present_but_vacuous", "none")
        if any(e["name"] == want for e in cov[s])]
print(hits[0] if len(hits) == 1 else ("MISSING" if not hits else "DUPLICATE"))
' "$1"
}

ec_expect() {  # <skill> <expected-class>
    local t="eval class: $1 → $2" got
    got="$(ec_state "$1")"
    if [ "$got" = "$2" ]; then ap "$t"; else af "$t" "got '$got'"; fi
}

# T14: no evals/ dir at all → none
ec_expect noevals none
# T15: EMPTY evals/ dir → none, never silently covered (anti-shopping)
ec_expect emptydir none
# T16: cases present but zero trigger keys → present_but_vacuous
ec_expect vacuous present_but_vacuous
# T17: evals/ holding only prose → present_but_vacuous (presence is not assertion)
ec_expect proseonly present_but_vacuous
# T18: malformed JSON does NOT fail open into covered
ec_expect malformed present_but_vacuous
# T19: positives only → present_but_vacuous (cannot catch over-firing)
ec_expect posonly present_but_vacuous
# T20: trigger key in a prose blob only → not covered (must be a real mapping key)
ec_expect prosekey present_but_vacuous
# T21: trigger keys present but with EMPTY values → asserts nothing
ec_expect emptyvals present_but_vacuous
# T22: two-sided prompt set (polarity in the value) → covered
ec_expect coveredjson covered
# T23: two-sided resolver eval YAML (polarity in the key) → covered
ec_expect coveredyaml covered
# T23b: string-encoded booleans still carry polarity → covered
ec_expect strvals covered
# T23c: cases split one-per-file under evals/cases/ are still seen → covered
ec_expect nested covered

# T24: --require-evals exits 1 when any skill is present_but_vacuous
t="--require-evals exits 1 on a vacuous evals/ artifact"
ec_audit --no-logs --require-evals >/dev/null 2>&1; rc=$?
if [ "$rc" -eq 1 ]; then ap "$t"; else af "$t" "rc=$rc (expected 1)"; fi

# T25: without --require-evals the report is informational (exit 0)
t="eval report informational without --require-evals (exit 0)"
ec_audit --no-logs >/dev/null 2>&1; rc=$?
if [ "$rc" -eq 0 ]; then ap "$t"; else af "$t" "rc=$rc (expected 0)"; fi

# T26: the gate does NOT fire on 'none' — absence is honest, and an untested
#      skill must not turn the EVAL gate red (gates are independent)
t="--require-evals exits 0 on none+covered only (absence not gated)"
ec_clean --no-logs --require-evals >/dev/null 2>&1; rc=$?
if [ "$rc" -eq 0 ]; then ap "$t"; else af "$t" "rc=$rc (expected 0)"; fi

# T27: converse independence — same root still fails --require-tests
t="--require-tests still exits 1 on that root (gate independence)"
ec_clean --no-logs --require-tests >/dev/null 2>&1; rc=$?
if [ "$rc" -eq 1 ]; then ap "$t"; else af "$t" "rc=$rc (expected 1)"; fi

# T28: human report includes the Eval coverage section with all three counts
t="human report includes '## Eval coverage' with 3 class counts"
if ec_audit --no-logs 2>/dev/null | grep -qE '^## Eval coverage +\[[0-9]+ covered / [0-9]+ vacuous / [0-9]+ none\]'; then
    ap "$t"
else
    af "$t"
fi

# T29: the gate's failure line names the vacuous skills (a silent gate is unusable)
t="--require-evals human output names the FAILED gate + a vacuous skill"
out=$(ec_audit --no-logs --require-evals 2>/dev/null)
if echo "$out" | grep -q -- '--require-evals gate FAILED' && echo "$out" | grep -q 'posonly:'; then
    ap "$t"
else
    af "$t"
fi

# T30: the polarity tally is reported per case, not collapsed to a flag
t="eval tally counts every case (coveredjson = 3 positive / 2 negative)"
if ec_audit --json --no-logs 2>/dev/null | python3 -c '
import json, sys
e = next(x for x in json.load(sys.stdin)["eval_coverage"]["covered"] if x["name"] == "coveredjson")
assert (e["positive"], e["negative"]) == (3, 2), e
' 2>/dev/null; then ap "$t"; else af "$t"; fi

rm -rf "$FX3"
rm -rf "$FX2"
rm -rf "$FX"
echo ""
echo "── results: $PASS passed, $FAIL failed ────────────────────────────"
if [ "$FAIL" -gt 0 ]; then printf '  failed: %s\n' "${FAILED[@]}"; exit 1; fi
exit 0
