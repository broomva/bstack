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
export BSTACK_WAVE_CACHE_DIR="$(mktemp -d)"
trap 'rm -rf "$BSTACK_WAVE_CACHE_DIR"' EXIT

echo "tests/wave — python3 -m unittest discover -s tests/wave -t ."
if PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests/wave -t . 2>&1 | tail -n 25; then
  :
fi
# `tail` swallowed the status; re-run the discovery's exit code the honest way.
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests/wave -t . >/dev/null 2>&1
rc=$?
if [ "$rc" -eq 0 ]; then
  echo "  ✓ tests/wave suite green"
else
  echo "  ✗ tests/wave suite failed (exit $rc)"
fi
exit "$rc"
