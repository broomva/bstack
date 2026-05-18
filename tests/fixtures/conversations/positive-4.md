# session 2026-05-18 — Phase 6 (v0.9.0) ship

The squash merge race **recurred** during the v0.9.0 PR. Auto-merge
captured the state before the shellcheck-portability fix landed; the
post-merge release was missing the corrected assertion. **Had to redo**
the merge after CI re-ran on the latest commit.

Same pattern, fourth incident now. The `--auto` flag interaction with
in-flight fix pushes keeps biting us — auto-merge picked the wrong
commit and the pipeline shipped broken until the follow-up.

Adding this to the crystallization queue: ≥3 instances, concrete
mechanism (auto-merge state capture), explicit failure mode (orphaned
fix commit, shipped broken release).
