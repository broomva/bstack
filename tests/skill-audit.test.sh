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

# ── round-3 fixtures ──────────────────────────────────────────────────────────
# no_trigger_eval — MAJOR-1: a fully graded other-shaped suite standing next to a
# STRAY ungradable sibling. Ranking "ungradable" above "other/parsed" let one
# 0-byte file gate a 34-assertion behavioural suite as a vacuous TRIGGER claim
# whose own trigger_keys was 0 — the --json entry contradicted itself. The stray
# files must still be NAMED (appended to the reason), just not decide the class.
make_skill "$EC_ROOT" straysuite straysuite "Graded suite plus a stray ungradable sibling."
mkdir -p "$EC_ROOT/straysuite/evals"
cat > "$EC_ROOT/straysuite/evals/evals.json" <<'JEOF'
{"skill_name": "straysuite",
 "evals": [{"id": 1, "prompt": "set up auth", "expected_output": "installs the package",
            "expectations": ["Installs the framework package", "Does NOT install the SPA package"]}]}
JEOF
echo '# TODO: add trigger evals' > "$EC_ROOT/straysuite/evals/notes.yaml"
: > "$EC_ROOT/straysuite/evals/todo.json"
# covered — MAJOR-1, the other direction: the exception the docstring already
# documented ("covered wins over an ungradable sibling") but nothing tested.
make_skill "$EC_ROOT" straycovered straycovered "Two-sided set plus a stray ungradable sibling."
mkdir -p "$EC_ROOT/straycovered/evals"
cat > "$EC_ROOT/straycovered/evals/prompts.json" <<'JEOF'
{"cases": [{"prompt": "a", "should_trigger": true}, {"prompt": "b", "should_trigger": false}]}
JEOF
: > "$EC_ROOT/straycovered/evals/todo.json"
# present_but_vacuous — the ungradable state stays REACHABLE when it is the only
# thing in evals/. Moving the branch below other/parsed must not delete the state.
make_skill "$EC_ROOT" lonetodo lonetodo "Only a 0-byte placeholder artifact."
mkdir -p "$EC_ROOT/lonetodo/evals"; : > "$EC_ROOT/lonetodo/evals/todo.json"

# covered — MINOR-3: three real resolver-eval shapes the round-1 `else: return None`
# fallthrough downgraded to vacuous. Under a KEY-polarity key the value position
# holds prompts, so a name->prompt map and a bare prompt string are both coverage.
make_skill "$EC_ROOT" keydict keydict "Resolver eval as a name->prompt map."
mkdir -p "$EC_ROOT/keydict/evals"
cat > "$EC_ROOT/keydict/evals/resolver.yaml" <<'YEOF'
should_fire: {c1: "run the thing"}
should_not_fire: {c3: "summarize this PDF"}
YEOF
make_skill "$EC_ROOT" keystr keystr "Resolver eval with one prompt per side."
mkdir -p "$EC_ROOT/keystr/evals"
cat > "$EC_ROOT/keystr/evals/resolver.yaml" <<'YEOF'
should_fire: "add a permission"
should_not_fire: "hello"
YEOF
make_skill "$EC_ROOT" keymixed keymixed "Resolver eval, list on one side and a string on the other."
mkdir -p "$EC_ROOT/keymixed/evals"
cat > "$EC_ROOT/keymixed/evals/resolver.yaml" <<'YEOF'
should_fire: ["a"]
should_not_fire: "hello"
YEOF
# present_but_vacuous — MINOR-3's own fail-open guard: admitting maps under the
# key-polarity keys must not reopen BLOCKER-2 wearing the other key class. A
# schema DESCRIBING the resolver format is not the resolver eval.
make_skill "$EC_ROOT" resolverschema resolverschema "JSON Schema for the resolver format."
mkdir -p "$EC_ROOT/resolverschema/evals"
cat > "$EC_ROOT/resolverschema/evals/resolver.schema.json" <<'JEOF'
{"$schema": "http://json-schema.org/draft-07/schema#", "type": "object",
 "properties": {"should_fire": {"type": "array", "items": {"type": "string"}},
                "should_not_fire": {"type": "array", "items": {"type": "string"}}}}
JEOF

