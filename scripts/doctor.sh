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
#      the policy, not a hook)
#   4. .control/policy.yaml has required blocks (ci_watch, ci_heal, auto_merge)
#   5. .claude/settings.json hooks wire the expected primitive scripts
#   6. Each primitive's mechanism is reachable on disk:
#      - P1: scripts/conversation-bridge-hook.sh
#      - P2: scripts/control-gate-hook.sh + .control/policy.yaml
#      - P6: skills/bookkeeping/scripts/bookkeeping.py
#      - P7: scripts/skill-freshness-hook.sh
#      - P8: scripts/branch-janitor.sh
#      - P9: skills/p9/scripts/p9.py
#      - P12: skills/persist/scripts/persist.py
#   7. (continued — each primitive's mechanism)
#   8. L3 trust gates (G-L3-1 + G-L3-2) pass via scripts/bstack-primitive-lint.py
#      and scripts/bstack-rule-of-three.py; broomva/autonomous skill installed
#      (the canonical operating mode on top of the substrate)
#
# Usage:
#   bash scripts/doctor.sh               # full report
#   bash scripts/doctor.sh --quiet       # only warnings
#   bash scripts/doctor.sh --strict      # exit 1 if any gap found (CI mode)

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
WORKSPACE="${BROOMVA_WORKSPACE:-$HOME/broomva}"
if [ ! -d "$WORKSPACE/.git" ] && [ ! -d "$WORKSPACE/.control" ]; then
    echo "[bstack doctor] workspace not found at $WORKSPACE (set BROOMVA_WORKSPACE)"
    exit 0
fi

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
    "P9: Productive Wait"
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
        if grep -qE "^### $prefix:" "$AGENTS"; then
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
declare -a REFLEXIVE_PRIMS=(P6 P9 P10 P11 P12 P13 P14 P15 P16 P17 P18 P19 P20)
if [ -f "$AGENTS" ]; then
    for prim in "${REFLEXIVE_PRIMS[@]}"; do
        # Look for "P{n} is a reflex" OR "Reflexive Trigger Rule" in proximity to the prim section
        if awk -v p="$prim" '
            /^### / { in_sec = ($0 ~ "^### "p":") }
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
)
HOOK_LABELS=(
    "P1 (Stop, Notification)"
    "P2 (PreToolUse)"
    "P7 (SessionStart)"
)
if [ -f "$SETTINGS" ]; then
    for i in "${!HOOK_FILES[@]}"; do
        hk="${HOOK_FILES[$i]}"
        label="${HOOK_LABELS[$i]}"
        if grep -q "$hk" "$SETTINGS"; then
            ok "$hk wired ($label)"
        else
            gap "$hk not wired in .claude/settings.json ($label)" \
                "add the hook entry per AGENTS.md primitive spec"
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
    if [ -e "$WORKSPACE/$path" ]; then
        ok "$label: $path"
    else
        gap "$label mechanism missing: $path" \
            "install the corresponding skill: npx skills add broomva/<skill>"
    fi
done

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
    ok "broomva/autonomous installed (canonical operating mode available)"
else
    gap "broomva/autonomous skill not installed" \
        "install the canonical operating mode: npx skills add broomva/autonomous"
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
