# session 2026-05-16 — release v0.2.3 recovery

The squash merge race struck **once more**. Fix commit pushed while the
prior CI run was still failing; auto-merge captured the orphaned state
and v0.2.2 shipped broken. Reverted via v0.2.3.

That is the **second time** this exact pattern has hit us in 48 hours.
The `--auto` flag is doing what it was asked to do, but the race window
between failed CI and the in-flight fix push is wider than expected.

The fix lost in the squash was the SC2034 shellcheck exclude. Re-applied
in v0.2.3 and re-enabled `--auto` only after the new commit's CI began.
