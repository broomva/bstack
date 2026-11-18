# Verification contracts (v0.52.0)

bstack distributes four versioned JSON contracts and checks records at its own
CLI boundary. Detailed skeptical/believing review procedures remain in companion
skills. No new primitive or global merge gate is introduced.

| Contract | Shape and checked invariants |
|---|---|
| `review-result` | Existing pass/concerns/fail result, five integer axes summing to the score, pass ≥7 with no zero, typed findings with evidence and settling observations |
| `claim-review` | Claim ledger with established/contested/untested status; three distinct fresh-context receipts bound to one artifact and all claim IDs; refuter raw-source coverage |
| `measurement` | Scoped claim, conditions, raw target/control references, predeclared falsification criterion, control/target outcomes and a consistent conclusion |
| `capability-plan` | Every operation tagged executable_now/request_quote/send_out, unique IDs, evidence appropriate to its tier and explicit authorization for catalog work |

Schemas live in `schemas/<contract>.v1.json`; bootstrap copies them to a workspace's
`schemas/` only when absent. A v1 file already present is preserved, including a
customized file or a broken symlink. Doctor reports a missing or diverged copy;
it does not silently replace it. A future incompatible contract needs a new
versioned filename. The bstack checker uses bundled schemas, so workspace edits
cannot weaken what `bstack verify` itself checks.

## Commands and meaning

```sh
bstack verify review-result review.json
bstack verify claim-review packet.json
bstack verify measurement observation.json
bstack verify capability-plan operations.json
```

Use `-` for stdin. Exit 0 means **the record validates**, not that the target
claim is true or the proposal authorized in the live environment. Exit 1 is an
invalid/unreadable record. Exit 2 is an unavailable optional validator or bad
invocation. Non-review contracts require `jsonschema` (`python3 -m pip install
jsonschema` in the interpreter environment used by bstack); it is never installed
automatically. Dependency absence is explicitly `inconclusive_dependency`.

`review-result` validation is dependency-free because it sits on the live review
path. Its runtime checks add the arithmetic invariant JSON Schema cannot express:
the score equals the sum of axes. The claim packet checker also checks references
between rows, distinct dispatch IDs, hash agreement and coverage. Validators
reject duplicate JSON keys and non-finite JSON constants instead of guessing.

## Negative evidence

`measurement` describes an **output record**, not a grader for raw signals. It
requires `control_ref`, using null when no verified positive control exists.

- A passing relevant control plus detected target yields `detected`.
- A passing control plus target null yields `refuted` only with
  `absence_refutes: true`, a criterion declared before observing the result.
- Otherwise a controlled null is `not_detected`.
- Missing, failed, errored or skipped controls require
  `inconclusive_no_positive_control`.
- Target errors/skips with a live control yield `inconclusive`.

A structurally valid inconclusive record still exits 0 from **verify**. Its
`claim_verdict` is included in stdout; consumers making subject-level decisions
must read it. Autoany's evaluator/promotion gates remain responsible for rejecting
inconclusive candidates, and `prove-the-negative` remains the signal-grading
entrypoint. A scoped null never establishes global impossibility.

## Review and planning integration

`bstack cross-review` always validates the model result before formatting,
receipt writing and exit-code selection. Invalid output becomes `skipped`, exit
30, and cannot discharge a review gate. A proposed settling observation is kept
separate from evidence that the test already ran. The receipt's host-supplied
head SHA remains alongside the checked result.

Use cross-review/autoreview's claim funnel for empirical claims: ledger, blinded
skeptic/believer, independent raw-source refuter. `fresh_context: true` and a
reference are assertions to corroborate against actual host receipts, not proof
that isolation or inspection occurred. A valid packet does not grant a rubric
pass or change its round budget.

Capability verification returns only `executable_now` IDs, preserving
`request_quote` and `send_out` rows in the input plan. Inspect live tools, input
and output schemas, credentials and authorization before dispatch. A quote tier
does not authorize spending or vendor contact; a send-out does not authorize
messaging another person. Keep the intended experiment when tools are missing.

## Doctor's advisory scope

Doctor reports available/diverged/missing workspace schemas, companion checker
file presence (version and execution remain unverified), and its own validator
probe. `operational_on_fixtures` requires both a valid measurement accepted and a
missing-control refutation rejected. A dead or always-accepting checker remains
`inconclusive`. This probes the schema validator, not an actual target's positive
control. Diagnostics add no pass/gap and do not change `--strict`; `--quiet`
suppresses them. No schema inventory or companion presence result is a subject
verdict.

Implementation context: software verification follow-up to
[broomva/skills#325](https://github.com/broomva/skills/pull/325),
[broomva/autoany#3](https://github.com/broomva/autoany/pull/3), and
[broomva/workspace#1327](https://github.com/broomva/workspace/pull/1327).
These contracts ship independently of those unmerged branches.
