#!/usr/bin/env bash
# bstack/scripts/install-rcs-stability.sh — Deploy the full multi-layer RCS
# stability infrastructure into a workspace (L0 + L1 + L2 + L3).
#
# Extends install-l3-stability.sh (v0.14.0) — calls it for the L3 gates,
# then layers on L0 + L1 audit hooks. L2 promotion audit is wired by
# bookkeeping.py calling l2-promotion-audit-hook.sh at promote step (no
# install-side wiring needed for L2 today; the hook is invoked
# programmatically by bookkeeping).
#
# Called by:
#   - bstack/scripts/onboard.sh    (v0.15.0+ replaces install-l3-stability)
#   - bstack/scripts/repair.sh     (when doctor §16/§17/§19 surface gaps)
#   - directly                     (manual install)
#
# Idempotent — safe to re-run. Honors the same --force / --dry-run /
# --workspace flags as install-l3-stability.

set -uo pipefail

WORKSPACE="${BROOMVA_WORKSPACE:-$PWD}"
FORCE=0
DRY_RUN=0

for arg in "$@"; do
    case "$arg" in
        --workspace=*) WORKSPACE="${arg#*=}" ;;
        --force)       FORCE=1 ;;
        --dry-run)     DRY_RUN=1 ;;
        --help|-h)
            grep -E '^#( |$)' "$0" | sed 's/^# \?//' | head -20
            exit 0
            ;;
        *)
            echo "install-rcs-stability: unknown flag: $arg" >&2
            exit 2
            ;;
    esac
done

BSTACK_REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# BRO-1929: skip the loop-sensor (leverage-sensor) Stop hook when the
# bstack@skills-dir plugin is enabled host-scope — hand-wiring alongside it races
# two writers on leverage-state.json. Enable-state (persisted) is the single
# source of truth; install-l3-stability.sh (invoked below) self-determines the same.
# shellcheck source=scripts/lib/plugin-preference.sh
. "$BSTACK_REPO/scripts/lib/plugin-preference.sh"
SKIP_PLUGIN_HOOKS=0
if bstack_plugin_enabled; then
    SKIP_PLUGIN_HOOKS=1
fi

echo "install-rcs-stability: target = $WORKSPACE"
echo "install-rcs-stability: bstack = $BSTACK_REPO"
echo ""

# ── 1. Run install-l3-stability (L3 gates: G0 PreToolUse + G1 pre-commit
#       + G2 GH Actions + rcs-parameters.toml) ─────────────────────────────
echo "─── L3 gates (delegating to install-l3-stability.sh) ───"
INSTALL_L3="$BSTACK_REPO/scripts/install-l3-stability.sh"
if [ ! -f "$INSTALL_L3" ]; then
    echo "install-rcs-stability: install-l3-stability.sh not found — cannot wire L3" >&2
    exit 2
fi
L3_FLAGS="--workspace=$WORKSPACE"
[ "$FORCE" = "1" ] && L3_FLAGS="$L3_FLAGS --force"
[ "$DRY_RUN" = "1" ] && L3_FLAGS="$L3_FLAGS --dry-run"
# shellcheck disable=SC2086
bash "$INSTALL_L3" $L3_FLAGS

echo ""
echo "─── L0 + L1 audit hooks ───"

# ── 2. Merge the multi-layer-hooks snippet (PostToolUse + Stop) into
#       .claude/settings.json. Idempotent via _bstack_primitive markers ─────
SETTINGS="$WORKSPACE/.claude/settings.json"
SNIPPET="$BSTACK_REPO/assets/templates/settings.json.multi-layer-hooks.snippet"

if [ "$SKIP_PLUGIN_HOOKS" = "1" ]; then
    echo "  [skip] loop-sensor (leverage-sensor) Stop hook — provided by the bstack@skills-dir plugin (BRO-1929)"
elif [ "$DRY_RUN" = "1" ]; then
    echo "  [dry] would merge loop-sensor (leverage-sensor) Stop hook into $SETTINGS"
elif ! command -v python3 >/dev/null 2>&1; then
    echo "  [skip] python3 not available; cannot merge JSON safely"
else
    mkdir -p "$(dirname "$SETTINGS")"
    python3 - "$SETTINGS" "$SNIPPET" "$BSTACK_REPO" <<'PYEOF'
import sys, json
from pathlib import Path

settings_path = Path(sys.argv[1])
snippet_path = Path(sys.argv[2])
bstack_repo = sys.argv[3]

if settings_path.exists():
    try:
        with settings_path.open() as f:
            settings = json.load(f)
    except json.JSONDecodeError:
        print(f"  [skip] {settings_path} not valid JSON; leaving alone")
        sys.exit(1)
else:
    settings = {}

with snippet_path.open() as f:
    snippet = json.load(f)

settings.setdefault("hooks", {})

def has_marker(entries, marker):
    for entry in entries or []:
        for h in entry.get("hooks", []):
            if h.get("_bstack_primitive") == marker:
                return True
    return False

def substitute(o):
    if isinstance(o, dict):
        return {k: substitute(v) for k, v in o.items()}
    if isinstance(o, list):
        return [substitute(x) for x in o]
    if isinstance(o, str):
        return o.replace("$BSTACK_REPO", bstack_repo)
    return o

snippet_substituted = substitute(snippet)

# Per-event merge
events_added = []
for event_name, entries in snippet_substituted.get("hooks", {}).items():
    existing = settings["hooks"].setdefault(event_name, [])
    for entry in entries:
        # Determine the marker on the new entry
        markers = [h.get("_bstack_primitive") for h in entry.get("hooks", [])]
        for marker in markers:
            if marker and has_marker(existing, marker):
                continue
            # Append the entry (only if no existing entry has this marker)
            existing.append(entry)
            events_added.append(f"{event_name}: {marker}")
            break  # one append per snippet entry

if events_added:
    with settings_path.open("w") as f:
        json.dump(settings, f, indent=2)
    print(f"  [ok]   {settings_path} (added {len(events_added)} hooks):")
    for e in events_added:
        print(f"           {e}")
else:
    print(f"  [skip] {settings_path} (loop-sensor hook already present)")
PYEOF
fi

# ── 3. Ensure audit directory exists ─────────────────────────────────────
echo ""
echo "─── Audit directory ───"
AUDIT_DIR="$WORKSPACE/.control/audit"
if [ -d "$AUDIT_DIR" ]; then
    echo "  [ok]   $AUDIT_DIR (exists)"
elif [ "$DRY_RUN" = "1" ]; then
    echo "  [dry]  would mkdir -p $AUDIT_DIR"
else
    mkdir -p "$AUDIT_DIR"
    echo "  [ok]   $AUDIT_DIR (created)"
fi

# ── Summary ───────────────────────────────────────────────────────────────
echo ""
echo "install-rcs-stability: done"
echo ""
echo "Verify with:"
echo "  bash $BSTACK_REPO/scripts/doctor.sh                  # all sections incl. §14–§19"
echo "  bash $BSTACK_REPO/scripts/compute-budget-status.sh --human  # multi-layer report"
echo "  bash $BSTACK_REPO/scripts/compute-lambda.sh --human         # paper-side λ"

exit 0
