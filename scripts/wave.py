#!/usr/bin/env python3
"""bstack wave — parallel sub-phase dispatch for Claude Code agent view.

Stdlib-only. See docs/superpowers/specs/2026-05-13-bstack-wave-design.md.
"""
from __future__ import annotations

import argparse
import sys
from typing import Sequence


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="bstack wave")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("dispatch")
    sub.add_parser("status")
    sub.add_parser("list")
    sub.add_parser("report")
    args = parser.parse_args(argv)
    raise SystemExit(0)


if __name__ == "__main__":
    sys.exit(main())