# no_trigger_eval — the mutation target for _other_assertion_weight's empty-value
# guard. Deleting that guard left the whole suite green, which contradicted the
# CHANGELOG's "every one is mutation-proven": here it flips other_assertions 0 -> 3.
make_skill "$EC_ROOT" emptyother emptyother "Other-shape suite whose assertions are all empty."
mkdir -p "$EC_ROOT/emptyother/evals"
cat > "$EC_ROOT/emptyother/evals/cases.json" <<'JEOF'
{"cases": [{"prompt": "a", "expect": "", "expected": null, "assertion": "   "}]}
JEOF

# covered — MINOR-5: `name.upper().startswith("README")` classified a real
# two-sided case set as `none` because its filename begins with those six letters.
make_skill "$EC_ROOT" readmecases readmecases "Case set whose name starts with README."
mkdir -p "$EC_ROOT/readmecases/evals"
cat > "$EC_ROOT/readmecases/evals/README-cases.json" <<'JEOF'
{"cases": [{"prompt": "a", "should_trigger": true}, {"prompt": "b", "should_trigger": false}]}
JEOF
# none — and the two other real README spellings stay documentation, not artifacts
make_skill "$EC_ROOT" readmebare readmebare "Evals dir with an extensionless README."
mkdir -p "$EC_ROOT/readmebare/evals"
echo 'evals are TODO' > "$EC_ROOT/readmebare/evals/README"
make_skill "$EC_ROOT" readmetxt readmetxt "Evals dir with a README.txt."
mkdir -p "$EC_ROOT/readmetxt/evals"
echo 'evals are TODO' > "$EC_ROOT/readmetxt/evals/README.txt"

# Roots holding exactly ONE vacuous sub-state each, so the gate's wording can be
# checked against a known sub-state with no other entry able to supply the words.
EC_UNG="$FX3/ungradable"; EC_CLAIM="$FX3/claimed"; mkdir -p "$EC_UNG" "$EC_CLAIM"
make_skill "$EC_UNG" ungonly ungonly "Only a 0-byte placeholder artifact."
mkdir -p "$EC_UNG/ungonly/evals"; : > "$EC_UNG/ungonly/evals/todo.json"
make_skill "$EC_CLAIM" claimonly claimonly "Positive cases only."
mkdir -p "$EC_CLAIM/claimonly/evals"
cat > "$EC_CLAIM/claimonly/evals/prompts.json" <<'JEOF'
{"cases": [{"prompt": "a", "should_trigger": true}]}
JEOF

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

# ...and the same stray-sibling precedence for the IO-error flavour: `unreadable`
# above covers the LONE case (still gated), this covers "next to a real suite".
# Kept in the IO root so it inherits the running-as-root skip below.
make_skill "$EC_IO" straysuiteio straysuiteio "Graded suite plus an unreadable sibling."
mkdir -p "$EC_IO/straysuiteio/evals"
cat > "$EC_IO/straysuiteio/evals/scenarios.yaml" <<'YEOF'
skill: straysuiteio
scenarios:
  - id: s1
    expect: { decision: defer }
    deterministic_test: tests/test_loop.py::test_defer
YEOF
cat > "$EC_IO/straysuiteio/evals/locked.json" <<'JEOF'
{"cases": [{"prompt": "a", "should_trigger": true}]}
JEOF
chmod 000 "$EC_IO/straysuiteio/evals/locked.json"

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
ec_ung()      { BSTACK_AUDIT_ROOTS="$EC_UNG"   BSTACK_DIR="$FAKE_BSTACK" python3 "$AUDIT_PY" "$@"; }
ec_claim()    { BSTACK_AUDIT_ROOTS="$EC_CLAIM" BSTACK_DIR="$FAKE_BSTACK" python3 "$AUDIT_PY" "$@"; }

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

