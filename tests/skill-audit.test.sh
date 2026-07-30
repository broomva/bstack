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

PASS=0; FAIL=0; SKIP=0; FAILED=()
ap() { PASS=$((PASS+1)); echo "  [pass] $1"; }
af() { FAIL=$((FAIL+1)); FAILED+=("$1"); echo "  [FAIL] $1"; [ -n "${2:-}" ] && echo "         $2"; }
# A skip is NOT a pass: an environment that cannot run a check must not be able to
# report a green it never earned.
as_() { SKIP=$((SKIP+1)); echo "  [skip] $1"; }

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
# Classes: none | no_trigger_eval | present_but_vacuous | covered. Presence is NOT
# assertion, so every fixture below distinguishes "an evals/ artifact exists" from
# "it asserts trigger behaviour in both directions" — and, since review round 1,
# from "it is a real eval suite that never made a trigger claim" (no_trigger_eval,
# never gated: a gate whose cheapest green is `rm -rf evals/` is disqualified).
FX3="$(mktemp -d)"; EC_ROOT="$FX3/skills"; EC_CLEAN="$FX3/clean"
EC_IO="$FX3/io"; EC_DEEP="$FX3/deep"
mkdir -p "$EC_ROOT" "$EC_CLEAN" "$EC_IO" "$EC_DEEP"

# none — no evals/ dir at all
make_skill "$EC_ROOT" noevals noevals "No evals directory."
# none — evals/ holding only .gitkeep. THE production-reachable honest absence:
# git cannot track an empty directory, so no tracked skill can be in the mkdir-only
# state below — grading .gitkeep as a vacuous claim put the only reachable form of
# "empty evals/" on the failing side of the gate (MAJOR-3).
make_skill "$EC_ROOT" gitkeep gitkeep "Evals dir tracked via .gitkeep."
mkdir -p "$EC_ROOT/gitkeep/evals"; : > "$EC_ROOT/gitkeep/evals/.gitkeep"
# none — .DS_Store is macOS litter, not an eval artifact
make_skill "$EC_ROOT" dsstore dsstore "Evals dir with only a .DS_Store."
mkdir -p "$EC_ROOT/dsstore/evals"; printf 'junk' > "$EC_ROOT/dsstore/evals/.DS_Store"
# none — evals/ dir present but truly EMPTY (untracked working state; kept as its
# own case so both sides of the git-cannot-store-an-empty-dir fact are pinned)
make_skill "$EC_ROOT" emptydir emptydir "Empty evals dir."
mkdir -p "$EC_ROOT/emptydir/evals"
# no_trigger_eval — prompt set with cases but zero trigger keys: nothing claimed,
# so nothing unmet. Reported, never gated.
make_skill "$EC_ROOT" notrigger notrigger "Eval artifact with no trigger keys."
mkdir -p "$EC_ROOT/notrigger/evals"
cat > "$EC_ROOT/notrigger/evals/prompts.json" <<'JEOF'
{"skill": "notrigger", "cases": [{"id": "c1", "prompt": "do the thing"}]}
JEOF
# no_trigger_eval — the BLOCKER-1 shape: a substantive behavioural-eval suite in
# the Anthropic schema (prompt + expectations), the shape clerk-setup ships.
make_skill "$EC_ROOT" behavioural behavioural "Behavioural eval suite, not a trigger eval."
mkdir -p "$EC_ROOT/behavioural/evals"
cat > "$EC_ROOT/behavioural/evals/evals.json" <<'JEOF'
{"skill_name": "behavioural",
 "evals": [{"id": 1, "prompt": "set up auth in my next app",
            "expected_output": "installs the framework package",
            "expectations": ["Installs @clerk/nextjs", "Does NOT install @clerk/clerk-react"]}]}
