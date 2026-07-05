#!/usr/bin/env bash
# bstack/scripts/l1-reflex-audit-hook.sh — DEPRECATED, no-op stub (retired v0.31.0).
#
# This Stop hook inferred /autonomous reflex compliance by grepping the session
# transcript for the agent's own prose — a sensor correlated with the controller
# it graded (h NOT ⟂ U), and it read tool_call_count (structurally 0). It is
# SUPERSEDED by the leverage-sensor (loop-sensor Stop hook, v0.30.0), whose L1
# metric — m1 continue_nudges_per_session — is derived from transcript structure
# (short user-turn nudges), independent of the agent's narration. doctor §17 now
# reads that from .control/leverage-state.json instead of this log.
#
# Kept as a no-op stub (not deleted) so any workspace still wiring this path from a
# pre-v0.30.0 install gets a clean exit instead of a missing-file error. Re-run
# `install-rcs-stability.sh` to migrate. A future release removes this file.
cat >/dev/null 2>&1 || true   # drain stdin so Claude Code's write never SIGPIPEs
exit 0
