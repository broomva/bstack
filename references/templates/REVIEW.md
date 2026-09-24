# Review instructions

## Passes

Run three passes and tag each finding with its pass:

- **Bugs**: logic errors, broken edge cases, subtle regressions
- **Security**: injection, authentication gaps, secrets or PII in logs and diffs
- **Compliance**: the change matches `spec.md`, `plan.md` and the design principles in `AGENTS.md`

## What Important means here

Reserve Important for findings that would break behavior, leak data, or breach a policy.
Style and naming are nits.

## Cap the nits

Report at most five nits per review. Summarize the rest as a count.

## Do not report

<generated paths> and anything CI already enforces.

## Feedback into the configuration

A mistake a review flags for the first time goes to memory. At the third occurrence it is
proposed for `AGENTS.md` (Crystallize, P16). A review also flags when a change has made
`CLAUDE.md` or `AGENTS.md` outdated.