JEOF
# none — evals/ holds only a README: documentation ABOUT the (absent) evals
make_skill "$EC_ROOT" proseonly proseonly "Evals dir with only a README."
mkdir -p "$EC_ROOT/proseonly/evals"
echo '# Evals: TODO, will add should_trigger cases later' > "$EC_ROOT/proseonly/evals/README.md"
# no_trigger_eval — a runner script is an artifact of another shape, not a claim
make_skill "$EC_ROOT" runneronly runneronly "Evals dir with only a runner script."
mkdir -p "$EC_ROOT/runneronly/evals"
echo '#!/usr/bin/env bash' > "$EC_ROOT/runneronly/evals/run-evals.sh"
# present_but_vacuous — malformed JSON must NOT fail open into covered
make_skill "$EC_ROOT" malformed malformed "Unparseable prompt set."
mkdir -p "$EC_ROOT/malformed/evals"
printf '{"cases": [{"should_trigger": true}, {"should_trigger": false},\n' \
    > "$EC_ROOT/malformed/evals/prompts.json"
# no_trigger_eval — a JSON SCHEMA describing the eval format is not the eval
# (BLOCKER-2: dict values under trigger keys used to score +1/-1 with zero cases)
# The two trigger keys sit in SEPARATE $defs nodes on purpose: one per mapping, so
# the dict guard is what keeps this out of `covered` (with dict back in the
# collection branch this scores +1/-1 from zero cases). Colocated in one node the
# distinct-case rule would mask the defect and the check would prove nothing.
make_skill "$EC_ROOT" schemafile schemafile "JSON Schema for the eval format."
mkdir -p "$EC_ROOT/schemafile/evals"
cat > "$EC_ROOT/schemafile/evals/prompts.schema.json" <<'JEOF'
{"$schema": "https://json-schema.org/draft/2020-12/schema", "type": "object",
 "properties": {"cases": {"type": "array", "items": {"$ref": "#/$defs/case"}}},
 "$defs": {"case": {"oneOf": [{"$ref": "#/$defs/pos"}, {"$ref": "#/$defs/neg"}]},
           "pos": {"type": "object",
                   "properties": {"should_trigger": {"type": "boolean"}}},
           "neg": {"type": "object",
                   "properties": {"should_not_trigger": {"type": "boolean"}}}}}
JEOF
# no_trigger_eval — an unfilled TEMPLATE asserts nothing; "<FILL ME>" is not a boolean
make_skill "$EC_ROOT" template template "Unfilled eval template."
mkdir -p "$EC_ROOT/template/evals"
cat > "$EC_ROOT/template/evals/TEMPLATE.json" <<'JEOF'
{"cases": [{"prompt": "<FILL ME>", "should_trigger": "<FILL ME>"},
           {"prompt": "<FILL ME>", "should_not_trigger": "<FILL ME>"}]}
JEOF
# no_trigger_eval — evals/results/ is the OUTPUT of a run, not a case set; a stale
# results dump must not certify a case set that has since been deleted
make_skill "$EC_ROOT" resultsonly resultsonly "Only a results dump."
mkdir -p "$EC_ROOT/resultsonly/evals/results"
cat > "$EC_ROOT/resultsonly/evals/results/run.json" <<'JEOF'
{"run": "2026-07-29", "results": [{"id": "c1", "should_trigger": true, "passed": true},
                                  {"id": "c2", "should_trigger": false, "passed": true}]}
JEOF
# present_but_vacuous — ONE self-contradictory case is not two-sidedness: two-sidedness
# is a property of distinct CASES, not of keys found anywhere in the tree
make_skill "$EC_ROOT" selfcontra selfcontra "One case asserting both polarities."
mkdir -p "$EC_ROOT/selfcontra/evals"
cat > "$EC_ROOT/selfcontra/evals/prompts.json" <<'JEOF'
{"cases": [{"prompt": "a", "should_trigger": true, "should_not_trigger": true}]}
JEOF
# covered — the same two assertions split across two DISTINCT cases (control for
# the fixture above: the distinct-case rule must not reject a legitimate pair)
make_skill "$EC_ROOT" twocases twocases "Two distinct one-sided cases."
mkdir -p "$EC_ROOT/twocases/evals"
cat > "$EC_ROOT/twocases/evals/prompts.json" <<'JEOF'
{"cases": [{"prompt": "a", "should_trigger": true}, {"prompt": "b", "should_not_trigger": true}]}
JEOF
# covered — JSONL, the format OpenAI evals / promptfoo / Braintrust emit (MAJOR-4)
make_skill "$EC_ROOT" jsonlcovered jsonlcovered "Line-delimited prompt set."
mkdir -p "$EC_ROOT/jsonlcovered/evals"
cat > "$EC_ROOT/jsonlcovered/evals/prompts.jsonl" <<'JEOF'
{"prompt": "a", "should_trigger": true}

