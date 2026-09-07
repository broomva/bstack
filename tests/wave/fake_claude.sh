#!/bin/sh
# Stub claude binary for E2E testing. Mirrors the two calls wave makes:
#   claude agents --json --all            -> an empty listing
#   claude --bg --name <n> ... <prompt>   -> prints `backgrounded · <id>` like
#                                            the real binary, then walks the
#                                            plan's lifecycle by shelling out
#                                            to `wave report`, as a peer would.
# The prompt is the LAST argument (peer.build_spawn_argv), never a fixed slot.

if [ "$1" = "agents" ]; then
  echo "[]"
  exit 0
fi

eval "PROMPT=\${$#}"
WAVE=$(echo "$PROMPT" | sed -n 's/^Wave: //p')
SLUG=$(echo "$PROMPT" | sed -n 's/^Plan-slug: //p')
PYTHON_WAVE="${BSTACK_WAVE_PY:-python3 scripts/wave.py}"

# Real output is ANSI-coloured on a TTY; keep the escape codes so the id
# capture is exercised against the shape it will actually see.
printf '\033[1mbackgrounded\033[0m \033[2m·\033[0m \033[36me2e%s\033[0m\n' "$(printf '%s' "$SLUG" | cksum | cut -c1-4)"

$PYTHON_WAVE report --wave "$WAVE" --plan "$SLUG" --event started
$PYTHON_WAVE report --wave "$WAVE" --plan "$SLUG" --event branch_pushed --branch test --head abc1234
$PYTHON_WAVE report --wave "$WAVE" --plan "$SLUG" --event pr_opened --pr https://github.com/o/r/pull/42
$PYTHON_WAVE report --wave "$WAVE" --plan "$SLUG" --event pr_merged --merge-sha def5678
exit 0
