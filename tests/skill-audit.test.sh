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

# T9: human report has all 5 sections
t="human report has 5 sections"
out=$(run_audit --no-logs 2>/dev/null)
if echo "$out" | grep -q '## Budget' && echo "$out" | grep -q '## Duplicates' \
   && echo "$out" | grep -q '## Registry coherence' && echo "$out" | grep -q '## Unused' \
   && echo "$out" | grep -q '## Roots'; then ap "$t"; else af "$t"; fi

rm -rf "$FX"
echo ""
echo "── results: $PASS passed, $FAIL failed ────────────────────────────"
if [ "$FAIL" -gt 0 ]; then printf '  failed: %s\n' "${FAILED[@]}"; exit 1; fi
exit 0
