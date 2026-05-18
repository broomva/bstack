# session 2026-05-15 — auto-merge mishap

Worked on the v0.2.x release. Enabled `gh pr merge --auto` while the
shellcheck job was still failing. The squash merge race captured an
orphaned commit — the fix push that landed afterwards did not make
it into the squash. Had to revert and ship a fast follow-up.

This happened **again** later in the same day during the doctor lint
patch — the auto-merge picked up the pre-fix state and the merged commit
shipped broken. Tagged a follow-on patch to recover.

Lesson: never enable `--auto` while CI is red. Wait for the latest
commit's CI to start before flipping the auto-merge switch.
