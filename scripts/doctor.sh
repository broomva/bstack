#!/bin/bash
# bstack/scripts/doctor.sh — Validate AGENTS.md / CLAUDE.md / .control/policy.yaml
# compliance with the bstack primitive contract.
#
# Runs at SessionStart (via hook) AND on demand (`bstack doctor`).
# Always exits 0 — never blocks a session. Reports gaps as actionable nudges.
#
# What it checks:
#   1. CLAUDE.md primitives table has all P1-P20 rows + correct count
#   2. AGENTS.md has each primitive section (### P1: through ### P20:)
#   3. AGENTS.md has the binding reflexive trigger rules for primitives
#      that require them (P6, P9, P10, P11, P12, P13, P14, P15, P16, P17,
#      P18, P19, P20 — primitives where the agent's reasoning enforces
#      the policy, not a hook; P7 = Skill Freshness hook,
#      P8 = Janitor make-target, P9 = CI Watcher / Productive Wait)
#   4. .control/policy.yaml has required blocks (ci_watch, ci_heal, auto_merge)
#   5. .claude/settings.json hooks wire the expected primitive scripts:
#      - P1: scripts/conversation-bridge-hook.sh (Stop + Notification)
#      - P2: scripts/control-gate-hook.sh (PreToolUse — Bash/Write/Edit)
#      - P7: scripts/skill-freshness-hook.sh (SessionStart)
#      - P17: ~/.agents/skills/role-x/scripts/role-x-intake-hook.sh (UserPromptSubmit)
#      - P17: ~/.agents/skills/role-x/scripts/role-x-coverage-hook.sh (SessionStart)
#   6. Each primitive's mechanism is reachable on disk:
#      - P1: scripts/conversation-bridge-hook.sh
#      - P2: scripts/control-gate-hook.sh + .control/policy.yaml
#      - P6: skills/bookkeeping/scripts/bookkeeping.py
#      - P7: scripts/skill-freshness-hook.sh
#      - P8: scripts/branch-janitor.sh
#      - P9: skills/p9/scripts/p9.py (Productive Wait — skill repo name matches primitive number)
#      - P12: skills/persist/scripts/persist.py
#      - P17: ~/.agents/skills/role-x/scripts/{role-x.py,role-x-{intake,coverage}-hook.sh}
#   7. (continued — each primitive's mechanism)
#   8. L3 trust gates (G-L3-1 + G-L3-2) pass via scripts/bstack-primitive-lint.py
#      and scripts/bstack-rule-of-three.py; broomva/autonomous skill installed
#      (the canonical operating mode on top of the substrate)
#   9. Naming convention propagation — Name (Pn) rule + Short-name index
#      present in CLAUDE.md + AGENTS.md (the LLM-loaded surfaces). Index has
#      exactly 20 entries (one per primitive).
#  10. Schema validation (v0.6.0+) — references/primitives.yaml validates
#      against schemas/primitives.v1.json; workspace .control/policy.yaml
#      validates against schemas/policy.v1.json. Skipped gracefully if
#      python3 + jsonschema + PyYAML are absent.
#  11. Gate enforcement type validation (v0.8.0+) — every blocking gate in
#      .control/policy.yaml must have a `pattern`, `enforcement.spec`, or
#      `measurement`. Advisory gates exempt. Catches gates declared
#      "blocking" with no mechanism behind them (Gap 4.2.1).
#
# Usage:
#   bash scripts/doctor.sh               # full report
#   bash scripts/doctor.sh --quiet       # only warnings
#   bash scripts/doctor.sh --strict      # exit 1 if any gap found (CI mode)
#
# Env:
#   BSTACK_LOOP_STRICT=1   §23 only — treat a wired-but-idle control loop as a
#                          gap. To FAIL CI on it you must ALSO pass --strict
#                          (i.e. `BSTACK_LOOP_STRICT=1 doctor.sh --strict`);
#                          BSTACK_LOOP_STRICT alone records the gap but never
#                          changes the exit code.

set -uo pipefail

# ── arg parsing ─────────────────────────────────────────────────────────────
QUIET=0
STRICT=0
while [ $# -gt 0 ]; do
    case "$1" in
        --quiet|-q) QUIET=1; shift ;;
        --strict)   STRICT=1; shift ;;
        --help|-h)
            grep -E '^#( |$)' "$0" | sed 's/^# \?//' | head -30
            exit 0 ;;
        *) shift ;;
    esac
done

# ── locate workspace ────────────────────────────────────────────────────────
# Resolve via the shared resolver (env → cwd-is-workspace → ~/.bstack config →
# cwd) — NEVER a hardcoded $HOME/broomva default. A bare `bstack doctor` from an
# unrelated dir used to silently audit the healthy self-hosting ~/broomva and
# report false-GREEN off-broomva (BRO-1715). If resolution lands on a non-workspace,
# FAIL LOUD (exit non-zero) rather than default — "nothing to audit" is a hard
# error, distinct from the soft gaps the run otherwise exits 0 on.
_DOCTOR_LIB="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/resolve-workspace.sh"
if [ -f "$_DOCTOR_LIB" ]; then
    # shellcheck source=scripts/lib/resolve-workspace.sh
    . "$_DOCTOR_LIB"
    WORKSPACE="$(resolve_workspace)"
else
    # Degraded install (no resolver lib) — still never default to ~/broomva.
    WORKSPACE="${BROOMVA_WORKSPACE:-$PWD}"
fi
if [ ! -d "$WORKSPACE/.git" ] && [ ! -d "$WORKSPACE/.control" ]; then
    echo "[bstack doctor] workspace not found at $WORKSPACE" >&2
    echo "                set BROOMVA_WORKSPACE, or run from inside a bstack workspace" >&2
    exit 3
fi

# ── locate bstack repo (needed by §14 + §15 L3 stability checks) ────────────
BSTACK_REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# ── helpers ─────────────────────────────────────────────────────────────────
GAPS=0
PASSES=0

ok() {
    PASSES=$((PASSES + 1))
    [ "$QUIET" = "0" ] && echo "  [ok] $1"
}

gap() {
    GAPS=$((GAPS + 1))
    echo "  [gap] $1"
    [ -n "${2:-}" ] && echo "        → fix: $2"
}

section() {
    [ "$QUIET" = "0" ] && echo "" && echo "$1"
}

# ── 1. Governance files exist ───────────────────────────────────────────────
section "1. Governance files"
for f in CLAUDE.md AGENTS.md .control/policy.yaml; do
    if [ -f "$WORKSPACE/$f" ]; then
        ok "$f"
    else
        gap "$f missing" "create or restore from upstream bstack template"
    fi
done

# ── 2. CLAUDE.md primitives table ───────────────────────────────────────────
section "2. CLAUDE.md primitives table"
CLAUDE="$WORKSPACE/CLAUDE.md"
if [ -f "$CLAUDE" ]; then
    EXPECTED_COUNT=20
    if grep -qE "^(Twenty|20) irreducible building blocks" "$CLAUDE"; then
        ok "primitive count header reads Twenty/20"
    else
        ACTUAL=$(grep -oE "^(One|Two|Three|Four|Five|Six|Seven|Eight|Nine|Ten|Eleven|Twelve|Thirteen|Fourteen|Fifteen|Sixteen|Seventeen|Eighteen|Nineteen|Twenty|[0-9]+) irreducible" "$CLAUDE" | head -1)
        gap "primitive count header off (expected 'Twenty irreducible'; saw '$ACTUAL')" \
            "edit CLAUDE.md → 'Bstack Core Automation Primitives' header"
    fi

    for n in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20; do
        if grep -qE "^\| P$n \|" "$CLAUDE"; then
            ok "P$n row present"
        else
            gap "P$n row missing in primitives table" \
                "add row to CLAUDE.md primitives table"
        fi
    done
fi

# ── 3. AGENTS.md primitive sections ─────────────────────────────────────────
section "3. AGENTS.md primitive sections"
AGENTS="$WORKSPACE/AGENTS.md"
declare -a P_NAMES=(
    "P1: Conversation Bridge"
    "P2: Control Gate"
    "P3: Linear Ticket"
    "P4: PR Pipeline"
    "P5: Parallel Agent"
    "P6: Knowledge Bookkeeping"
    "P7: Skill Freshness"
    "P8: Branch + Worktree Janitor"
    "P9: CI Watcher + Productive Wait"
    "P10: Worktree Hygiene"
    "P11: Empirical Feedback Loop"
    "P12: Persistent Loop Discipline"
    "P13: Dream Cycle Discipline"
    "P14: Dependency-Chain Reasoning"
    "P15: State-Snapshot Before Action"
    "P16: Crystallization Discipline"
    "P17: Lens-Routed Request Articulation"
    "P18: Format-Follows-Audience Discipline"
    "P19: Orchestration-Mechanism Selection"
    "P20: Cross-Model Adversarial Review Gate"
)
if [ -f "$AGENTS" ]; then
    for entry in "${P_NAMES[@]}"; do
        prefix="${entry%%:*}"   # e.g. "P1"
        # Accept either:
        #   ### P1: Title                  (original bstack format)
        #   ### P1 — Label: Title          (extended format with categorical label)
        # Trailing word-boundary ensures P1 doesn't match P10/P11/P12.
        if grep -qE "^### $prefix(:| —)" "$AGENTS"; then
            ok "section $prefix present"
        else
            gap "AGENTS.md missing '### $entry' section" \
                "append the primitive section per CLAUDE.md primitives table"
        fi
    done
fi

