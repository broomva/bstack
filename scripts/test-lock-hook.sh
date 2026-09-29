#!/usr/bin/env bash
# test-lock-hook.sh — PreToolUse hook (BRO-2542): block an edit to a test that a
# `Test-Lock:` commit trailer pins. Exit 2 blocks, with the reason on stderr.
#
# All logic lives in test_lock.py `hook`, which FAILS OPEN on any internal error
# (exit 0 plus a one-line warning); `bstack-test-lock verify` is the backstop
# that fails closed. If python3 itself is missing, exec fails with 127, which
# Claude Code treats as a non-blocking error — also open.
#
# python3 -I (BRO-2652): the script's own directory and PYTHONPATH stay off
# sys.path, so no json.py or re.py beside it or in the environment can replace a
# stdlib module. test_lock.py loads its one sibling, git_env_policy.py, by path.
exec python3 -I "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/test_lock.py" hook
