#!/usr/bin/env bash
# tests/workflow-fork-guard.test.sh — BRO-2542. A fork's pull request must not run a job
# that can fall back to a self-hosted runner.
#
# Scope: jobs whose `runs-on` names `self-hosted` as its default, in workflows triggered by
# pull_request, pull_request_target or workflow_run. agent-evals is one: it runs eval
# files' setup, reference and command checks as shell. ci-triage is another: it hands a
# failed run's logs to a model. Both use a runner logged in to a subscription. Jobs that
# default to a GitHub-hosted runner (sdlc-gates, linear-backlink) are out of scope: they
# run bstack's scripts over a fork's files, not the fork's commands.
#
# The check EVALUATES each in-scope job's `if:`:
#   - it must be false for an event whose head is a fork;
#   - it must be true for the same event from this repo, so the job still runs.
# A name the evaluator does not know fails the check. It never guesses.
#
# Both arms: the shipped templates pass, and every guard, weakened in memory in each way a
# review found (`always() || guard`, `github.event_name == '...' || guard`, the guard
# removed), is flagged.
#
# Second invariant, same templates: a step that runs bstack's own code (anything under
# $RUNNER_TEMP/bstack/) holds no token in its env. bstack is fetched by tag, so its code
# must not be able to act on the repository; only steps that talk to GitHub hold one.
# A step sees the workflow's and the job's env as well as its own. The negative arm
# adds GH_TOKEN to each such step, and to its job, and expects both flagged.
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
import re
import sys

import yaml

WF_DIR = sys.argv[1]
REPO, FORK = "owner/repo", "someone/repo"
HEAD = {
    "pull_request": "github.event.pull_request.head.repo.full_name",
    "pull_request_target": "github.event.pull_request.head.repo.full_name",
    "workflow_run": "github.event.workflow_run.head_repository.full_name",
}


def context(event, head):
    ctx = {"github.event_name": event, "github.repository": REPO, HEAD[event]: head}
    if event == "workflow_run":
        ctx["github.event.workflow_run.conclusion"] = "failure"
    return ctx


def evaluate(cond, ctx):
    """GitHub's `if:` over a fixed context. Raises KeyError on an unknown name."""
    c = str(cond if cond is not None else "").strip()
    if c.startswith("${{") and c.endswith("}}"):
        c = c[3:-2].strip()
    if not c:
        return True
    out = []
    for part in re.split(r"('[^']*')", c):
        if part.startswith("'"):
            out.append(repr(part[1:-1]))
            continue
        part = part.replace("||", " or ").replace("&&", " and ")
        part = re.sub(r"!(?!=)", " not ", part)
        part = re.sub(r"\b(success|always)\(\)", " True ", part)
        part = re.sub(r"\b(failure|cancelled)\(\)", " False ", part)

        def name(m):
            n = m.group(0)
            if n in ("and", "or", "not", "True", "False"):
                return n
            if n.startswith("vars."):
                return repr("")
            if n in ctx:
                return repr(ctx[n])
            raise KeyError(n)

        out.append(re.sub(r"[A-Za-z_][\w.-]*", name, part))
    return bool(eval("".join(out), {"__builtins__": {}}, {}))


def triggers(doc):
    on = doc.get(True, doc.get("on"))   # PyYAML reads a bare `on:` key as True
    if isinstance(on, str):
        return {on}
    return set(on or [])


def problems(name, doc):
    out = []
    for job, spec in (doc.get("jobs") or {}).items():
        if "'self-hosted'" not in str(spec.get("runs-on", "")):
            continue
        for event in sorted(triggers(doc) & set(HEAD)):
            try:
                from_fork = evaluate(spec.get("if"), context(event, FORK))
                from_repo = evaluate(spec.get("if"), context(event, REPO))
            except KeyError as e:
                out.append(f"{name}: job {job!r}: `if:` uses {e.args[0]!r}, unknown here")
                continue
            if from_fork:
                out.append(f"{name}: job {job!r} runs a {event} from a fork on a self-hosted runner")
            if not from_repo:
                out.append(f"{name}: job {job!r} never runs a {event} from this repo")
    return out


