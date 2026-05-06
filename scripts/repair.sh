#!/usr/bin/env bash
# bstack repair — apply targeted fixes for gaps surfaced by doctor.
#
# Re-runs `bstack doctor` (--quiet), reads the gap list, and offers fixes:
#   - Missing CLAUDE.md / AGENTS.md / .control/policy.yaml → scaffold from template
#   - Missing policy block (ci_watch / ci_heal / auto_merge) → append from template
#   - Missing hook in .claude/settings.json → suggest re-running bootstrap
#
# Modes:
#   default     — interactive; asks before each fix
#   --apply-all — apply every fix without asking (CI / scripted use)
#   --dry-run   — list what would be fixed; do not write
#
# Always idempotent. Never destructive.

set -uo pipefail

INTERACTIVE=1
DRY_RUN=0
while [ $# -gt 0 ]; do
    case "$1" in
        --apply-all) INTERACTIVE=0; shift ;;
        --dry-run)   DRY_RUN=1; shift ;;
        --help|-h)
            grep -E '^#( |$)' "$0" | sed 's/^# \?//' | head -25
            exit 0
            ;;
        *) shift ;;
    esac
done

WORKSPACE_DIR="${BROOMVA_WORKSPACE:-$PWD}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SKILL_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
TEMPLATES_DIR="$SKILL_ROOT/assets/templates"
DOCTOR="$SCRIPT_DIR/doctor.sh"

if [ ! -f "$DOCTOR" ]; then
    echo "bstack repair: doctor.sh not found at $DOCTOR" >&2
    exit 2
fi

confirm() {
    [ "$INTERACTIVE" = "0" ] && return 0
    [ "$DRY_RUN" = "1" ] && return 1
    local prompt="$1"
    read -r -p "$prompt [y/N] " reply
    [ "${reply:-N}" = "y" ] || [ "${reply:-N}" = "Y" ]
}

scaffold_if_missing() {
    local target="$1"
    local template="$2"
    if [ -f "$WORKSPACE_DIR/$target" ]; then return; fi
    if [ ! -f "$TEMPLATES_DIR/$template" ]; then
        echo "  [skip] $target — template missing in skill: $template"
        return
    fi
    if confirm "Scaffold $target from $template?"; then
        mkdir -p "$WORKSPACE_DIR/$(dirname "$target")"
        sed "s/{{WORKSPACE_NAME}}/$(basename "$WORKSPACE_DIR")/g" \
            "$TEMPLATES_DIR/$template" > "$WORKSPACE_DIR/$target"
        echo "  [fix] scaffolded $target"
    elif [ "$DRY_RUN" = "1" ]; then
        echo "  [dry-run] would scaffold $target"
    else
        echo "  [skip] $target (declined)"
    fi
}

append_policy_block_if_missing() {
    local block_name="$1"
    local pol="$WORKSPACE_DIR/.control/policy.yaml"
    if [ ! -f "$pol" ]; then
        echo "  [skip] $block_name — .control/policy.yaml absent (scaffold first)"
        return
    fi
    if grep -qE "^${block_name}:" "$pol"; then return; fi
    if confirm "Append $block_name: block to .control/policy.yaml?"; then
        python3 - "$TEMPLATES_DIR/policy.yaml.template" "$block_name" "$pol" <<'PYEOF'
import sys
from pathlib import Path

template, block_name, target = sys.argv[1], sys.argv[2], sys.argv[3]
text = Path(template).read_text()
lines = text.splitlines()
in_block = False
block_lines = []
for line in lines:
    if line.startswith(f"{block_name}:"):
        in_block = True
        block_lines.append(line)
        continue
    if in_block:
        if line and not line.startswith(" ") and not line.startswith("#") and ":" in line:
            break
        block_lines.append(line)

if not block_lines:
    sys.exit(0)
while block_lines and not block_lines[-1].strip():
    block_lines.pop()
with Path(target).open("a") as f:
    f.write("\n# === Appended by bstack repair ===\n")
    f.write("\n".join(block_lines))
    f.write("\n")
print(f"  [fix] appended {block_name}: block ({len(block_lines)} lines)")
PYEOF
    elif [ "$DRY_RUN" = "1" ]; then
        echo "  [dry-run] would append $block_name: block"
    else
        echo "  [skip] $block_name (declined)"
    fi
}

# ── Run doctor to identify gaps ────────────────────────────────────────────
echo "[bstack repair] running doctor to identify gaps..."
echo ""
GAPS_OUTPUT=$(BROOMVA_WORKSPACE="$WORKSPACE_DIR" bash "$DOCTOR" --quiet 2>&1 || true)

if echo "$GAPS_OUTPUT" | grep -q "fully bstack-compliant"; then
    echo "  ✓ no gaps — workspace already bstack-compliant"
    exit 0
fi

echo "$GAPS_OUTPUT"
echo ""
echo "[bstack repair] applying fixes..."
echo ""

# ── Governance files ───────────────────────────────────────────────────────
echo "$GAPS_OUTPUT" | grep -q "CLAUDE.md missing" && scaffold_if_missing "CLAUDE.md" "CLAUDE.md.template"
echo "$GAPS_OUTPUT" | grep -q "AGENTS.md missing" && scaffold_if_missing "AGENTS.md" "AGENTS.md.template"
echo "$GAPS_OUTPUT" | grep -q ".control/policy.yaml missing" && scaffold_if_missing ".control/policy.yaml" "policy.yaml.template"

# ── policy.yaml blocks ─────────────────────────────────────────────────────
echo "$GAPS_OUTPUT" | grep -q "ci_watch: block missing" && append_policy_block_if_missing "ci_watch"
echo "$GAPS_OUTPUT" | grep -q "ci_heal: block missing" && append_policy_block_if_missing "ci_heal"
echo "$GAPS_OUTPUT" | grep -q "auto_merge: block missing" && append_policy_block_if_missing "auto_merge"

# ── Hook re-wire ───────────────────────────────────────────────────────────
if echo "$GAPS_OUTPUT" | grep -qE "(conversation-bridge|control-gate|skill-freshness)-hook.sh not wired"; then
    echo "  Note: run 'bstack bootstrap' to wire hooks; repair handles only"
    echo "        governance files and policy blocks."
fi

echo ""
echo "=== post-repair doctor pass ==="
BROOMVA_WORKSPACE="$WORKSPACE_DIR" bash "$DOCTOR" --quiet || true

exit 0
