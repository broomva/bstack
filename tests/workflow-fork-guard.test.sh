#!/usr/bin/env bash
# tests/workflow-fork-guard.test.sh — BRO-2542. A fork's code must never run on a
# self-hosted runner.
#
# Invariant, checked over every shipped workflow template: a job whose `runs-on` can
# fall back to a self-hosted runner, in a workflow triggered by pull_request,
# pull_request_target or workflow_run, carries a job-level `if:` that requires the head
# repository to be this repository. agent-evals runs eval files' shell on that runner, and
# ci-triage hands a fork's logs to a model there; the runner is logged in to a
# subscription.
#
# Both arms: the check passes on the shipped templates, and each guard, removed in
# memory, is flagged. A checker that never flags would pass the first arm alone.
#
# There is deliberately NO skip path: missing PyYAML fails the run.
#
# Run from anywhere:
#   bash tests/workflow-fork-guard.test.sh

set -uo pipefail

BSTACK_REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

python3 - "$BSTACK_REPO/references/templates/workflows" <<'PY'
import glob
import os
import sys

import yaml

WF_DIR = sys.argv[1]
GUARDS = {
    "pull_request": "github.event.pull_request.head.repo.full_name == github.repository",
    "pull_request_target": "github.event.pull_request.head.repo.full_name == github.repository",
    "workflow_run": "github.event.workflow_run.head_repository.full_name == github.repository",
}


def triggers(doc):
    on = doc.get(True, doc.get("on"))   # PyYAML reads a bare `on:` key as True
    if isinstance(on, str):
        return {on}
    if isinstance(on, list):
        return set(on)
    return set(on or {})


def problems(name, doc):
    out = []
    risky = triggers(doc) & set(GUARDS)
    for job, spec in (doc.get("jobs") or {}).items():
        if "self-hosted" not in str(spec.get("runs-on", "")):
            continue
        cond = str(spec.get("if") or "")
        for t in sorted(risky):
            if GUARDS[t] not in cond:
                out.append(f"{name}: job {job!r} can run a {t} from a fork on a self-hosted "
                           f"runner; its if: must require `{GUARDS[t]}`")
    return out


passed = failed = 0


def check(desc, ok):
    global passed, failed
    if ok:
        passed += 1
        print(f"  [pass] {desc}")
    else:
        failed += 1
        print(f"  [FAIL] {desc}")


print("── fork guard on self-hosted jobs (BRO-2542) ──────────────────────")
templates = sorted(glob.glob(os.path.join(WF_DIR, "*.yml")))
check("found the workflow templates", len(templates) >= 6)
docs = {os.path.basename(p): yaml.safe_load(open(p)) for p in templates}

found = [p for name, doc in docs.items() for p in problems(name, doc)]
for p in found:
    print(f"         {p}")
check("every self-hosted job reachable from a fork's event requires the head repo", not found)

# The negative arm: every guard the templates carry, removed, must be flagged.
guarded = [(name, job) for name, doc in docs.items()
           for job, spec in (doc.get("jobs") or {}).items()
           if any(g in str(spec.get("if") or "") for g in GUARDS.values())]
check("at least the agent-evals and ci-triage jobs carry a guard",
      {"agent-evals.yml", "ci-triage.yml"} <= {n for n, _ in guarded})
for name, job in guarded:
    doc = yaml.safe_load(open(os.path.join(WF_DIR, name)))
    cond = str(doc["jobs"][job]["if"])
    for g in GUARDS.values():
        cond = cond.replace(g, "true")
    doc["jobs"][job]["if"] = cond
    check(f"{name}: job {job!r} with its guard removed is flagged",
          bool(problems(name, doc)))

print()
print("── Summary ────────────────────────────────────────────────────────")
print(f"  passed: {passed}")
print(f"  failed: {failed}")
sys.exit(1 if failed else 0)
PY