{"prompt": "b", "should_trigger": false}
JEOF
# present_but_vacuous — ONE malformed line makes the whole JSONL ungradable; it must
# not fail open into covered on the strength of the lines that did parse. The two
# good lines are deliberately TWO-SIDED: if the parser skipped the broken line
# instead of failing closed, this fixture would read `covered`, so the strictness is
# load-bearing rather than decorative.
make_skill "$EC_ROOT" jsonlbad jsonlbad "Line-delimited set with a broken line."
mkdir -p "$EC_ROOT/jsonlbad/evals"
cat > "$EC_ROOT/jsonlbad/evals/prompts.jsonl" <<'JEOF'
{"prompt": "a", "should_trigger": true}
{"prompt": "b", "should_trigger": false}
{"prompt": "c", "should_trigger": tru
JEOF
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
# present_but_vacuous — trigger keys present but with EMPTY values, in BOTH
# polarities and in both value shapes, so a placeholder cannot be mistaken for an
# assertion. Both halves are load-bearing: the empty lists are what a dropped
# empty-collection guard would turn into a two-sided pass, and the two empty-string
# cases (opposite keys, DISTINCT mappings, so the contradiction rule cannot mask
# them) are what a lenient string branch would.
make_skill "$EC_ROOT" emptyvals emptyvals "Resolver eval with empty prompt lists."
mkdir -p "$EC_ROOT/emptyvals/evals"
cat > "$EC_ROOT/emptyvals/evals/resolver.yaml" <<'YEOF'
lens: emptyvals
should_fire: []
should_not_fire: []
cases:
  - prompt: a
    should_trigger: ""
  - prompt: b
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

# covered — TOML, the other ext skillify_check.py's _EVAL_DATA_EXTS declares (MAJOR-4).
# Skipped below when the interpreter predates tomllib; the auditor must DEGRADE, not crash.
make_skill "$EC_ROOT" tomlcovered tomlcovered "TOML prompt set."
mkdir -p "$EC_ROOT/tomlcovered/evals"
cat > "$EC_ROOT/tomlcovered/evals/prompts.toml" <<'TEOF'
[[cases]]
prompt = "a"
should_trigger = true

[[cases]]
prompt = "b"
should_trigger = false
TEOF

# Clean root: honest absence + real coverage + a real eval suite of ANOTHER shape,
# plus an UNTESTED skill — proves (a) the two gates are independent and (b) the
# BLOCKER-1 incentive is right way up: shipping a 2-expectation behavioural suite
# must not be worse for the gate than shipping no evals at all.
make_skill "$EC_CLEAN" cnone cnone "No evals — honest absence."
mkdir -p "$EC_CLEAN/cnone/scripts"; echo 'print(1)' > "$EC_CLEAN/cnone/scripts/run.py"
make_skill "$EC_CLEAN" ccovered ccovered "Two-sided prompt set."
mkdir -p "$EC_CLEAN/ccovered/evals"
cat > "$EC_CLEAN/ccovered/evals/prompts.json" <<'JEOF'
{"cases": [{"prompt": "a", "should_trigger": true}, {"prompt": "b", "should_trigger": false}]}
JEOF
make_skill "$EC_CLEAN" cother cother "Scenario eval suite, not a trigger eval."
mkdir -p "$EC_CLEAN/cother/evals"
cat > "$EC_CLEAN/cother/evals/scenarios.yaml" <<'YEOF'
skill: cother
scenarios:
  - id: s1
    expect: { decision: defer, performs_act: false }
    deterministic_test: tests/test_loop_state.py::test_defer
YEOF

# IO-error root: the artifact exists and is a data file, but cannot be READ.
# Fails CLOSED — an unreadable eval asserts as much as a missing one while still
# being a standing claim (mutation G14: making this path return a two-sided
# payload used to leave the whole suite green).
make_skill "$EC_IO" unreadable unreadable "Eval artifact that cannot be read."
mkdir -p "$EC_IO/unreadable/evals"
cat > "$EC_IO/unreadable/evals/prompts.json" <<'JEOF'
{"cases": [{"prompt": "a", "should_trigger": true}, {"prompt": "b", "should_trigger": false}]}
JEOF
chmod 000 "$EC_IO/unreadable/evals/prompts.json"

# Deep root: nesting past the depth cap. Assertions below it are NOT graded — the
# auditor must say so out loud rather than silently under-counting.
make_skill "$EC_DEEP" deep deep "Absurdly nested prompt set."
mkdir -p "$EC_DEEP/deep/evals"
python3 -c '
import json
node = {"cases": [{"prompt": "a", "should_trigger": True},
                  {"prompt": "b", "should_trigger": False}]}
for _ in range(45):
    node = {"nest": node}
print(json.dumps(node))' > "$EC_DEEP/deep/evals/prompts.json"

ec_audit()    { BSTACK_AUDIT_ROOTS="$EC_ROOT"  BSTACK_DIR="$FAKE_BSTACK" python3 "$AUDIT_PY" "$@"; }
ec_clean()    { BSTACK_AUDIT_ROOTS="$EC_CLEAN" BSTACK_DIR="$FAKE_BSTACK" python3 "$AUDIT_PY" "$@"; }
ec_io()       { BSTACK_AUDIT_ROOTS="$EC_IO"    BSTACK_DIR="$FAKE_BSTACK" python3 "$AUDIT_PY" "$@"; }
ec_deep()     { BSTACK_AUDIT_ROOTS="$EC_DEEP"  BSTACK_DIR="$FAKE_BSTACK" python3 "$AUDIT_PY" "$@"; }

# Print the eval-coverage class of one skill (or MISSING / DUPLICATE). The class
# list is read from the auditor's own `counts` block, so a new class cannot be
# added without every ec_expect below seeing it.
ec_state() {
    ec_audit --json --no-logs 2>/dev/null | python3 -c '
import json, sys
cov = json.load(sys.stdin)["eval_coverage"]
want = sys.argv[1]
hits = [s for s in cov["counts"] if any(e["name"] == want for e in cov[s])]
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
# T15: evals/.gitkeep → none. The production-reachable honest absence (MAJOR-3):
#      git cannot store an empty dir, so this is the ONLY tracked form of "no evals yet".
ec_expect gitkeep none
# T15b: .DS_Store is litter, not an artifact
ec_expect dsstore none
# T15c: truly EMPTY evals/ dir → none, never silently covered (anti-shopping)
ec_expect emptydir none
# T16: cases present but zero trigger keys → no_trigger_eval (no claim, so nothing unmet)
ec_expect notrigger no_trigger_eval
# T16b: BLOCKER-1 — a substantive behavioural-eval suite is NOT a vacuous claim
ec_expect behavioural no_trigger_eval
# T17: evals/ holding only a README → none (docs about the absent evals)
ec_expect proseonly none
# T17b: evals/ holding only a runner script → no_trigger_eval, never gated
ec_expect runneronly no_trigger_eval
# T18: malformed JSON does NOT fail open into covered
ec_expect malformed present_but_vacuous
# T19: positives only → present_but_vacuous (cannot catch over-firing)
ec_expect posonly present_but_vacuous
# T20: trigger key in a prose blob only → not covered (must be a real mapping key)
ec_expect prosekey no_trigger_eval
# T21: trigger keys present but with EMPTY values → asserts nothing, and it IS a
#      reach for the trigger keys → the gated state
ec_expect emptyvals present_but_vacuous
# T22: two-sided prompt set (polarity in the value) → covered
ec_expect coveredjson covered
# T23: two-sided resolver eval YAML (polarity in the key) → covered.
#      Also the positive control for BLOCKER-2: restricting the collection branch
#      to list/tuple/set must NOT break the shape it exists for.
ec_expect coveredyaml covered
# T23b: string-encoded booleans still carry polarity → covered
ec_expect strvals covered
# T23c: cases split one-per-file under evals/cases/ are still seen → covered
ec_expect nested covered
# T31: BLOCKER-2 — a JSON SCHEMA describing the eval format is NOT the eval. dict
#      values under trigger keys must assert nothing (mutation G6: with `dict` back
#      in the collection branch this scores covered, +1/-1, from zero cases).
#      It lands in the GATED class, not the exempt one: unlike a behavioural suite,
#      a schema-only evals/ dir does reach for the trigger keys and delivers no case.
ec_expect schemafile present_but_vacuous
# T31b: and the polarity tally is empty — "not covered" must not rest on the class
#       name alone while the counter still says +1/-1
t="schema file contributes ZERO polarity (0 positive / 0 negative)"
if ec_audit --json --no-logs 2>/dev/null | python3 -c '
import json, sys
cov = json.load(sys.stdin)["eval_coverage"]
e = next(x for x in cov["present_but_vacuous"] if x["name"] == "schemafile")
assert (e["positive"], e["negative"]) == (0, 0), e
assert e["trigger_keys"] >= 2, e
' 2>/dev/null; then ap "$t"; else af "$t"; fi
# T32: an unfilled "<FILL ME>" template asserts nothing — only real booleans count
ec_expect template present_but_vacuous
# T33: evals/results/ is run OUTPUT, not a case set
ec_expect resultsonly no_trigger_eval
# T34: ONE self-contradictory case is not two-sidedness (distinct cases required)
ec_expect selfcontra present_but_vacuous
# T34b: control — the same two assertions in two DISTINCT cases → covered
ec_expect twocases covered
# T35: JSONL is parsed line-delimited → covered (MAJOR-4)
ec_expect jsonlcovered covered
# T35b: one malformed JSONL line makes the file ungradable, never covered
ec_expect jsonlbad present_but_vacuous
# T36: TOML is parsed → covered (MAJOR-4); skipped where tomllib is unavailable
if python3 -c 'import tomllib' 2>/dev/null; then
    ec_expect tomlcovered covered
else
    as_ "eval class: tomlcovered → covered (no tomllib on this interpreter)"
    t="tomlcovered degrades to a non-gated class without tomllib"
    got="$(ec_state tomlcovered)"
    if [ "$got" = "no_trigger_eval" ]; then ap "$t"; else af "$t" "got '$got'"; fi
fi

# T24: --require-evals exits 1 when any skill is present_but_vacuous
t="--require-evals exits 1 on a vacuous evals/ artifact"
ec_audit --no-logs --require-evals >/dev/null 2>&1; rc=$?
if [ "$rc" -eq 1 ]; then ap "$t"; else af "$t" "rc=$rc (expected 1)"; fi

# T25: without --require-evals the report is informational (exit 0)
t="eval report informational without --require-evals (exit 0)"
ec_audit --no-logs >/dev/null 2>&1; rc=$?
if [ "$rc" -eq 0 ]; then ap "$t"; else af "$t" "rc=$rc (expected 0)"; fi

# T26: the gate does NOT fire on 'none' or 'no_trigger_eval' — absence is honest,
#      a suite of another shape made no claim, and an untested skill must not turn
#      the EVAL gate red (gates are independent)
t="--require-evals exits 0 on none+covered+no_trigger_eval (absence + other shapes not gated)"
ec_clean --no-logs --require-evals >/dev/null 2>&1; rc=$?
if [ "$rc" -eq 0 ]; then ap "$t"; else af "$t" "rc=$rc (expected 0)"; fi

# T26b: BLOCKER-1's disqualifying property, stated as a test — deleting a real
#       eval suite must never be the cheaper way to satisfy the gate. `cother`
#       ships a scenario suite; removing it leaves `none`, which is also green.
#       Both green is the requirement; the FAILURE this pins is suite=red/absent=green.
t="deleting a real (non-trigger) eval suite is not a way to pass the gate"
ec_clean --json --no-logs --require-evals >/dev/null 2>&1; with_suite=$?
mv "$EC_CLEAN/cother/evals" "$EC_CLEAN/cother/evals.off"
ec_clean --no-logs --require-evals >/dev/null 2>&1; without_suite=$?
mv "$EC_CLEAN/cother/evals.off" "$EC_CLEAN/cother/evals"
if [ "$with_suite" -eq 0 ] && [ "$without_suite" -eq 0 ]; then
    ap "$t"
else
    af "$t" "with_suite=$with_suite without_suite=$without_suite (both must be 0)"
fi

# T37: an UNREADABLE artifact fails CLOSED — class is the gated one, and the gate
#      goes red (mutation G14: return a two-sided payload on OSError). Skipped for
#      root, which reads a chmod-000 file regardless.
t="unreadable eval artifact fails CLOSED (class + gate)"
if [ -r "$EC_IO/unreadable/evals/prompts.json" ]; then
    as_ "$t (file still readable — running as root?)"
else
    io_state=$(ec_io --json --no-logs 2>/dev/null | python3 -c '
import json, sys
cov = json.load(sys.stdin)["eval_coverage"]
print(next(s for s in cov["counts"] if any(e["name"] == "unreadable" for e in cov[s])))
' 2>/dev/null)
    ec_io --no-logs --require-evals >/dev/null 2>&1; rc=$?
    if [ "$io_state" = "present_but_vacuous" ] && [ "$rc" -eq 1 ]; then
        ap "$t"
    else
        af "$t" "state='$io_state' rc=$rc (want present_but_vacuous / 1)"
    fi
fi

# T37b: and the reason names the IO error rather than calling it "empty or unparseable"
t="unreadable artifact's reason names the IO error"
if [ -r "$EC_IO/unreadable/evals/prompts.json" ]; then
    as_ "$t (file still readable — running as root?)"
elif ec_io --json --no-logs 2>/dev/null | python3 -c '
import json, sys
cov = json.load(sys.stdin)["eval_coverage"]
e = next(x for x in cov["present_but_vacuous"] if x["name"] == "unreadable")
assert "unreadable (IO error)" in e["reason"], e["reason"]
' 2>/dev/null; then ap "$t"; else af "$t"; fi

# T38: nesting past the depth cap WARNS instead of silently under-counting
t="depth cap emits a warning instead of dropping assertions silently"
if ec_deep --no-logs 2>&1 >/dev/null | grep -q 'nesting exceeds 40 levels'; then
    ap "$t"
else
    af "$t"
fi

# T39: the trigger-key set is pinned and SYMMETRIC. An asymmetric set grades one
#      spelling and ignores the other; drift upstream must break a test, not a gate.
t="TRIGGER_ASSERTION_KEYS pinned + camelCase/negative symmetry"
if python3 - "$AUDIT_PY" <<'PYEOF' 2>/dev/null
import importlib.util, sys
spec = importlib.util.spec_from_file_location("skill_audit_under_test", sys.argv[1])
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

expected = {
    "should_trigger", "shouldTrigger",
    "should_not_trigger", "shouldNotTrigger",
    "should_fire", "shouldFire",
    "should_not_fire", "shouldNotFire",
    "negative_case", "negativeCase",
}
assert set(m.TRIGGER_ASSERTION_KEYS) == expected, sorted(set(m.TRIGGER_ASSERTION_KEYS) ^ expected)

def camel(s):
    head, *rest = s.split("_")
    return head + "".join(w[:1].upper() + w[1:] for w in rest)

snake = {k for k in expected if "_" in k}
assert {camel(k) for k in snake} <= expected, "camelCase twin missing"
assert set(m._NEGATIVE_TRIGGER_KEYS) <= expected, "negative keys not a subset"
# every negative key has its positive counterpart in the set, and vice versa
for k in m._NEGATIVE_TRIGGER_KEYS:
    assert ("not" in k.lower()) or ("negative" in k.lower()), k
assert set(m.EVAL_DATA_EXTS) == {".json", ".jsonl", ".yaml", ".yml", ".toml"}, m.EVAL_DATA_EXTS
assert m.EVAL_STATES == ("covered", "no_trigger_eval", "present_but_vacuous", "none"), m.EVAL_STATES
PYEOF
then ap "$t"; else af "$t"; fi

# T27: converse independence — same root still fails --require-tests
t="--require-tests still exits 1 on that root (gate independence)"
ec_clean --no-logs --require-tests >/dev/null 2>&1; rc=$?
if [ "$rc" -eq 1 ]; then ap "$t"; else af "$t" "rc=$rc (expected 1)"; fi

# T28: human report includes the Eval coverage section with all FOUR class counts
t="human report includes '## Eval coverage' with 4 class counts"
if ec_audit --no-logs 2>/dev/null | grep -qE '^## Eval coverage +\[[0-9]+ covered / [0-9]+ vacuous / [0-9]+ no-trigger-eval / [0-9]+ none\]'; then
    ap "$t"
else
    af "$t"
fi

# T28b: the no_trigger_eval class is REPORTED, not hidden — an ungated class that
#       nobody can see is indistinguishable from not classifying at all
t="human report lists the no_trigger_eval skills with reasons"
out=$(ec_audit --no-logs 2>/dev/null)
if echo "$out" | grep -q 'no_trigger_eval ([0-9]*)' && echo "$out" | grep -q 'behavioural: .*another shape'; then
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

# T29b: the operator message must not accuse a skill of a claim it never made, and
#       must say that deleting evals/ is not the fix (BLOCKER-1's inverted incentive
#       survived review precisely because the message asserted the opposite)
t="gate message drops the false accusation and names the real fix"
out=$(ec_audit --no-logs --require-evals 2>/dev/null)
if ! echo "$out" | grep -q 'claim eval coverage they do not have' \
   && echo "$out" | grep -q 'does not establish' \
   && echo "$out" | grep -qi 'deleting evals/ is NOT a fix'; then
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

# T30c: a key-polarity key is ONE assertion, not len(prompts). Pinned because the
#       weighting is what makes the empty-collection guard testable at all: under
#       len() weighting, `should_fire: []` and a dropped guard tally identically
#       (both 0), so the guard would be un-testable and the emptyvals check vacuous.
t="key-polarity tally is per KEY, not per prompt (coveredyaml = 1 positive / 1 negative)"
if ec_audit --json --no-logs 2>/dev/null | python3 -c '
import json, sys
e = next(x for x in json.load(sys.stdin)["eval_coverage"]["covered"] if x["name"] == "coveredyaml")
assert (e["positive"], e["negative"]) == (1, 1), e
' 2>/dev/null; then ap "$t"; else af "$t"; fi

# T30b: --json carries the fourth class in `counts` and as its own entry list, so a
#       consumer can tell "no claim" from "unmet claim" without parsing prose
t="--json exposes no_trigger_eval in counts + as an entry list"
if ec_audit --json --no-logs 2>/dev/null | python3 -c '
import json, sys
cov = json.load(sys.stdin)["eval_coverage"]
assert set(cov["counts"]) == {"covered", "no_trigger_eval", "present_but_vacuous", "none"}, cov["counts"]
names = {e["name"] for e in cov["no_trigger_eval"]}
assert {"behavioural", "notrigger", "resultsonly", "runneronly"} <= names, names
e = next(x for x in cov["no_trigger_eval"] if x["name"] == "behavioural")
assert e["trigger_keys"] == 0 and e["other_assertions"] >= 3, e
assert cov["counts"]["no_trigger_eval"] == len(cov["no_trigger_eval"])
' 2>/dev/null; then ap "$t"; else af "$t"; fi

chmod 644 "$EC_IO/unreadable/evals/prompts.json" 2>/dev/null || true
rm -rf "$FX3"
rm -rf "$FX2"
rm -rf "$FX"
echo ""
echo "── results: $PASS passed, $FAIL failed, $SKIP skipped ──────────────"
if [ "$FAIL" -gt 0 ]; then printf '  failed: %s\n' "${FAILED[@]}"; exit 1; fi
exit 0
