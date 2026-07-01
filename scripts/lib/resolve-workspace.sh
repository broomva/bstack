#!/usr/bin/env bash
# resolve-workspace.sh — shared workspace resolver for bstack status/metrics.
#
# Resolution order (BRO-1632):
#   1. $BROOMVA_WORKSPACE                         (explicit env override)
#   2. ~/.bstack/config.yaml `workspace:` key     (via bin/bstack-config get)
#   3. $PWD                                        (last resort)
#
# WHY: `bstack status` + its metrics collectors previously resolved the workspace
# as `${BROOMVA_WORKSPACE:-$PWD}` only — they never consulted the configured
# `workspace` key. On a host where the env var is unset and the command runs from
# a directory other than the workspace, they silently read the wrong `.control/`
# tree and reported false blocking violations. Consulting the config key closes
# that gap durably (no need to export the env var in every shell).
#
# Usage:  source "$BSTACK_DIR/scripts/lib/resolve-workspace.sh"; ws="$(resolve_workspace)"
resolve_workspace() {
  if [ -n "${BROOMVA_WORKSPACE:-}" ]; then
    printf '%s\n' "${BROOMVA_WORKSPACE}"
    return 0
  fi
  local _bin _cfg
  _bin="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../bin" 2>/dev/null && pwd || true)"
  if [ -n "${_bin}" ] && [ -f "${_bin}/bstack-config" ]; then
    _cfg="$(bash "${_bin}/bstack-config" get workspace 2>/dev/null || true)"
    if [ -n "${_cfg}" ] && [ -d "${_cfg}" ]; then
      printf '%s\n' "${_cfg}"
      return 0
    fi
  fi
  printf '%s\n' "${PWD}"
}
