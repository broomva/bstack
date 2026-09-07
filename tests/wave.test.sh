#!/usr/bin/env bash
# tests/wave.test.sh — run the python suite for `bstack wave` + the peer
# spawn contract (scripts/wave.py, scripts/peer.py) under the `tests/*.test.sh`
# CI job. Before BRO-2453 the suite under tests/wave/ existed but nothing in
# ci.yml invoked it, so wave shipped with a dead gate.
set -uo pipefail

BSTACK_REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$BSTACK_REPO" || exit 1

# The suite mutates BSTACK_WAVE_* env for its stubs; keep it out of the
# operator's real ~/.cache/bstack/wave.
BSTACK_WAVE_CACHE_DIR="$(mktemp -d)"
export BSTACK_WAVE_CACHE_DIR
trap 'rm -rf "$BSTACK_WAVE_CACHE_DIR"' EXIT

echo "tests/wave — python3 -m unittest discover -s tests/wave -t ."
# One run. `pipefail` is set, so the pipeline's status is unittest's, not
# tail's; PIPESTATUS[0] is read explicitly so the intent survives a future
# edit that drops pipefail.
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests/wave -t . 2>&1 | tail -n 25
rc="${PIPESTATUS[0]}"
if [ "$rc" -eq 0 ]; then
  echo "  ✓ tests/wave suite green"
else
  echo "  ✗ tests/wave suite failed (exit $rc)"
fi
exit "$rc"