# ── 4. AGENTS.md reflexive trigger rules ────────────────────────────────────
section "4. AGENTS.md reflexive trigger rules"
# Primitives whose discipline is enforced via agent reasoning rather than hooks.
# These MUST contain a Reflexive Trigger Rule subsection.
# Note: workspace canonical numbering — P7 = Skill Freshness (hook-enforced),
# P8 = Janitor (mechanism-only), P9 = Productive Wait (reasoning-enforced,
# skill name `broomva/p9` matches primitive number).
declare -a REFLEXIVE_PRIMS=(P6 P9 P10 P11 P12 P13 P14 P15 P16 P17 P18 P19 P20)
if [ -f "$AGENTS" ]; then
    for prim in "${REFLEXIVE_PRIMS[@]}"; do
        # Look for "P{n} is a reflex" OR "Reflexive Trigger Rule" in proximity to the prim section.
        # Accept either '### P{n}: Title' or '### P{n} — Label: Title' header format.
        if awk -v p="$prim" '
            /^### / { in_sec = ($0 ~ "^### "p"(:| —)") }
            in_sec && /Reflexive Trigger Rule/ { found = 1 }
            in_sec && / is a reflex/ { found = 1 }
            END { exit (found ? 0 : 1) }
        ' "$AGENTS"; then
            ok "$prim has reflexive trigger rule"
        else
            gap "$prim section in AGENTS.md missing 'Reflexive Trigger Rule' subsection" \
                "add the binding-on-every-agent rule following P6/P9's pattern"
        fi
    done
fi

# ── 4b. AGENTS.md Development Philosophy section (advisory, backfillable) ────
# Templated since bstack 0.24.0. A workspace bootstrapped before then
# legitimately lacks it — this is NOT a contract violation (the P1-P20 contract
# is unchanged), so it is reported as an informational advisory only: never a
# GAP, never fails --strict (mirrors §12 Pillars / §13 dogfood convention).
# `bstack repair` backfills it from the template.
section "4b. AGENTS.md Development Philosophy (advisory)"
if [ -f "$AGENTS" ]; then
    if grep -qE "^## Development Philosophy" "$AGENTS"; then
        ok "AGENTS.md has Development Philosophy section"
    else
        echo "  [info] AGENTS.md has no Development Philosophy section (templated since 0.24.0)"
        echo "         → backfill: bstack repair  (informational; not a gap, does not fail --strict)"
    fi
fi

# ── 4c. AGENTS.md P6 retrieval-discipline reflex (advisory, backfillable) ────
# The "/kg for discovery, never substrate grep" reflex was added to the §P6
# template with the BRO-1426 reflex. A workspace bootstrapped before then
# legitimately lacks it (scaffold never overwrites an existing AGENTS.md), so —
# like §4b — this is informational only: never a GAP, never fails --strict.
# `bstack repair` backfills it. Detection mirrors repair's two signals so the two
# agree on "present": the coined phrase "substrate grep" (catches the template +
# "never"/"not a" wording variants) OR the structural `**Retrieval discipline`
# lead (catches a reflex reworded to drop the phrase).
section "4c. AGENTS.md retrieval-discipline reflex (advisory)"
if [ -f "$AGENTS" ]; then
    if grep -qF "substrate grep" "$AGENTS" || grep -qE "^\*\*Retrieval discipline" "$AGENTS"; then
        ok "AGENTS.md has P6 retrieval-discipline reflex (/kg for discovery)"
    else
        echo "  [info] AGENTS.md has no P6 retrieval-discipline reflex (/kg for discovery, never substrate grep)"
        echo "         → backfill: bstack repair  (informational; not a gap, does not fail --strict)"
    fi
fi

# ── 5. policy.yaml required blocks ──────────────────────────────────────────
section "5. .control/policy.yaml blocks"
POL="$WORKSPACE/.control/policy.yaml"
if [ -f "$POL" ]; then
    for block in ci_watch ci_heal auto_merge; do
        if grep -qE "^${block}:" "$POL"; then
            ok "$block: block present"
        else
            gap "$block: block missing from policy.yaml" \
                "P9 fails closed without ci_watch/ci_heal; auto-merge actuator needs auto_merge:"
        fi
    done
fi

# ── 6. Claude Code hooks wired ──────────────────────────────────────────────
section "6. .claude/settings.json hook wiring"
SETTINGS="$WORKSPACE/.claude/settings.json"
# Use parallel arrays instead of associative (bash 3.2 compatible)
HOOK_FILES=(
    "conversation-bridge-hook.sh"
    "control-gate-hook.sh"
    "skill-freshness-hook.sh"
    "role-x-intake-hook.sh"
    "role-x-coverage-hook.sh"
)
HOOK_LABELS=(
    "P1 (Stop, Notification)"
    "P2 (PreToolUse)"
    "P7 (SessionStart)"
    "P17 (UserPromptSubmit)"
    "P17 (SessionStart)"
)
if [ -f "$SETTINGS" ]; then
    for i in "${!HOOK_FILES[@]}"; do
        hk="${HOOK_FILES[$i]}"
        label="${HOOK_LABELS[$i]}"
        if grep -q "$hk" "$SETTINGS"; then
            ok "$hk wired ($label)"
        else
            gap "$hk not wired in .claude/settings.json ($label)" \
                "add the hook entry per AGENTS.md primitive spec — run 'bstack repair' or 'bstack bootstrap' to wire from assets/templates/settings.json.snippet"
        fi
    done
fi