# ── round-3 checks ────────────────────────────────────────────────────────────
# T40: MAJOR-1 — a stray ungradable sibling must NOT outrank a real graded suite.
#      Mutation: hoist the ungradable branch back above other/parsed → this reads
#      present_but_vacuous with trigger_keys 0 / other_assertions 2, and the gate
#      fires on a skill that never made a trigger claim.
ec_expect straysuite no_trigger_eval
# T40b: the documented exception, finally pinned — covered wins over the stray too
ec_expect straycovered covered
# T40c: and the ungradable state stays REACHABLE when it is the ONLY thing present
#       (the precedence guard must narrow the branch, not delete it)
ec_expect lonetodo present_but_vacuous
# T40d: the stray is still NAMED in both winning classes — silently dropping it
#       would trade a false gate for a blind spot
t="a stray ungradable sibling is still named in the winning class's reason"
if ec_audit --json --no-logs 2>/dev/null | python3 -c '
import json, sys
cov = json.load(sys.stdin)["eval_coverage"]
o = next(x for x in cov["no_trigger_eval"] if x["name"] == "straysuite")
assert o["trigger_keys"] == 0 and o["other_assertions"] >= 2, o
assert "ungradable sibling" in o["reason"] and "todo.json" in o["reason"], o["reason"]
c = next(x for x in cov["covered"] if x["name"] == "straycovered")
assert "ungradable sibling" in c["reason"] and "todo.json" in c["reason"], c["reason"]
' 2>/dev/null; then ap "$t"; else af "$t"; fi
# T40e: the IO-error flavour of the same precedence (skipped when running as root,
#       which reads a chmod-000 file regardless)
t="an UNREADABLE stray sibling does not outrank a real graded suite"
if [ -r "$EC_IO/straysuiteio/evals/locked.json" ]; then
    as_ "$t (file still readable — running as root?)"
