Real Stop-hook inputs from Claude Code 2.1.295 (BRO-2815), captured 2026-10-09 by a
dump hook on throwaway `claude -p` sessions in /tmp:

- `shell-pending.json`: `run_in_background` Bash still running at the turn end.
- `none-pending.json`: the same session's next stop, after the task finished.
- `subagent-pending.json`: a background Agent still running ("Reviewer launched; I will
  wait for its notification."); the stop bg-task-stop-guard blocked on the live run.
- `shell-pending-hook-active.json`: the stop after that block (`stop_hook_active: true`),
  with a shell the subagent left behind still listed.

The first two were transcribed from the capture's printed output; the last two are the
captured files byte for byte. A Monitor watch was measured as `type: "shell"` too.
