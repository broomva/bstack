# session 2026-05-17 — onboard T1 portability fix

Squash merge race orphaned the portable onboard assertion **again**.
That is the **third time** we have hit this in a single arc. The fix
commit was lost in the squash because `--auto` had latched onto the
pre-fix CI state.

The recovery path is consistent: revert the broken release, re-apply
the missing assertion in a follow-up patch, and only re-enable the
auto-merge switch *after* the new commit's CI has begun running.

Marking this as a rule-of-three candidate for P16 promotion. The pattern
is real, the mechanism is concrete, the failure mode is named.