else
    io2_state=$(ec_io --json --no-logs 2>/dev/null | python3 -c '
import json, sys
cov = json.load(sys.stdin)["eval_coverage"]
print(next(s for s in cov["counts"] if any(e["name"] == "straysuiteio" for e in cov[s])))
' 2>/dev/null)
    if [ "$io2_state" = "no_trigger_eval" ]; then ap "$t"; else af "$t" "got '$io2_state'"; fi
fi

# T41: MAJOR-2 — the gate's words and the entry's own trigger_keys cannot disagree.
#      An entry with trigger_keys == 0 must never be described as "uses trigger
#      keys", and must never be told to "add the missing side" of a claim it did
#      not make. Checked on a root whose ONLY vacuous skill is that sub-state, so
#      no other entry can supply the words.
t="ungradable-only gate message never claims trigger keys (MAJOR-2)"
out=$(ec_ung --no-logs --require-evals 2>/dev/null); rc=$?
if [ "$rc" -eq 1 ] \
   && ! echo "$out" | grep -q 'uses trigger keys' \
   && ! echo "$out" | grep -q 'add the missing side' \
   && echo "$out" | grep -q 'is not two-sided coverage' \
   && echo "$out" | grep -q 'make the artifact gradable'; then
    ap "$t"
else
    af "$t" "rc=$rc"
fi
# T41b: converse — a real one-sided claim still gets the "add the missing side"
#       advice, so the branch above is a split and not a blanket softening
t="one-sided claim still gets the 'add the missing side' advice"
out=$(ec_claim --no-logs --require-evals 2>/dev/null); rc=$?
if [ "$rc" -eq 1 ] \
   && echo "$out" | grep -q 'uses trigger keys' \
   && echo "$out" | grep -q 'add the missing side' \
   && ! echo "$out" | grep -q 'make the artifact gradable'; then
    ap "$t"
else
    af "$t" "rc=$rc"
fi
# T41c: on a MIXED root the two sub-headers' counts must equal the --json split by
#       trigger_keys — the report cannot narrate a distribution the data denies
t="vacuous sub-header counts match the --json split by trigger_keys"
if python3 - <<PYEOF 2>/dev/null
import json, re, subprocess, sys
env_args = ["--json", "--no-logs"]
j = json.loads(subprocess.run([sys.executable, "$AUDIT_PY", *env_args],
    env={**__import__("os").environ, "BSTACK_AUDIT_ROOTS": "$EC_ROOT",
         "BSTACK_DIR": "$FAKE_BSTACK"},
    capture_output=True, text=True).stdout)
vac = j["eval_coverage"]["present_but_vacuous"]
want_claimed = sum(1 for v in vac if v["trigger_keys"])
want_ung = sum(1 for v in vac if not v["trigger_keys"])
assert want_claimed and want_ung, "fixture root must exercise BOTH sub-states"
human = subprocess.run([sys.executable, "$AUDIT_PY", "--no-logs"],
    env={**__import__("os").environ, "BSTACK_AUDIT_ROOTS": "$EC_ROOT",
         "BSTACK_DIR": "$FAKE_BSTACK"},
    capture_output=True, text=True).stdout
got_claimed = re.search(r"uses trigger keys without reaching two-sided coverage \((\d+)\)", human)
got_ung = re.search(r"ships an evals/ artifact that is not two-sided coverage \((\d+)\)", human)
assert got_claimed and int(got_claimed.group(1)) == want_claimed, (got_claimed, want_claimed)
assert got_ung and int(got_ung.group(1)) == want_ung, (got_ung, want_ung)
PYEOF
then ap "$t"; else af "$t"; fi

# T42: MINOR-3 — three real resolver shapes the round-1 fallthrough downgraded
ec_expect keydict covered
ec_expect keystr covered
ec_expect keymixed covered
# T42b: and each is TWO distinct sets, not one contradictory case — reading the
#       distinct-set property off `isinstance(v, list)` made keystr/keydict read
#       contradictory once their values stopped being lists
t="key-polarity sides are distinct sets in every value shape (1 pos / 1 neg, 0 contradictory)"
if ec_audit --json --no-logs 2>/dev/null | python3 -c '
import json, sys
cov = json.load(sys.stdin)["eval_coverage"]
for name in ("keydict", "keystr", "keymixed"):
    e = next(x for x in cov["covered"] if x["name"] == name)
    assert (e["positive"], e["negative"], e["contradictory"]) == (1, 1, 0), (name, e)
' 2>/dev/null; then ap "$t"; else af "$t"; fi
# T42c: BLOCKER-2 stays closed on BOTH key classes. schemafile/template pin the
#       value-polarity side (T31/T31b/T32); this pins the side MINOR-3 opened.
ec_expect resolverschema present_but_vacuous
t="a schema describing the RESOLVER format contributes ZERO polarity (BLOCKER-2, key-polarity side)"
if ec_audit --json --no-logs 2>/dev/null | python3 -c '
import json, sys
cov = json.load(sys.stdin)["eval_coverage"]
e = next(x for x in cov["present_but_vacuous"] if x["name"] == "resolverschema")
assert (e["positive"], e["negative"]) == (0, 0), e
assert e["trigger_keys"] >= 2, e
' 2>/dev/null; then ap "$t"; else af "$t"; fi

# T43: _other_assertion_weight's empty-value guard, pinned. Deleting the guard
#      left 55/55 green — the count is the only observable, so assert the COUNT.
#      Mutation: drop the `if value is None or (str and not strip())` line →
#      other_assertions becomes 3 and this goes red.
t="empty/None/whitespace other-assertion values weigh 0 (mutation-proof)"
if ec_audit --json --no-logs 2>/dev/null | python3 -c '
import json, sys
cov = json.load(sys.stdin)["eval_coverage"]
e = next(x for x in cov["no_trigger_eval"] if x["name"] == "emptyother")
assert e["other_assertions"] == 0, e
assert "no trigger keys" in e["reason"], e["reason"]
' 2>/dev/null; then ap "$t"; else af "$t"; fi

# T44: MINOR-5 — README matching is extension-aware, not a bare name prefix
ec_expect readmecases covered
ec_expect readmebare none
ec_expect readmetxt none

# T45: the .toml < 3.11 degrade path, pinned by SIMULATING the import failure.
#      Previously it was reachable only on an interpreter this repo does not run,
#      so the CHANGELOG called proven a branch nothing exercised. Mutation: return
#      None instead of _UNSUPPORTED for a missing tomllib → present_but_vacuous.
t=".toml degrades to a NON-gated class when tomllib is absent (simulated <3.11)"
if python3 - "$AUDIT_PY" "$EC_ROOT/tomlcovered" <<'PYEOF' 2>/dev/null
import importlib.util, sys
from pathlib import Path
spec = importlib.util.spec_from_file_location("skill_audit_toml_degrade", sys.argv[1])
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
d = Path(sys.argv[2])
if m.tomllib is not None:                       # control on 3.11+
    assert m.classify_eval_coverage(d)["state"] == "covered", m.classify_eval_coverage(d)
m.tomllib = None                                # simulate python < 3.11
info = m.classify_eval_coverage(d)
assert info["state"] == "no_trigger_eval", info
assert "cannot parse" in info["reason"] and "3.11" in info["reason"], info["reason"]
assert (info["positive"], info["negative"]) == (0, 0), info
PYEOF
then ap "$t"; else af "$t"; fi

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
chmod 644 "$EC_IO/straysuiteio/evals/locked.json" 2>/dev/null || true
rm -rf "$FX3"
rm -rf "$FX2"
rm -rf "$FX"
echo ""
echo "── results: $PASS passed, $FAIL failed, $SKIP skipped ──────────────"
if [ "$FAIL" -gt 0 ]; then printf '  failed: %s\n' "${FAILED[@]}"; exit 1; fi
exit 0
