#!/usr/bin/env bash
# tests/fleet.test.sh — run the python suite for `bstack fleet`
# (scripts/fleet.py on top of scripts/peer.py) under the `tests/*.test.sh` CI
# job. `ci.yml` runs `tests/*.test.sh` only: a python suite with no wrapper is
# a dead gate, which is exactly how wave shipped ungated until BRO-2453.
set -uo pipefail

BSTACK_REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$BSTACK_REPO" || exit 1

# The suite stubs the claude binary and points every state surface at temp
# dirs; this keeps it out of the operator's real ~/.cache/bstack/fleet even if
# a test forgets its own sandbox.
BSTACK_FLEET_STATE_DIR="$(mktemp -d)"
export BSTACK_FLEET_STATE_DIR
trap 'rm -rf "$BSTACK_FLEET_STATE_DIR"' EXIT

echo "tests/fleet — python3 -m unittest discover -s tests/fleet -t ."
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests/fleet -t . 2>&1 | tail -n 25
# `tail` reports its OWN status, so the pipeline above cannot decide the gate.
# Re-run for the honest exit code.
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests/fleet -t . >/dev/null 2>&1
rc=$?
if [ "$rc" -eq 0 ]; then
  echo "  ✓ tests/fleet suite green"
else
  echo "  ✗ tests/fleet suite failed (exit $rc)"
fi
exit "$rc"
