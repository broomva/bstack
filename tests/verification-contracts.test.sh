#!/usr/bin/env bash
# Exercise verification boundaries using offline subprocess fixtures.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
python3 -m unittest discover -s "$ROOT/tests" -p test_verification_contracts.py -v