# ── 7. Primitive mechanisms reachable on disk ───────────────────────────────
section "7. Primitive mechanisms (scripts/skills present)"
SCRIPT_PATHS=(
    "scripts/conversation-bridge-hook.sh"
    "scripts/control-gate-hook.sh"
    "skills/bookkeeping/scripts/bookkeeping.py"
    "scripts/skill-freshness-hook.sh"
    "scripts/branch-janitor.sh"
    "skills/p9/scripts/p9.py"
    "skills/persist/scripts/persist.py"
)
SCRIPT_LABELS=(P1 P2 P6 P7 P8 P9 P12)
for i in "${!SCRIPT_PATHS[@]}"; do
    path="${SCRIPT_PATHS[$i]}"
    label="${SCRIPT_LABELS[$i]}"
    _found=""
    if [ -e "$WORKSPACE/$path" ]; then
        _found="$path"
    elif [[ "$path" == skills/* ]]; then
        # Skill mechanisms (P6/P9/P12) install GLOBALLY — `npx skills add -g` lands
        # them in ~/.claude/skills AND ~/.agents/skills, not the workspace tree. A
        # workspace-relative miss is NOT a real gap if the skill resolves globally
        # (BRO-1715 — this section used to false-RED every non-self-hosting install).
        # Mirrors the /kg global-dir check below.
        _rel="${path#skills/}"
        for _g in "$HOME/.claude/skills/$_rel" "$HOME/.agents/skills/$_rel"; do
            if [ -e "$_g" ]; then _found="$_g"; break; fi
        done
    fi
    if [ -n "$_found" ]; then
        ok "$label: $_found"
    else
        gap "$label mechanism missing: $path" \
            "install the corresponding skill: npx skills add broomva/skills --skill <name> -g"
    fi
done

# P19 mechanism: bstack wave subcommand
_WAVE_BIN="$WORKSPACE/bin/bstack-wave"
_WAVE_PY="$WORKSPACE/scripts/wave.py"
if [ -x "$_WAVE_BIN" ] && [ -f "$_WAVE_PY" ]; then
  ok "P19 mechanism: bin/bstack-wave + scripts/wave.py present"
else
  gap "P19 mechanism: bin/bstack-wave or scripts/wave.py missing" \
      "install/rebuild from bstack repo HEAD"
fi

# P6 catalog: knowledge-catalog-refresh-hook + docs/knowledge-index.md freshness
# (LLM-as-index architecture — substrate routes through the catalog; the
# catalog must regenerate at Stop time and stay <48h old)
_CATALOG_HOOK="$WORKSPACE/scripts/knowledge-catalog-refresh-hook.sh"
_CATALOG="$WORKSPACE/docs/knowledge-index.md"
if [ -x "$_CATALOG_HOOK" ]; then
    ok "P6 catalog hook: scripts/knowledge-catalog-refresh-hook.sh present + executable"
else
    gap "P6 catalog hook missing or not executable: scripts/knowledge-catalog-refresh-hook.sh" \
        "copy from bstack/assets/templates/ or rerun 'bstack repair'"
fi
if [ -f "$_CATALOG" ]; then
    if [ "$(uname)" = "Darwin" ]; then
        _catalog_mtime=$(stat -f %m "$_CATALOG" 2>/dev/null || echo 0)
    else
        _catalog_mtime=$(stat -c %Y "$_CATALOG" 2>/dev/null || echo 0)
    fi
    _now=$(date +%s)
    _age_h=$(( (_now - _catalog_mtime) / 3600 ))
    # Threshold precedence: .control/policy.yaml catalog.stale_doctor_hours,
    # then hardcoded 48h fallback. Single source of truth for catalog
    # thresholds — see BRO-1223 I1 (was: three different "stale" values
    # across kg.py / doctor.sh / hook).
    #
    # Defensive: argv-passing avoids SyntaxError on single-quote-in-path
    # (P20 I1); regex validation handles empty/non-numeric stdout (P20 C3).
    _policy_file="$WORKSPACE/.control/policy.yaml"
    _stale_h=48
    if [ -f "$_policy_file" ] && command -v python3 >/dev/null 2>&1; then
        _raw=$(python3 -c '
import sys
try:
    import yaml
    with open(sys.argv[1]) as f: d = yaml.safe_load(f) or {}
    print(int((d.get("catalog") or {}).get("stale_doctor_hours", 48)))
except Exception:
    print(48)
' "$_policy_file" 2>/dev/null)
        if [[ "$_raw" =~ ^[0-9]+$ ]]; then
            _stale_h="$_raw"
        fi
    fi
    if [ "$_age_h" -le "$_stale_h" ]; then
        ok "P6 catalog fresh: docs/knowledge-index.md (${_age_h}h old; threshold ${_stale_h}h)"
    else
        gap "P6 catalog stale: docs/knowledge-index.md (${_age_h}h old; threshold ${_stale_h}h)" \
            "run 'python3 skills/bookkeeping/scripts/bookkeeping.py index'"
    fi
else
    gap "P6 catalog missing: docs/knowledge-index.md" \
        "run 'python3 skills/bookkeeping/scripts/bookkeeping.py index'"
fi

# /kg load skill — vendored in the broomva/skills monorepo (knowledge/kg).
# Accepts either an `npx skills add broomva/skills --skill kg` install (under
# ~/.claude/skills/kg/ or ~/.agents/skills/kg/) or a legacy workspace-local v1 install.
_kg_installed=0
for _kg_path in "$HOME/.claude/skills/kg" "$HOME/.agents/skills/kg"; do
    if [ -f "$_kg_path/SKILL.md" ] && [ -f "$_kg_path/scripts/kg.py" ]; then
        ok "/kg load skill installed at $_kg_path"
        _kg_installed=1
        break
    fi
done
if [ "$_kg_installed" = "0" ]; then
    gap "/kg load skill missing at ~/.claude/skills/kg/ or ~/.agents/skills/kg/" \
        "install via 'npx skills add broomva/skills --skill kg' (managed roster entry) — see references/skills-roster.md"
fi

# ── L3 trust gates (G-L3-1 + G-L3-2) ───────────────────────────────────────
section "8. L3 trust gates"
L3_PRIMITIVE_LINT="$WORKSPACE/scripts/bstack-primitive-lint.py"
L3_RULE_OF_THREE="$WORKSPACE/scripts/bstack-rule-of-three.py"

if [ -e "$L3_PRIMITIVE_LINT" ]; then
    if python3 "$L3_PRIMITIVE_LINT" >/dev/null 2>&1; then
        ok "G-L3-1 structural completeness (all P-N have required sections + CLAUDE.md row + count match)"
    else
        gap "G-L3-1 structural completeness fails" \
            "run \`python3 $L3_PRIMITIVE_LINT\` to see specific gaps"
    fi
else
    gap "G-L3-1 script missing: scripts/bstack-primitive-lint.py" \
        "copy from broomva/workspace or re-run \`bstack bootstrap\`"
fi

if [ -e "$L3_RULE_OF_THREE" ]; then
    if python3 "$L3_RULE_OF_THREE" >/dev/null 2>&1; then
        ok "G-L3-2 rule-of-three audit (post-P16 primitives have ≥3 logged instances)"
    else
        gap "G-L3-2 rule-of-three audit fails" \
            "run \`python3 $L3_RULE_OF_THREE\` to see which primitive lacks evidence"
    fi
else
    gap "G-L3-2 script missing: scripts/bstack-rule-of-three.py" \
        "copy from broomva/workspace or re-run \`bstack bootstrap\`"
fi

# Canonical operating mode check
if [ -d "$HOME/.agents/skills/autonomous" ] || [ -d "$HOME/.claude/skills/autonomous" ] || [ -d "$WORKSPACE/skills/autonomous" ]; then
    ok "autonomous skill installed (canonical operating mode available)"
else
    gap "autonomous skill not installed" \
        "install the canonical operating mode: npx skills add broomva/skills --skill autonomous"
fi

# ── 9. Naming convention propagation ────────────────────────────────────────
# The `Name (Pn)` rule must be present in the LLM-loaded surfaces (CLAUDE.md +
# AGENTS.md). The Short-name index must enumerate exactly 20 primitives. Closes
# the failure mode where agents revert to bare `Pn` in prose because the rule
# was buried in only one document.
section "9. Naming convention propagation (Name (Pn) rule)"
EXPECTED_INDEX_COUNT=20
for f in CLAUDE.md AGENTS.md; do
    fp="$WORKSPACE/$f"
    [ -f "$fp" ] || continue

    # 9a. Naming rule present (the binding instruction in prose)
    if grep -qE "use the .*Name \(Pn\). form" "$fp" 2>/dev/null; then
        ok "$f restates the Name (Pn) naming rule"
    else
        gap "$f missing the Name (Pn) naming rule in prose" \
            "add §Naming convention paragraph (see bstack/SKILL.md naming-convention block)"
    fi

    # 9b. Short-name index present + has 20 entries
    if grep -qE '^\*\*Short-name index\*\*' "$fp" 2>/dev/null; then
        idx_line="$(grep -E '^\*\*Short-name index\*\*' "$fp" | head -1)"
        idx_entries="$(echo "$idx_line" | grep -oE '\(P[0-9]+\)' | wc -l | tr -d ' ')"
        if [ "$idx_entries" = "$EXPECTED_INDEX_COUNT" ]; then
            ok "$f Short-name index has $EXPECTED_INDEX_COUNT entries"
        else
            gap "$f Short-name index has $idx_entries entries (expected $EXPECTED_INDEX_COUNT)" \
                "the Short-name index must list every primitive once; add or remove entries to match"
        fi
    else
        gap "$f missing **Short-name index** line" \
            "add the canonical short-name index after the naming rule"
    fi
done

# ── Section 10: schema validation (v0.6.0+) ────────────────────────────────
# Validates references/primitives.yaml against schemas/primitives.v1.json and
# workspace .control/policy.yaml against schemas/policy.v1.json (when both
# schemas are present in the bstack install).
#
# Requires python3 + the jsonschema package + PyYAML. Degrades gracefully:
# skipped if dependencies absent (informational message, never a gap).
echo ""
echo "=== Section 10: schema validation ==="
DOCTOR_DIR="$(cd "$(dirname "$0")" && pwd)"
BSTACK_INSTALL_DIR="$(cd "$DOCTOR_DIR/.." && pwd)"
SCHEMAS_DIR="$BSTACK_INSTALL_DIR/schemas"
PRIMITIVES_YAML="$BSTACK_INSTALL_DIR/references/primitives.yaml"

if [ ! -d "$SCHEMAS_DIR" ]; then
    echo "  [skip] no schemas/ directory in this bstack install (pre-v0.6.0?)"
elif ! command -v python3 >/dev/null 2>&1; then
    echo "  [skip] python3 not available — cannot run schema validation"
elif ! python3 -c "import jsonschema, yaml" 2>/dev/null; then
    echo "  [skip] python3 packages jsonschema + PyYAML not installed"
    echo "         pip install jsonschema PyYAML  (then re-run bstack doctor)"
else
    # Validate primitives.yaml against primitives.v1.json
    if [ -f "$PRIMITIVES_YAML" ] && [ -f "$SCHEMAS_DIR/primitives.v1.json" ]; then
        if python3 - "$PRIMITIVES_YAML" "$SCHEMAS_DIR/primitives.v1.json" <<'PYEOF' 2>/dev/null
import json, sys, yaml, jsonschema
data_path, schema_path = sys.argv[1], sys.argv[2]
schema = json.load(open(schema_path))
data = yaml.safe_load(open(data_path))
v = jsonschema.Draft7Validator(schema)
errors = list(v.iter_errors(data))
sys.exit(1 if errors else 0)
PYEOF
        then
            ok "references/primitives.yaml validates against primitives.v1.json"
        else
            gap "references/primitives.yaml fails primitives.v1.json validation" \
                "run \`python3 -c 'import json,yaml,jsonschema; s=json.load(open(\"schemas/primitives.v1.json\")); d=yaml.safe_load(open(\"references/primitives.yaml\")); [print(e.message) for e in jsonschema.Draft7Validator(s).iter_errors(d)]'\`"
        fi
    fi

    # Validate workspace .control/policy.yaml against policy.v1.json (if both exist).
    POLICY_FILE="$WORKSPACE/.control/policy.yaml"
    if [ -f "$POLICY_FILE" ] && [ -f "$SCHEMAS_DIR/policy.v1.json" ]; then
        if python3 - "$POLICY_FILE" "$SCHEMAS_DIR" <<'PYEOF' 2>/dev/null
import json, sys, yaml, jsonschema
from pathlib import Path
policy_path = sys.argv[1]
schemas_dir = Path(sys.argv[2])
store = {}
for f in schemas_dir.glob('*.json'):
    s = json.load(open(f))
    sid = s.get('$id') or f.name
    store[sid] = s
    store[f.name] = s
policy_schema = json.load(open(schemas_dir / 'policy.v1.json'))
resolver = jsonschema.RefResolver(base_uri='', referrer=policy_schema, store=store)
v = jsonschema.Draft7Validator(policy_schema, resolver=resolver)
errors = list(v.iter_errors(yaml.safe_load(open(policy_path))))
sys.exit(1 if errors else 0)
PYEOF
        then
            ok ".control/policy.yaml validates against policy.v1.json"
        else
            gap ".control/policy.yaml fails policy.v1.json validation" \
                "see bstack schemas/policy.v1.json + workspace .control/policy.yaml; \`scripts/migrate.sh\` may be needed"
        fi
    fi
fi

# ── Section 11: gate enforcement type validation (v0.8.0+) ─────────────────
# Every blocking/governed gate must have a `pattern`, `enforcement.spec`,
# or `measurement`. Advisory / soft / warn severities exempt. Catches
# gates declared "blocking" with no mechanism behind them (Gap 4.2.1).
echo ""
echo "=== Section 11: gate enforcement type validation ==="
POLICY_FOR_GATES="$WORKSPACE/.control/policy.yaml"
if [ ! -f "$POLICY_FOR_GATES" ]; then
    echo "  [skip] no policy.yaml at $POLICY_FOR_GATES"
elif ! command -v python3 >/dev/null 2>&1 || ! python3 -c "import yaml" 2>/dev/null; then
    echo "  [skip] python3 + PyYAML required for gate-enforcement lint"
else
    gate_lint=$(python3 - "$POLICY_FOR_GATES" <<'PYEOF'
import sys, yaml
data = yaml.safe_load(open(sys.argv[1]))
gates = data.get("gates", {}) or {}
problems = []
ok_count = 0
for category, items in gates.items():
    if not isinstance(items, list):
        continue
    for g in items:
        if not isinstance(g, dict):
            continue
        gid = g.get("id", "?")
        severity = g.get("severity", "")
        if severity in ("advisory", "soft", "warn"):
            ok_count += 1
            continue
        has_pattern = bool(g.get("pattern"))
        has_enforcement = bool((g.get("enforcement") or {}).get("spec"))
        has_measurement = bool(g.get("measurement"))
        if has_pattern or has_enforcement or has_measurement:
            ok_count += 1
        else:
            problems.append(f"{gid} ({category}): no pattern, no enforcement.spec, no measurement")
if problems:
    print("GAP")
    for p in problems:
        print(p)
else:
    print(f"OK {ok_count}")
PYEOF
)
    if echo "$gate_lint" | head -1 | grep -q "^OK "; then
        n=$(echo "$gate_lint" | head -1 | awk '{print $2}')
        ok "all $n declared gates have enforcement type (pattern / runtime_check / measurement)"
    else
        echo "  [gap] blocking gates without enforcement mechanism:"
        echo "$gate_lint" | tail -n +2 | sed 's/^/         /'
        gap "Section 11 found gates declared blocking with no enforcement" \
            "add 'pattern' / 'enforcement.spec' / 'measurement' to each blocking gate (or downgrade severity to advisory)"
    fi
fi

# ── Section 12: Four Pillars of Self-Operation (informational) ──────────────
# Surface the four canonical agentic-systems pillars and which primitives
# deliver each. PARTIAL / GAP states are KNOWN limitations tracked in
# workspace CLAUDE.md §Four Pillars of Self-Operation; they are informational
# only and do NOT count against doctor's GAP total or fail --strict mode.
# The audit's job is to keep the lens visible during install / SessionStart,
# not to gate compliance.
section "12. Four Pillars of Self-Operation"
# Format: pillar_num|pillar_name|primitives|state
# State values: FULL | PARTIAL | GAP (candidate P21 territory)
FOUR_PILLARS=(
    "1|Recursive self-improvement|P1 P6 P13 P16|FULL"
    "2|Setting its own goals|P1 P5 P6 P9 P12 P16 P19|PARTIAL"
    "3|Acquiring its own resources|P2 P3|GAP"
    "4|Acting autonomously|P4 P5 P7 P8 P9 P10 P11 P12 P14 P15 P17 P18 P19 P20|FULL"
)
for entry in "${FOUR_PILLARS[@]}"; do
    pnum="${entry%%|*}"
    rest="${entry#*|}"
    pname="${rest%%|*}"
    rest="${rest#*|}"
    pprims="${rest%%|*}"
    pstate="${rest#*|}"

    case "$pstate" in
        FULL)
            ok "Pillar $pnum ($pname): $pprims — FULL"
            ;;
        PARTIAL)
            [ "$QUIET" = "0" ] && echo "  [info] Pillar $pnum ($pname): $pprims — PARTIAL (goal-formation implicit; see CLAUDE.md §Four Pillars of Self-Operation)"
            ;;
        GAP)
            [ "$QUIET" = "0" ] && echo "  [info] Pillar $pnum ($pname): $pprims — GAP (candidate P21; see CLAUDE.md §Four Pillars of Self-Operation for promotion gating)"
            ;;
    esac
done

# ── Section 13: P11 Empirical dogfood-readiness (informational) ─────────────
# Detect tech stack from repo signals, then verify a Dogfood Plan section
# exists in workspace AGENTS.md OR docs/dogfood-plan.md OR (acceptable but
# weakest) is just expected per-PR via PR body.
#
# Informational — never blocks (rule-of-three for promotion to blocking gate
# not yet hit). Surfaces stack detection + plan presence so the agent applying
# P11 has the right cookbook entry at hand. References: bstack/references/
# dogfood-patterns.md is the canonical per-stack cookbook.
section "13. P11 Empirical dogfood-readiness"

# Detect stack by repo signals (mirrors dogfood-patterns.md §Detection algorithm).
DETECTED_STACK="unknown"
DETECTION_REASON=""

# Is any code build manifest present? (checked individually — `ls a b c` returns
# non-zero if ANY one is missing, so it can't answer "are they all absent".)
CODE_MANIFEST=0
for _m in Cargo.toml package.json go.mod pyproject.toml setup.py pom.xml build.gradle Gemfile composer.json; do
    [ -f "$WORKSPACE/$_m" ] && CODE_MANIFEST=1 && break
done

if [ -f "$WORKSPACE/Cargo.toml" ] && [ -d "$WORKSPACE/src-tauri" ]; then
    DETECTED_STACK="tauri-sidecar"
    DETECTION_REASON="Cargo.toml + src-tauri/ present"
elif [ -d "$WORKSPACE/app/src-tauri" ] || ls "$WORKSPACE"/*/src-tauri 2>/dev/null | head -1 | grep -q . ; then
    DETECTED_STACK="tauri-sidecar"
    DETECTION_REASON="nested src-tauri/ present (multi-package repo)"
elif ls "$WORKSPACE"/next.config.* 2>/dev/null | head -1 | grep -q . ; then
    DETECTED_STACK="nextjs"
    DETECTION_REASON="next.config.* present"
elif [ -f "$WORKSPACE/app.json" ] && grep -q '"expo"' "$WORKSPACE/app.json" 2>/dev/null; then
    DETECTED_STACK="expo-rn"
    DETECTION_REASON="app.json with expo block"
elif [ -f "$WORKSPACE/Cargo.toml" ]; then
    DETECTED_STACK="rust-cli"
    DETECTION_REASON="Cargo.toml without src-tauri/"
elif ls "$WORKSPACE"/openapi.* 2>/dev/null | head -1 | grep -q . ; then
    DETECTED_STACK="rest-api"
    DETECTION_REASON="openapi.* spec present"
elif [ -f "$WORKSPACE/mcp.json" ] || [ -f "$WORKSPACE/mcp.yaml" ]; then
    DETECTED_STACK="mcp-server"
    DETECTION_REASON="mcp.{json,yaml} present"
elif [ -f "$WORKSPACE/package.json" ] && grep -qE '"(fastapi|hono|axum|express)"' "$WORKSPACE/package.json" 2>/dev/null; then
    DETECTED_STACK="rest-api"
    DETECTION_REASON="REST framework dep in package.json"
elif [ "$CODE_MANIFEST" = "0" ] && { [ -d "$WORKSPACE/entities" ] || [ -d "$WORKSPACE/.obsidian" ] || [ -d "$WORKSPACE/vault" ] \
        || [ "$(find "$WORKSPACE" -maxdepth 3 -name '*.md' -not -path '*/.git/*' -not -path '*/.control/*' 2>/dev/null | wc -l | tr -d ' ')" -ge 5 ]; }; then
    # Non-code repo: a knowledge vault / document repo (markdown-dominant, no code
    # build manifest). The dogfood predicate is content integrity, not a test suite.
    DETECTED_STACK="knowledge-vault"
    DETECTION_REASON="markdown/document repo (no code build manifest; Pattern H)"
fi

if [ "$DETECTED_STACK" = "unknown" ]; then
    [ "$QUIET" = "0" ] && echo "  [info] stack: unknown (no detection signal matched; agent declares stack in Dogfood Plan)"
else
    ok "stack detected: $DETECTED_STACK ($DETECTION_REASON)"
fi

# Verify a Dogfood Plan anchor exists at one of three accepted locations.
DOGFOOD_PLAN_FOUND=0
DOGFOOD_LOCATION=""
if [ -f "$AGENTS" ] && grep -qiE '^## Dogfood Plan' "$AGENTS"; then
    DOGFOOD_PLAN_FOUND=1
    DOGFOOD_LOCATION="AGENTS.md"
elif [ -f "$WORKSPACE/docs/dogfood-plan.md" ]; then
    DOGFOOD_PLAN_FOUND=1
    DOGFOOD_LOCATION="docs/dogfood-plan.md"
fi

if [ "$DOGFOOD_PLAN_FOUND" = "1" ]; then
    ok "Dogfood Plan section present at $DOGFOOD_LOCATION"
else
    [ "$QUIET" = "0" ] && echo "  [info] no Dogfood Plan anchor in AGENTS.md or docs/dogfood-plan.md"
    [ "$QUIET" = "0" ] && echo "         → for substantive feature PRs, include plan in PR body OR run \`bstack onboard\` to stub one"
    [ "$QUIET" = "0" ] && echo "         → reference: bstack/references/dogfood-patterns.md for the per-stack cookbook (informational; not blocking until rule-of-three)"
fi

# ── Section 14: RCS stability budget (compute lambda) ───────────────────────
# Recompute lambda_i from the workspace's .control/rcs-parameters.toml (or
# fallback to research/rcs/data/parameters.toml or bstack's template). Report
# per-level lambda + drift vs cached values + composite omega. A level with
# lambda <= 0 is a HARD gap (counts against doctor's gap total). Drift > 1e-4
# is a soft warning unless --strict is set.
section "14. RCS stability budget"

COMPUTE_LAMBDA="$BSTACK_REPO/scripts/compute-lambda.sh"
if [ ! -x "$COMPUTE_LAMBDA" ] && [ -f "$COMPUTE_LAMBDA" ]; then
    chmod +x "$COMPUTE_LAMBDA" 2>/dev/null || true
fi

if [ ! -f "$COMPUTE_LAMBDA" ]; then
    [ "$QUIET" = "0" ] && echo "  [info] scripts/compute-lambda.sh missing — RCS check skipped"
elif ! command -v python3 >/dev/null 2>&1; then
    [ "$QUIET" = "0" ] && echo "  [info] python3 missing — RCS lambda computation skipped"
else
    LAMBDA_OUT=$(BROOMVA_WORKSPACE="$WORKSPACE" bash "$COMPUTE_LAMBDA" --json 2>/dev/null)
    LAMBDA_EXIT=$?
    if [ "$LAMBDA_EXIT" = "2" ]; then
        [ "$QUIET" = "0" ] && echo "  [info] no parameters.toml in workspace — install with: bash $BSTACK_REPO/scripts/install-l3-stability.sh"
    elif [ "$LAMBDA_EXIT" = "0" ]; then
        # All stable — report compactly
        omega=$(echo "$LAMBDA_OUT" | python3 -c "import json,sys;print(json.load(sys.stdin)['composite_omega'])" 2>/dev/null)
        ok "RCS composite stability ω = ${omega:-?} (min λᵢ; all levels stable)"
        # Drift check (informational unless --strict)
        if [ "$STRICT" = "1" ]; then
            DRIFT_OUT=$(BROOMVA_WORKSPACE="$WORKSPACE" bash "$COMPUTE_LAMBDA" --json --strict 2>&1 >/dev/null)
            DRIFT_EXIT=$?
            if [ "$DRIFT_EXIT" = "3" ]; then
                gap "RCS lambda drift > 1e-4 (--strict)" \
                    "recompute and commit cached values: bash $COMPUTE_LAMBDA --human"
            fi
        fi
    else
        # Unstable — hard gap
        gap "RCS lambda <= 0 at some level (composite system unstable)" \
            "review .control/rcs-parameters.toml; run: bash $COMPUTE_LAMBDA --human"
    fi
fi

# ── Section 15: L3 stability gate-flow wiring ───────────────────────────────
# Verify the four-gate flow (G0 Claude Code, G1 git pre-commit, G2 GH Actions,
# G3 doctor §14 which we just ran) is wired into this workspace. Each missing
# gate is an INFORMATIONAL gap (not blocking) — the install command is
# surfaced as the fix.
section "15. L3 stability gate-flow wiring"

INSTALL_CMD="bash $BSTACK_REPO/scripts/install-l3-stability.sh"

# G0 — Claude Code PreToolUse hook in .claude/settings.json
SETTINGS_FILE="$WORKSPACE/.claude/settings.json"
if [ -f "$SETTINGS_FILE" ] && grep -q '"_bstack_primitive": *"L3-G0"' "$SETTINGS_FILE"; then
    ok "G0 Claude Code PreToolUse hook wired (.claude/settings.json)"
else
    [ "$QUIET" = "0" ] && echo "  [info] G0 Claude Code PreToolUse hook missing (.claude/settings.json)"
    [ "$QUIET" = "0" ] && echo "         → fix: $INSTALL_CMD"
fi

# G1 — git pre-commit hook
PRE_COMMIT_HOOK="$WORKSPACE/.githooks/pre-commit"
if [ -f "$PRE_COMMIT_HOOK" ] && grep -q "L3 rate gate" "$PRE_COMMIT_HOOK"; then
    ok "G1 git pre-commit hook wired (.githooks/pre-commit)"
else
    [ "$QUIET" = "0" ] && echo "  [info] G1 git pre-commit hook missing (.githooks/pre-commit)"
    [ "$QUIET" = "0" ] && echo "         → fix: $INSTALL_CMD"
fi

# G2 — GitHub Actions workflow
GH_WORKFLOW="$WORKSPACE/.github/workflows/l3-stability.yml"
if [ -f "$GH_WORKFLOW" ]; then
    ok "G2 GitHub Actions workflow wired (.github/workflows/l3-stability.yml)"
else
    [ "$QUIET" = "0" ] && echo "  [info] G2 GitHub Actions workflow missing (.github/workflows/l3-stability.yml)"
    [ "$QUIET" = "0" ] && echo "         → fix: $INSTALL_CMD"
fi

# G3 — workspace parameters.toml (the data the other gates read)
PARAMS_FILE="$WORKSPACE/.control/rcs-parameters.toml"
PAPER_PARAMS="$WORKSPACE/research/rcs/data/parameters.toml"
if [ -f "$PARAMS_FILE" ]; then
    ok "RCS parameters config present (.control/rcs-parameters.toml)"
elif [ -f "$PAPER_PARAMS" ]; then
    ok "RCS parameters config present (research/rcs/data/parameters.toml — paper canonical)"
else
    [ "$QUIET" = "0" ] && echo "  [info] no .control/rcs-parameters.toml in workspace"
    [ "$QUIET" = "0" ] && echo "         → fix: $INSTALL_CMD"
fi

# ── Section 16: L0 plant audit (tool calls) ─────────────────────────────────
# SUPERSEDED (v0.31.0; sensor since v0.30.0): the L0 plant metrics now come from
# the transcript-derived leverage-sensor (§23), not the retired PostToolUse
# l0-tools.jsonl (which logged latency_ms — a field Claude Code never emits →
# 100% null). Surface m2/m3/m4 from .control/leverage-state.json. Informational.
section "16. L0 plant audit (tool calls)"

LEV_STATE="$WORKSPACE/.control/leverage-state.json"
INSTALL_RCS="bash $BSTACK_REPO/scripts/install-rcs-stability.sh"

if [ ! -f "$LEV_STATE" ]; then
    [ "$QUIET" = "0" ] && echo "  [info] no leverage-state yet — L0 metrics appear after the Stop sensor runs (see §23)"
    [ "$QUIET" = "0" ] && echo "         → fix: $INSTALL_RCS"
elif ! command -v python3 >/dev/null 2>&1; then
    [ "$QUIET" = "0" ] && echo "  [info] python3 missing — L0 summary skipped"
else
    L0_SUMMARY=$(python3 - "$LEV_STATE" <<'PYEOF'
import sys, json
try:
    m = json.load(open(sys.argv[1])).get("metrics", {})
    print("tool_error_rate=%s read_before_edit_rate=%s permission_bypass/session=%s" % (
        m.get("m2_tool_error_rate"), m.get("m3_read_before_edit_rate"),
        m.get("m4_permission_bypass_per_session")))
except Exception:
    print("unreadable")
PYEOF
)
    ok "L0 (leverage-sensor §23): $L0_SUMMARY"
fi

# ── Section 17: L1 autonomic reflex health ──────────────────────────────────
# SUPERSEDED (v0.31.0; sensor since v0.30.0): the retired Stop hook inferred reflex
# compliance by grepping the agent's own prose (h correlated with U). The
# leverage-sensor's L1 signal — m1 continue_nudges_per_session — is transcript-
# structural instead. Informational.
section "17. L1 autonomic reflex health"

if [ ! -f "$LEV_STATE" ]; then
    [ "$QUIET" = "0" ] && echo "  [info] no leverage-state yet — L1 signal appears after the Stop sensor runs (see §23)"
    [ "$QUIET" = "0" ] && echo "         → fix: $INSTALL_RCS"
elif ! command -v python3 >/dev/null 2>&1; then
    [ "$QUIET" = "0" ] && echo "  [info] python3 missing — L1 summary skipped"
else
    L1_SUMMARY=$(python3 - "$LEV_STATE" <<'PYEOF'
import sys, json
try:
    d = json.load(open(sys.argv[1]))
    m = d.get("metrics", {})
    print("continue_nudges/session=%s over %s sessions/%sd" % (
        m.get("m1_continue_nudges_per_session"),
        d.get("sessions_analyzed"), d.get("window_days")))
except Exception:
    print("unreadable")
PYEOF
)
    ok "L1 (leverage-sensor §23): $L1_SUMMARY"
fi

# ── Section 18: L2 EGRI promotion throttle ─────────────────────────────────
# Reads .control/audit/l2-promotions.jsonl. Counts promotions in last τ_a_2
# window (default 1h); reports vs budget. Hard gap if over budget.
section "18. L2 EGRI promotion throttle"

L2_LOG="$WORKSPACE/.control/audit/l2-promotions.jsonl"

if [ ! -f "$L2_LOG" ]; then
    [ "$QUIET" = "0" ] && echo "  [info] no L2 audit log (bookkeeping not yet wired to l2-promotion-audit-hook)"
    [ "$QUIET" = "0" ] && echo "         → fix: $INSTALL_RCS (audit dir created) + bookkeeping.py promote step calls scripts/l2-promotion-audit-hook.sh"
elif ! command -v python3 >/dev/null 2>&1; then
    [ "$QUIET" = "0" ] && echo "  [info] python3 missing — L2 audit summary skipped"
else
    # Read tau_a_2 from parameters.toml
    PARAMS_FOR_TAU=""
    if [ -f "$WORKSPACE/.control/rcs-parameters.toml" ]; then
        PARAMS_FOR_TAU="$WORKSPACE/.control/rcs-parameters.toml"
    elif [ -f "$WORKSPACE/research/rcs/data/parameters.toml" ]; then
        PARAMS_FOR_TAU="$WORKSPACE/research/rcs/data/parameters.toml"
    fi
    L2_SUMMARY=$(python3 - "$L2_LOG" "$PARAMS_FOR_TAU" <<'PYEOF'
import sys, json, time
log = sys.argv[1]
params_path = sys.argv[2]

tau_a = 3600.0
budget = 5
if params_path:
    try:
        import tomllib
        with open(params_path, "rb") as f:
            data = tomllib.load(f)
        for lvl in data.get("levels", []):
            if lvl.get("id") == "L2":
                tau_a = float(lvl.get("tau_a", tau_a))
                break
    except Exception:
        pass

now_ms = int(time.time() * 1000)
cutoff_ms = now_ms - int(tau_a * 1000)
rows = []
try:
    with open(log) as f:
        for line in f:
            try:
                r = json.loads(line)
                if r.get("ts", 0) >= cutoff_ms:
                    rows.append(r)
                    if isinstance(r.get("budget"), (int, float)):
                        budget = int(r["budget"])
            except Exception:
                pass
except Exception:
    pass

count = len(rows)
status = "OK" if count <= budget else "OVER_BUDGET"
print(f"promotions={count}/{budget} window={int(tau_a)}s status={status}")
PYEOF
)
    if echo "$L2_SUMMARY" | grep -q "OVER_BUDGET"; then
        gap "L2 promotion throttle: $L2_SUMMARY" \
            "defer promotions until window resets; or raise budget in policy.yaml after rule-of-three"
    else
        ok "L2 audit: $L2_SUMMARY"
    fi
fi

# ── Section 19: Multi-layer composite health (the unifier) ─────────────────
# Calls compute-budget-status.sh; reports composite verdict + per-layer
# verdicts. Hard gap only if any layer is "unstable"; warn on "stable_warn".
section "19. Multi-layer composite health"

BUDGET_STATUS="$BSTACK_REPO/scripts/compute-budget-status.sh"
if [ ! -f "$BUDGET_STATUS" ]; then
    [ "$QUIET" = "0" ] && echo "  [info] scripts/compute-budget-status.sh missing"
elif ! command -v python3 >/dev/null 2>&1; then
    [ "$QUIET" = "0" ] && echo "  [info] python3 missing — composite health skipped"
else
    STATUS_JSON=$(BROOMVA_WORKSPACE="$WORKSPACE" bash "$BUDGET_STATUS" --json 2>/dev/null)
    STATUS_EXIT=$?
    if [ -z "$STATUS_JSON" ]; then
        [ "$QUIET" = "0" ] && echo "  [info] compute-budget-status produced no output"
    else
        ALL_STABLE=$(echo "$STATUS_JSON" | python3 -c "import sys,json;d=json.load(sys.stdin);print(d.get('all_layers_stable'))" 2>/dev/null)
        VERDICTS=$(echo "$STATUS_JSON" | python3 -c "import sys,json;d=json.load(sys.stdin);print(' '.join(l['id']+'='+l['verdict'] for l in d.get('layers',[])))" 2>/dev/null)
        WARN_COUNT=$(echo "$STATUS_JSON" | python3 -c "import sys,json;d=json.load(sys.stdin);print(len(d.get('warnings',[])))" 2>/dev/null)
        if [ "$STATUS_EXIT" = "0" ] && [ "$ALL_STABLE" = "True" ]; then
            ok "composite health: $VERDICTS (all stable; ${WARN_COUNT:-0} warnings)"
        else
            gap "composite health: $VERDICTS (warnings=$WARN_COUNT, exit=$STATUS_EXIT)" \
                "review: bash $BUDGET_STATUS --human"
        fi
    fi
fi

# ── Section 20: Workspace federation registry (Phase 8, v0.18.0) ───────────
# Reports whether the host-level federation registry at
# ~/.broomva/global/registry.yaml exists, how many workspaces are registered,
# and (if any) flags entries whose last_seen_at is > 30 days old.
#
# Federation is OPT-IN — a missing registry is INFORMATIONAL, never a gap.
# The only hard gap is schema_version drift (registry file exists but the
# header says schema_version != 1) — that's a corrupt/mismatched registry
# the user installed by hand or with a future bstack version.
section "20. Workspace federation registry"

REGISTRY_DEFAULT="$HOME/.broomva/global/registry.yaml"
REGISTRY_PATH="${BSTACK_REGISTRY:-$REGISTRY_DEFAULT}"
WORKSPACE_BIN="$BSTACK_REPO/bin/bstack-workspace"

if [ ! -x "$WORKSPACE_BIN" ]; then
    [ "$QUIET" = "0" ] && echo "  [info] bin/bstack-workspace not present (federation not installed)"
elif [ ! -f "$REGISTRY_PATH" ]; then
    [ "$QUIET" = "0" ] && echo "  [info] no registry present (federation not in use)"
    [ "$QUIET" = "0" ] && echo "         → fix: bash $WORKSPACE_BIN register"
elif ! command -v python3 >/dev/null 2>&1; then
    [ "$QUIET" = "0" ] && echo "  [info] python3 missing — federation registry summary skipped"
else
    FED_SUMMARY=$(BSTACK_REGISTRY="$REGISTRY_PATH" BSTACK_DIR="$BSTACK_REPO" \
        bash "$WORKSPACE_BIN" list --json 2>/dev/null)
    FED_EXIT=$?
    # Exit 3 = schema mismatch / parse error (per bstack-workspace contract).
    if [ "$FED_EXIT" = "3" ]; then
        gap "workspace registry schema_version mismatch or parse error ($REGISTRY_PATH)" \
            "inspect: cat $REGISTRY_PATH; fix header to 'schema_version: 1' or re-create with 'bstack workspace register'"
    elif [ -z "$FED_SUMMARY" ]; then
        [ "$QUIET" = "0" ] && echo "  [info] registry present but list --json produced no output"
    else
        SCHEMA_OK=$(echo "$FED_SUMMARY" | python3 -c "import sys,json;d=json.load(sys.stdin);print('1' if d.get('count') is not None else '0')" 2>/dev/null)
        if [ "$SCHEMA_OK" != "1" ]; then
            gap "workspace registry malformed JSON output ($REGISTRY_PATH)" \
                "inspect: bash $WORKSPACE_BIN list --json"
        else
            FED_COUNT=$(echo "$FED_SUMMARY" | python3 -c "import sys,json;print(json.load(sys.stdin).get('count', 0))" 2>/dev/null)
            # SC2259-safe: pass payload via env var instead of pipe + heredoc.
            FED_STALE=$(BSTACK_FED_SUMMARY="$FED_SUMMARY" python3 - "$REGISTRY_PATH" <<'PYEOF' 2>/dev/null
import json, os, sys
from datetime import datetime, timedelta, timezone
data = json.loads(os.environ.get("BSTACK_FED_SUMMARY", "{}"))
cutoff = datetime.now(timezone.utc) - timedelta(days=30)
stale = []
for ws in data.get("workspaces", []):
    raw = ws.get("last_seen_at") or ws.get("registered_at")
    if not raw:
        continue
    try:
        # Accept 'Z' suffix as UTC.
        ts = datetime.strptime(raw, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except Exception:
        continue
    if ts < cutoff:
        stale.append(ws.get("name", "?"))
print(",".join(stale))
PYEOF
)
            if [ -n "$FED_STALE" ]; then
                [ "$QUIET" = "0" ] && echo "  [info] federation: $FED_COUNT workspace(s); stale (>30d): $FED_STALE"
                [ "$QUIET" = "0" ] && echo "         → fix: refresh with 'bstack workspace register' from each stale workspace"
            fi
            ok "workspace registry present: $FED_COUNT workspace(s) at $REGISTRY_PATH"
        fi
    fi
fi

# ── Section 21: Closure-Contract arcs (v0.19.0+) ───────────────────────────
# Reads workspace .control/arcs.yaml (if present) and reports:
#   - Count of declared arcs
#   - Count of arcs with all 5 components present (id, plant_surfaces, sensor,
#     actuator, termination, tau_a)
#   - Last termination event timestamp per arc (informational only)
# Hard gap only if .control/arcs.yaml is present AND schema_version != 1.
# Missing arcs.yaml is informational (workspaces opt in by writing the file).
section "21. Closure-Contract arcs (.control/arcs.yaml)"

ARCS_FILE="$WORKSPACE/.control/arcs.yaml"
COMPUTE_ARC_STATUS="$BSTACK_REPO/scripts/compute-arc-status.sh"

if [ ! -f "$ARCS_FILE" ]; then
    [ "$QUIET" = "0" ] && echo "  [info] no .control/arcs.yaml (workspace has not declared closure arcs)"
    [ "$QUIET" = "0" ] && echo "         → see bstack/assets/templates/arcs.yaml.template for the 5-tuple shape"
elif ! command -v python3 >/dev/null 2>&1; then
    [ "$QUIET" = "0" ] && echo "  [info] python3 missing — arcs validation skipped"
else
    ARC_SUMMARY=$(python3 - "$ARCS_FILE" <<'PYEOF' 2>/dev/null
import sys, json
from pathlib import Path

path = Path(sys.argv[1])
text = path.read_text()
try:
    import yaml
    data = yaml.safe_load(text) or {}
except ImportError:
    # Defer to the minimal parser inside compute-arc-status; here we only need
    # schema_version + arc count, which fit a simpler shape.
    data = {}
    lines = text.splitlines()
    arcs = []
    in_arcs = False
    current = None
    for line in lines:
        s = line.lstrip()
        if s.startswith("#") or not s:
            continue
        if line.startswith("schema_version:"):
            try:
                data["schema_version"] = int(line.split(":", 1)[1].strip())
            except Exception:
                pass
        elif line.startswith("arcs:"):
            in_arcs = True
        elif in_arcs and s.startswith("- "):
            if current:
                arcs.append(current)
            current = {"id": "?"}
            rest = s[2:]
            if rest.startswith("id:"):
                current["id"] = rest.split(":", 1)[1].strip()
        elif in_arcs and current and s.startswith("id:"):
            current["id"] = s.split(":", 1)[1].strip()
    if current:
        arcs.append(current)
    data["arcs"] = arcs

sv = data.get("schema_version")
arcs = data.get("arcs") or []

# Count arcs with all 5 components present.
def complete(a):
    if not isinstance(a, dict):
        return False
    have_id = bool(a.get("id"))
    have_ps = bool(a.get("plant_surfaces"))
    have_sensor = isinstance(a.get("sensor"), dict) and bool(a["sensor"].get("kind"))
    have_act = isinstance(a.get("actuator"), dict) and bool(a["actuator"].get("kind"))
    have_term = isinstance(a.get("termination"), dict) and bool(a["termination"].get("kind"))
    have_tau = isinstance(a.get("tau_a"), (int, float))
    return have_id and have_ps and have_sensor and have_act and have_term and have_tau

complete_count = sum(1 for a in arcs if complete(a))
print(json.dumps({"schema_version": sv, "arc_count": len(arcs), "complete_count": complete_count, "ids": [a.get("id") for a in arcs if isinstance(a, dict)]}))
PYEOF
)
    if [ -z "$ARC_SUMMARY" ]; then
        gap "arcs.yaml parse failed (workspace .control/arcs.yaml malformed?)" \
            "validate against schemas/arcs.v1.json"
    else
        SV=$(echo "$ARC_SUMMARY" | python3 -c "import sys,json;print(json.load(sys.stdin).get('schema_version'))" 2>/dev/null)
        AC=$(echo "$ARC_SUMMARY" | python3 -c "import sys,json;print(json.load(sys.stdin).get('arc_count'))" 2>/dev/null)
        CC=$(echo "$ARC_SUMMARY" | python3 -c "import sys,json;print(json.load(sys.stdin).get('complete_count'))" 2>/dev/null)
        if [ "$SV" != "1" ]; then
            gap "arcs.yaml schema_version = $SV (expected 1)" \
                "bump arcs.yaml schema_version to 1 or migrate to current shape"
        else
            ok "arcs.yaml: schema_version=1, arcs=$AC, complete=$CC"
            # Surface most-recent termination event per arc (informational).
            if [ -n "$AC" ] && [ "$AC" -gt 0 ]; then
                IDS=$(echo "$ARC_SUMMARY" | python3 -c "import sys,json;print(' '.join((d:=json.load(sys.stdin)).get('ids') or []))" 2>/dev/null)
                for aid in $IDS; do
                    LOG="$WORKSPACE/.control/audit/arc-$aid.jsonl"
                    if [ -f "$LOG" ]; then
                        LAST_TS=$(tail -1 "$LOG" 2>/dev/null | python3 -c "import sys,json;
try:
    print(json.loads(sys.stdin.read()).get('ts','-'))
except Exception:
    print('-')
" 2>/dev/null)
                        [ "$QUIET" = "0" ] && echo "         arc $aid: last termination ts=$LAST_TS"
                    else
                        [ "$QUIET" = "0" ] && echo "         arc $aid: no termination events yet (.control/audit/arc-$aid.jsonl absent)"
                    fi
                done
            fi
        fi
    fi
fi
# ── Section 22: Composite-ω drift trend (v0.19.0+) ─────────────────────────
# Reads .control/audit/composite-omega-history.jsonl (written by
# `compute-budget-status.sh --trend`). Reports last value, baseline, slope,
# verdict. Hard gap only when verdict == drift_down (composite ω is shrinking
# — the system is losing stability over time).
section "22. Composite-ω drift trend"

OMEGA_HISTORY="$WORKSPACE/.control/audit/composite-omega-history.jsonl"
BUDGET_TREND="$BSTACK_REPO/scripts/compute-budget-status.sh"

if [ ! -f "$OMEGA_HISTORY" ]; then
    [ "$QUIET" = "0" ] && echo "  [info] no composite-omega-history.jsonl (run \`bash scripts/compute-budget-status.sh --trend\` periodically to populate)"
elif [ ! -f "$BUDGET_TREND" ]; then
    [ "$QUIET" = "0" ] && echo "  [info] scripts/compute-budget-status.sh missing — trend skipped"
elif ! command -v python3 >/dev/null 2>&1; then
    [ "$QUIET" = "0" ] && echo "  [info] python3 missing — trend skipped"
else
    TREND_JSON=$(BROOMVA_WORKSPACE="$WORKSPACE" bash "$BUDGET_TREND" --json --trend 2>/dev/null)
    if [ -z "$TREND_JSON" ]; then
        [ "$QUIET" = "0" ] && echo "  [info] --trend produced no output"
    else
        TREND_VERDICT=$(echo "$TREND_JSON" | python3 -c "import sys,json;d=json.load(sys.stdin).get('trend',{});print(d.get('verdict','-'))" 2>/dev/null)
        TREND_LAST=$(echo "$TREND_JSON" | python3 -c "import sys,json;d=json.load(sys.stdin).get('trend',{});print(d.get('last','-'))" 2>/dev/null)
        TREND_BASELINE=$(echo "$TREND_JSON" | python3 -c "import sys,json;d=json.load(sys.stdin).get('trend',{});print(d.get('baseline','-'))" 2>/dev/null)
        TREND_SLOPE=$(echo "$TREND_JSON" | python3 -c "import sys,json;d=json.load(sys.stdin).get('trend',{});print(d.get('slope_per_second','-'))" 2>/dev/null)
        TREND_POINTS=$(echo "$TREND_JSON" | python3 -c "import sys,json;d=json.load(sys.stdin).get('trend',{});print(d.get('points','-'))" 2>/dev/null)
        if [ "$TREND_VERDICT" = "drift_down" ]; then
            gap "composite-ω drift_down: last=$TREND_LAST baseline=$TREND_BASELINE slope=$TREND_SLOPE (n=$TREND_POINTS)" \
                "review .control/rcs-parameters.toml; investigate which layer's λ is shrinking"
        else
            ok "composite-ω trend: last=$TREND_LAST baseline=$TREND_BASELINE slope=$TREND_SLOPE verdict=$TREND_VERDICT (n=$TREND_POINTS)"
        fi
    fi
fi

# ── Section 23: Control-loop closure verdict (the "is it wired + running?") ─
# The single verdict answering: is the RCS control loop wired, connected, and
# running on this workspace? Distinct from §19 (the budget/stability lens, which
# already hard-gates a wired-but-diverging loop). §23 composes three signals:
#   W (wired)   — .claude/settings.json OR settings.local.json carries the
#                 L0-audit + L1-audit hook markers AND .control/audit/ exists.
#   R (running) — an L0/L1 audit log exists, is non-empty, and was written in
#                 the last 7 days (multi-session cadence).
#   C (closing) — closure arcs are declared/resolvable AND composite-ω is
#                 computable (compute-budget-status.sh present).
# Three states: (a) substrate absent (!W) → info; (b) wired-but-idle (W && !R)
# → SOFT by default, hard gap only under BSTACK_LOOP_STRICT=1 (audit logs are
# empty until the first hook fires, so a hard default would redden every fresh
# bootstrap for purely temporal reasons); (c) W && R → ok.
section "23. Control-loop closure verdict"

# CONTENT-AWARE closure verdict. The prior §23 checked only that an audit log
# EXISTED and was recent — so a 100%-null / 100%-zero fake sensor passed as
# "wired + running + closing." This reads the sensor's OWN per-RCS-level verdict
# in .control/leverage-state.json (written by leverage-sensor.py) and FAILS on a
# dead sensor or a level with no live signal. The verifier is now causally
# independent of what it grades — it inspects row content, not file existence.
LOOP_STRICT="${BSTACK_LOOP_STRICT:-0}"
STATE_FILE="$WORKSPACE/.control/leverage-state.json"

# W — wired: settings carries the real sensor marker (loop-sensor @ Stop). Legacy
# L0-audit / L1-audit markers = the FAKE sensors → wired-but-fake (a migration gap).
W_OK=0; W_LEGACY=0
for _s in "$WORKSPACE/.claude/settings.json" "$WORKSPACE/.claude/settings.local.json"; do
    [ -f "$_s" ] || continue
    grep -q '"loop-sensor"' "$_s" 2>/dev/null && W_OK=1
    { grep -q '"L0-audit"' "$_s" 2>/dev/null || grep -q '"L1-audit"' "$_s" 2>/dev/null; } && W_LEGACY=1
done

if [ "$W_OK" = "0" ] && [ "$W_LEGACY" = "1" ]; then
    gap "control loop wired to the FAKE l0/l1 sensors (latency_ms 100% null; tool_call_count 100% zero)" \
        "migrate: bash $BSTACK_REPO/scripts/install-rcs-stability.sh  (wires the real leverage-sensor)"
elif [ "$W_OK" = "0" ]; then
    [ "$QUIET" = "0" ] && echo "  [info] control loop NOT wired (no loop-sensor Stop hook)"
    [ "$QUIET" = "0" ] && echo "         → fix: bash $BSTACK_REPO/scripts/install-rcs-stability.sh  (or re-run \`bstack bootstrap\` without BSTACK_SKIP_RCS=1)"
elif [ ! -f "$STATE_FILE" ]; then
    # wired but idle — the sensor has not run yet (normal on a fresh workspace)
    if [ "$LOOP_STRICT" = "1" ]; then
        gap "control loop WIRED but IDLE (no .control/leverage-state.json yet)" \
            "exercise a session so the Stop sensor fires; or unset BSTACK_LOOP_STRICT"
    else
        [ "$QUIET" = "0" ] && echo "  [info] control loop WIRED but IDLE (sensor has not run yet; normal on a fresh bootstrap)"
        [ "$QUIET" = "0" ] && echo "         → CI lanes: run with BSTACK_LOOP_STRICT=1 to fail on idle"
    fi
else
    # W_OK + state present — read the sensor's OWN closure verdict (content-aware)
    _fresh=1; [ -z "$(find "$STATE_FILE" -mtime -7 2>/dev/null)" ] && _fresh=0
    _verdict=$(python3 -c "
import json
c=json.load(open('$STATE_FILE')).get('closure',{})
openlv=','.join(k for k,v in c.get('levels',{}).items() if not v.get('live')) or '-'
print('%d %d %d %s' % (int(bool(c.get('sensor_live'))), int(bool(c.get('levels_closed'))), int(bool(c.get('reference_authored'))), openlv))
" 2>/dev/null)
    read -r _sensor_live _levels_closed _ref_authored _open_lv <<EOF_V
$_verdict
EOF_V

    if [ -z "$_verdict" ]; then
        gap "control loop state unreadable ($STATE_FILE has no closure block)" \
            "re-run the sensor: python3 $BSTACK_REPO/scripts/leverage-sensor.py"
    elif [ "$_sensor_live" != "1" ]; then
        # THE un-blinding: a fake/dead sensor (all metrics null/zero) now FAILS
        gap "control loop sensor is DEAD — all metrics null/zero over 0 sessions (a fake sensor certifying itself as running)" \
            "verify leverage-sensor.py resolves the transcript path; this is exactly the failure §23 used to pass as 'closing'"
    elif [ "$_levels_closed" != "1" ]; then
        gap "control loop OPEN at RCS level(s): $_open_lv (no live metric there)" \
            "add/verify a metric at each level in .control/leverage-setpoints.yaml"
    elif [ "$_fresh" = "0" ] && [ "$LOOP_STRICT" = "1" ]; then
        gap "control loop closed but STALE (leverage-state.json > 7d old)" \
            "exercise a session so the Stop sensor refreshes"
    elif [ "$_ref_authored" = "1" ]; then
        ok "control loop: CLOSED across L0–L3 (sensor live, every level live, reference r0 authored)"
    else
        ok "control loop: closed across L0–L3 (sensor live, every level live)"
        [ "$QUIET" = "0" ] && echo "         ⚠ reference r0 is bstack-default (endogenous r=g(x)) — author + sign .control/leverage-setpoints.yaml (causal priority)"
    fi
fi

# ── 24. Loop-stall rejection hooks (BRO-1700) ──────────────────────────────
section "24. Loop-stall rejection hooks (P19)"
SETTINGS="$WORKSPACE/.claude/settings.json"
LOOPSTALL_HOOKS=(
    "autonomous-posture-hook.sh"
    "arc-continuation-hook.sh"
)
LOOPSTALL_LABELS=(
    "UserPromptSubmit — sticky posture + /autonomous self-bootstrap (disturbance #5)"
    "Stop — no-op-terminal → block, the m1 machine-checkable core (disturbances #3/#4)"
)
if [ -f "$SETTINGS" ]; then
    for i in "${!LOOPSTALL_HOOKS[@]}"; do
        hk="${LOOPSTALL_HOOKS[$i]}"
        label="${LOOPSTALL_LABELS[$i]}"
        if grep -q "$hk" "$SETTINGS"; then
            ok "$hk wired — $label"
        else
            gap "$hk not wired in .claude/settings.json — $label" \
                "run 'bstack repair' or 'bstack bootstrap' to wire from assets/templates/settings.json.snippet"
        fi
    done
else
    [ "$QUIET" = "0" ] && echo "         (no .claude/settings.json — skipping loop-stall wiring check)"
fi
# arc-file health — informational, never a gap
ARC_HOME="${BROOMVA_AUTONOMOUS_HOME:-$HOME/.config/broomva/autonomous}"
if [ -d "$ARC_HOME" ] && [ "$QUIET" = "0" ]; then
    _active=$(grep -l '"active": true' "$ARC_HOME"/*.arc 2>/dev/null | wc -l | tr -d ' ')
    echo "         arc state: ${_active:-0} active arc(s)"
fi

# ── Section 25: Wired hook command resolution (BRO-1715) ───────────────────
# §24 (and every wiring check) greps that a hook is WIRED by name — it never
# checks the command PATH resolves. That let 5 bstack hooks dangle at a
# ${BROOMVA_HOME}/.claude/skills/bstack/scripts/ dir no install step creates
# (bstack installs by git-clone, never as a global skill), silently dead while
# doctor stayed green. This resolves EVERY wired command: a bstack-shipped one
# that dangles is a HARD gap; a not-yet-installed companion skill
# (~/.{claude,agents}/skills/<name>, name != bstack) is a SOFT info, not a gap
# (fresh-aware, mirrors §23's soft-vs-hard nuance — a workspace can be audited
# before `npx skills add` has run).
section "25. Wired hook command resolution"
_HOOK_SETTINGS=()
for _s in "$WORKSPACE/.claude/settings.json" "$WORKSPACE/.claude/settings.local.json"; do
    [ -f "$_s" ] && _HOOK_SETTINGS+=("$_s")
done
if [ ${#_HOOK_SETTINGS[@]} -eq 0 ]; then
    [ "$QUIET" = "0" ] && echo "  [info] no .claude/settings.json — skipping hook-resolution check"
elif ! command -v python3 >/dev/null 2>&1; then
    [ "$QUIET" = "0" ] && echo "  [info] python3 unavailable — skipping hook-resolution check"
else
    _HOOK_REPORT=$(python3 - "$HOME" "${_HOOK_SETTINGS[@]}" <<'PYEOF'
import json, os, shlex, sys
home = sys.argv[1]
roots = (os.path.join(home, ".claude", "skills") + os.sep,
         os.path.join(home, ".agents", "skills") + os.sep)

def script_path(cmd):
    # The absolute script a hook runs — skipping a leading interpreter
    # (python3/bash/node), flags (-x), and env-assignments (VAR=val). Without
    # this, 'python3 /a/b.py' would check isfile("python3") → false-dangle.
    #
    # Deliberately CONSERVATIVE: only a single absolute-path command is verified.
    # We bias to false-NEGATIVE over false-positive — a spurious HARD gap would
    # break 'doctor --strict' on a hook that actually resolves, which is worse
    # than silently not-verifying an exotic form. So: shlex-parse (handles
    # quotes/spaces), bail on shell composites we can't statically resolve
    # (&&/||/;/pipes, 'sh -c <code>'), and return only an absolute path.
    # NB: no literal backticks anywhere in this heredoc body — it lives inside
    # $( ... ) and bash 3.2's pre-parser dies on them even though the delimiter
    # is quoted (BRO-1718). The operator tuple uses chr(96) for the same reason.
    try:
        toks = shlex.split(cmd)
    except ValueError:
        return None   # unbalanced quotes etc — don't guess
    if any(t in ("&&", "||", ";", "|", "&", ">", ">>", "<", chr(96)) for t in toks):
        return None
    for i, t in enumerate(toks):
        if t.startswith("-"):
            continue
        if "=" in t and not t.startswith("/"):
            continue   # env assignment VAR=val
        if t in ("sh", "bash", "zsh", "dash") and i + 1 < len(toks) and toks[i + 1] == "-c":
            return None   # sh -c '<code>' — the next token is code, not a path
        if t.startswith("/"):
            return t
        # otherwise a bare command/interpreter (python3, make, node) — keep scanning
    return None   # no absolute script path to verify

dangling, softmiss, seen = [], [], set()
for path_file in sys.argv[2:]:
    try:
        data = json.load(open(path_file))
    except Exception:
        continue
    for event, blocks in (data.get("hooks") or {}).items():
        for b in blocks or []:
            for h in b.get("hooks", []):
                cmd = h.get("command", "")
                p = script_path(cmd) if cmd else None
                if not p or p in seen:
                    continue
                seen.add(p)
                if os.path.isfile(p):
                    continue
                companion = None
                for r in roots:
                    if p.startswith(r):
                        name = p[len(r):].split(os.sep)[0]
                        if name != "bstack":   # bstack is NOT a companion skill — no hiding here
                            companion = name
                if companion:
                    softmiss.append((companion, p))
                else:
                    dangling.append(p)
for c, p in softmiss:
    print("SOFT\t%s\t%s" % (c, p))
for p in dangling:
    print("HARD\t-\t%s" % p)   # "-" name placeholder: an empty field collapses under IFS=$'\t'
PYEOF
)
    if [ -z "$_HOOK_REPORT" ]; then
        ok "every wired hook command resolves to an existing file"
    else
        while IFS=$'\t' read -r _kind _name _p; do
            [ -z "${_kind:-}" ] && continue
            if [ "$_kind" = "HARD" ]; then
                gap "wired hook command dangles: $_p" \
                    "re-run 'bstack repair' (re-roots bstack hooks at \$BSTACK_REPO); if custom, fix its path in .claude/settings.json"
            else
                [ "$QUIET" = "0" ] && echo "  [info] companion skill '$_name' not installed yet (hook $_p) — 'npx skills add broomva/skills --skill $_name -g'"
            fi
        done <<< "$_HOOK_REPORT"
    fi
fi

# ── summary ─────────────────────────────────────────────────────────────────
echo ""
TOTAL=$((PASSES + GAPS))
if [ "$GAPS" = "0" ]; then
    echo "[bstack doctor] $PASSES/$TOTAL checks passed — workspace fully bstack-compliant."
else
    echo "[bstack doctor] $PASSES/$TOTAL passed, $GAPS gap(s) — see above"
    if [ "$QUIET" = "0" ]; then
        echo "  Run \`bstack revamp\` for full reconfiguration, or fix gaps individually."
    fi
fi

# Strict mode: exit non-zero if any gap (CI usage)
if [ "$STRICT" = "1" ] && [ "$GAPS" != "0" ]; then
    exit 1
fi

# Default: never block a session
exit 0
