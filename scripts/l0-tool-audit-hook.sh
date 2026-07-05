#!/usr/bin/env bash
# bstack/scripts/l0-tool-audit-hook.sh — DEPRECATED, no-op stub (retired v0.31.0).
#
# This PostToolUse hook logged tool_result.latency_ms — a field Claude Code never
# emits (100% null) — to .control/audit/l0-tools.jsonl. It is SUPERSEDED by the
# transcript-derived leverage-sensor (scripts/leverage-sensor.py, wired as the
# loop-sensor Stop hook in v0.30.0), whose L0 metrics — m2 tool_error_rate,
# m3 read_before_edit_rate, m4 permission_bypass — come from transcript STRUCTURE
# (tool_result.is_error), so h ⟂ U. doctor §16 now reads those from
# .control/leverage-state.json instead of this log.
#
# Kept as a no-op stub (not deleted) so any workspace that still wires this path
# from a pre-v0.30.0 install gets a clean exit instead of a missing-file error.
# Re-run `install-rcs-stability.sh` to migrate. A future release removes this file.
cat >/dev/null 2>&1 || true   # drain stdin so Claude Code's write never SIGPIPEs
exit 0
