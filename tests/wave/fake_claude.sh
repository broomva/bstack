#!/bin/sh
# Stub claude binary for E2E testing. The second argument is the prompt;
# we parse Wave and Plan-slug from it, then walk through the full lifecycle.

PROMPT="$2"
WAVE=$(echo "$PROMPT" | sed -n 's/^Wave: //p')
SLUG=$(echo "$PROMPT" | sed -n 's/^Plan-slug: //p')
PYTHON_WAVE="${BSTACK_WAVE_PY:-python3 scripts/wave.py}"

$PYTHON_WAVE report --wave "$WAVE" --plan "$SLUG" --event started
$PYTHON_WAVE report --wave "$WAVE" --plan "$SLUG" --event branch_pushed --branch test --head abc1234
$PYTHON_WAVE report --wave "$WAVE" --plan "$SLUG" --event pr_opened --pr https://github.com/o/r/pull/42
$PYTHON_WAVE report --wave "$WAVE" --plan "$SLUG" --event pr_merged --merge-sha def5678
exit 0
