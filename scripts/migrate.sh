#!/usr/bin/env bash
# migrate.sh — apply schema migrations to .control/policy.yaml on bstack
# upgrade. Invoked by `bstack repair` (after the hook merge) and by
# `bstack-autoupdate-hook.sh` (post-pull, before next session).
#
# Migration semantics:
#   * v1 → v1   identity (no-op). Today this is the only valid path.
#   * vN → vN+1 future migrations declared in scripts/migrations/<from>-to-<to>.sh
#               and dispatched here.
#
# Always idempotent. Never destructive. A backup is written to
# .control/policy.yaml.bak.<epoch> before any structural change is applied.
#
# Usage:
#   bash scripts/migrate.sh                 — detect + apply (default)
#   bash scripts/migrate.sh --dry-run       — report what would migrate
#   bash scripts/migrate.sh --from 1 --to 2 — force specific path
#   bash scripts/migrate.sh --help          — this message
#
# Exit codes:
#   0  migration applied (or no-op)
#   1  detection failed (no policy.yaml, or version unparseable)
#   2  destination version unknown (no migration registered)
#   3  user declined (interactive)
set -euo pipefail

WORKSPACE_DIR="${BROOMVA_WORKSPACE:-$PWD}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SKILL_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
POLICY="$WORKSPACE_DIR/.control/policy.yaml"

DRY_RUN=0
FORCE_FROM=""
FORCE_TO=""
INTERACTIVE=1

CURRENT_SCHEMA="1"   # bumped when a new policy.yaml schema ships

while [ $# -gt 0 ]; do
    case "$1" in
        --dry-run)     DRY_RUN=1; shift ;;
        --apply-all)   INTERACTIVE=0; shift ;;
        --from)        FORCE_FROM="${2:?}"; shift 2 ;;
        --to)          FORCE_TO="${2:?}"; shift 2 ;;
        -h|--help)
            sed -n '2,30p' "$0" | sed 's/^# \?//'
            exit 0
            ;;
        *) shift ;;
    esac
done

confirm() {
    [ "$INTERACTIVE" = "0" ] && return 0
    [ "$DRY_RUN" = "1" ] && return 1
    local prompt="$1"
    read -r -p "$prompt [y/N] " reply
    [ "${reply:-N}" = "y" ] || [ "${reply:-N}" = "Y" ]
}

# Detect current policy.yaml version (the major number — `1.x` → `1`).
detect_version() {
    if [ ! -f "$POLICY" ]; then
        echo "migrate: no policy.yaml at $POLICY" >&2
        return 1
    fi
    local v
    v=$(grep -E '^version:' "$POLICY" 2>/dev/null | head -1 | awk '{print $2}' | tr -d '"' | tr -d "'" | cut -d. -f1)
    if [ -z "$v" ]; then
        echo "migrate: could not parse version field in $POLICY" >&2
        return 1
    fi
    echo "$v"
}

# v1 → v1 is identity; report no-op and exit.
migration_v1_to_v1() {
    if [ "$DRY_RUN" = "1" ]; then
        echo "  [dry-run] v1 → v1 no-op (policy.yaml schema unchanged)"
        return 0
    fi
    echo "  [ok] v1 → v1 identity (no policy.yaml changes needed)"
}

# Dispatch table — extend as new versions ship.
apply_migration() {
    local from="$1" to="$2"
    if [ "$from" = "$to" ]; then
        case "$from" in
            1) migration_v1_to_v1 ;;
            *) echo "migrate: unknown identity migration v$from → v$from" >&2; return 2 ;;
        esac
        return $?
    fi
    case "$from-$to" in
        # Future: register migrations here, e.g.:
        # 1-2) migration_v1_to_v2 ;;
        *)
            echo "migrate: no migration registered for v$from → v$to" >&2
            return 2
            ;;
    esac
}

CURRENT_FROM="${FORCE_FROM:-$(detect_version)}" || exit 1
CURRENT_TO="${FORCE_TO:-$CURRENT_SCHEMA}"

echo "[migrate] policy.yaml: detected v${CURRENT_FROM}, target v${CURRENT_TO}"

if [ "$CURRENT_FROM" = "$CURRENT_TO" ]; then
    apply_migration "$CURRENT_FROM" "$CURRENT_TO"
    exit 0
fi

# Non-identity migration: require user confirmation in interactive mode.
if [ "$DRY_RUN" = "0" ] && ! confirm "Apply migration v${CURRENT_FROM} → v${CURRENT_TO} to $POLICY?"; then
    echo "  [skip] migration declined"
    exit 3
fi

# Backup before structural changes.
if [ "$DRY_RUN" = "0" ]; then
    BACKUP="${POLICY}.bak.$(date +%s)"
    cp "$POLICY" "$BACKUP"
    echo "  [backup] $BACKUP"
fi

apply_migration "$CURRENT_FROM" "$CURRENT_TO"