passed = failed = 0


def check(desc, ok):
    global passed, failed
    passed, failed = (passed + 1, failed) if ok else (passed, failed + 1)
    print(f"  [{'pass' if ok else 'FAIL'}] {desc}")


print("── fork guard on self-hosted jobs (BRO-2542) ──────────────────────")
docs = {os.path.basename(p): yaml.safe_load(open(p))
        for p in sorted(glob.glob(os.path.join(WF_DIR, "*.yml")))}
check("found the workflow templates", len(docs) >= 6)

found = [p for name, doc in docs.items() for p in problems(name, doc)]
for p in found:
    print(f"         {p}")
check("no in-scope job runs a fork's event; each still runs this repo's", not found)

in_scope = [(name, job) for name, doc in docs.items()
            for job, spec in (doc.get("jobs") or {}).items()
            if "'self-hosted'" in str(spec.get("runs-on", "")) and triggers(doc) & set(HEAD)]
check("agent-evals and ci-triage are in scope",
      {"agent-evals.yml", "ci-triage.yml"} <= {n for n, _ in in_scope})

# The negative arm: each way a guard has been, or could be, weakened.
for name, job in in_scope:
    base = str(docs[name]["jobs"][job]["if"])
    guard = next(re.escape(h) + r"\s*==\s*github\.repository" for h in HEAD.values() if h in base)
    event = sorted(triggers(docs[name]) & set(HEAD))[0]
    weakened = {
        "always() || guard": f"always() || {base}",
        f"github.event_name == '{event}' || guard": f"github.event_name == '{event}' || {base}",
        "the guard removed": re.sub(guard, "true", base),
    }
    for how, cond in weakened.items():
        doc = yaml.safe_load(open(os.path.join(WF_DIR, name)))
        doc["jobs"][job]["if"] = cond
        check(f"{name}: job {job!r} with {how} is flagged", bool(problems(name, doc)))


TOKEN_RE = re.compile(r"github\.token|secrets\.")


def token_problems(name, doc):
    out = []
    wf_env = doc.get("env") or {}
    for job, spec in (doc.get("jobs") or {}).items():
        for i, st in enumerate(spec.get("steps") or []):
            if "$RUNNER_TEMP/bstack/" not in str(st.get("run") or ""):
                continue
            # A step sees the workflow's and the job's env too.
            env = {**wf_env, **(spec.get("env") or {}), **(st.get("env") or {})}
            held = [k for k, v in env.items() if TOKEN_RE.search(str(v))]
            if held:
                out.append(f"{name}: job {job!r} step {st.get('name', i)!r} runs bstack's code "
                           f"holding {', '.join(held)}")
    return out


print()
print("── bstack's code runs with no token ───────────────────────────────")
found = [p for name, doc in docs.items() for p in token_problems(name, doc)]
for p in found:
    print(f"         {p}")
bstack_steps = [(name, job, i) for name, doc in docs.items()
                for job, spec in (doc.get("jobs") or {}).items()
                for i, st in enumerate(spec.get("steps") or [])
                if "$RUNNER_TEMP/bstack/" in str(st.get("run") or "")]
check("found the steps that run bstack's code", len(bstack_steps) >= 6)
check("none of them holds a token", not found)
for name, job, i in bstack_steps:
    doc = yaml.safe_load(open(os.path.join(WF_DIR, name)))
    st = doc["jobs"][job]["steps"][i]
    st.setdefault("env", {})["GH_TOKEN"] = "${{ github.token }}"
    check(f"{name}: step {st.get('name', i)!r} given GH_TOKEN is flagged",
          bool(token_problems(name, doc)))
    doc = yaml.safe_load(open(os.path.join(WF_DIR, name)))
    doc["jobs"][job].setdefault("env", {})["GH_TOKEN"] = "${{ github.token }}"
    check(f"{name}: job {job!r} given a job-level GH_TOKEN is flagged",
          bool(token_problems(name, doc)))

print()
print("── Summary ────────────────────────────────────────────────────────")
print(f"  passed: {passed}")
print(f"  failed: {failed}")
sys.exit(1 if failed else 0)
PY
