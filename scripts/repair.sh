#!/usr/bin/env bash
# bstack repair — apply targeted fixes for gaps surfaced by doctor.
#
# Re-runs `bstack doctor` (--quiet), reads the gap list, and offers fixes:
#   - Missing CLAUDE.md / AGENTS.md / .control/policy.yaml → scaffold from template
#   - Missing policy block (ci_watch / ci_heal / auto_merge) → append from template
#   - Missing hook in .claude/settings.json → merge from snippet (≥ 0.2.3)
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

# ── Hook re-wire (helper) ──────────────────────────────────────────────────
# Idempotently merges every hook in assets/templates/settings.json.snippet
# into $WORKSPACE_DIR/.claude/settings.json. Existing entries are never
# overwritten or reordered — only missing entries are appended. This closes
# the upgrade gap where new hooks shipped in a snippet update would not
# reach existing installs without manually re-running `bstack bootstrap`.
#
# Run before doctor's early-exit so a fully-compliant workspace still picks
# up newly-templated hooks. Silent when everything is in sync.
merge_hooks_into_settings() {
    local snippet="$TEMPLATES_DIR/settings.json.snippet"
    local target="$WORKSPACE_DIR/.claude/settings.json"
    if [ ! -f "$snippet" ]; then
        echo "  [skip] hook merge — snippet not found at $snippet"
        return
    fi
    if ! command -v python3 >/dev/null 2>&1; then
        echo "  [skip] hook merge — python3 not available"
        echo "         manual: copy entries from $snippet into $target"
        return
    fi
    if [ ! -f "$target" ]; then
        if confirm "Scaffold .claude/settings.json from snippet?"; then
            mkdir -p "$(dirname "$target")"
            sed -e "s|\${BROOMVA_WORKSPACE}|$WORKSPACE_DIR|g" \
                -e "s|\${BROOMVA_HOME}|$HOME|g" \
                "$snippet" > "$target"
            echo "  [fix] scaffolded .claude/settings.json"
        elif [ "$DRY_RUN" = "1" ]; then
            echo "  [dry-run] would scaffold .claude/settings.json"
        else
            echo "  [skip] .claude/settings.json (declined)"
        fi
        return
    fi
    python3 - "$snippet" "$target" "$WORKSPACE_DIR" "$HOME" "$DRY_RUN" <<'PYEOF'
import json
import sys
from pathlib import Path

snippet_path, target_path, workspace, home, dry_run_str = sys.argv[1:6]
dry_run = dry_run_str == "1"

raw = Path(snippet_path).read_text()
raw = raw.replace("${BROOMVA_WORKSPACE}", workspace).replace("${BROOMVA_HOME}", home)
template = json.loads(raw)
template.pop("_comment", None)

target = json.loads(Path(target_path).read_text())
target.setdefault("hooks", {})

added = []
for event, blocks in template.get("hooks", {}).items():
    current_blocks = target["hooks"].setdefault(event, [])
    for block in blocks:
        for hook in block.get("hooks", []):
            cmd = hook.get("command", "")
            script_name = Path(cmd).name
            already = any(
                any(Path(h.get("command", "")).name == script_name
                    for h in cb.get("hooks", []))
                for cb in current_blocks
            )
            if already:
                continue
            matching = next(
                (cb for cb in current_blocks if cb.get("matcher") == block.get("matcher")),
                None,
            )
            if matching is None:
                new_block = {"hooks": [hook]}
                if "matcher" in block:
                    new_block["matcher"] = block["matcher"]
                current_blocks.append(new_block)
            else:
                matching.setdefault("hooks", []).append(hook)
            label = f"{event}"
            if block.get("matcher"):
                label += f"[{block['matcher']}]"
            added.append(f"{label}: {script_name}")

if not added:
    print("  ✓ all template hooks already wired")
    sys.exit(0)

if dry_run:
    print(f"  [dry-run] would add {len(added)} hook(s):")
    for line in added:
        print(f"    + {line}")
    sys.exit(0)

Path(target_path).write_text(json.dumps(target, indent=2) + "\n")
print(f"  [fix] added {len(added)} hook(s):")
for line in added:
    print(f"    + {line}")
PYEOF
}

# ── Run doctor to identify gaps ────────────────────────────────────────────
echo "[bstack repair] running doctor to identify gaps..."
echo ""
GAPS_OUTPUT=$(BROOMVA_WORKSPACE="$WORKSPACE_DIR" bash "$DOCTOR" --quiet 2>&1 || true)

# Always attempt hook merge before the early-exit on "fully bstack-compliant".
# The merge is idempotent and prints nothing when every templated hook is
# already wired — so a compliant workspace still sees no extra noise.
if [ "$DRY_RUN" = "1" ] || confirm "Merge missing hooks from settings.json.snippet into .claude/settings.json?"; then
    merge_hooks_into_settings
fi

if echo "$GAPS_OUTPUT" | grep -q "fully bstack-compliant"; then
    echo "  ✓ no other gaps — workspace already bstack-compliant"
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

echo ""
echo "=== post-repair doctor pass ==="
BROOMVA_WORKSPACE="$WORKSPACE_DIR" bash "$DOCTOR" --quiet || true

exit 0
