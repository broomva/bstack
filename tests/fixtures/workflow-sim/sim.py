#!/usr/bin/env python3
"""Run one GitHub Actions workflow job locally, the way a runner would, against fakes.

    sim.py <workflow.yml> <simdir> <checkout> [NAME=VALUE ...]

NAME=VALUE supplies an expression the job reads: `vars.X=...`, `github.event.before=...`,
`github.base_ref=...`. Used by tests/workflow-templates.test.sh (BRO-2542).

What it does, per step, in order:
  - `uses:` steps are skipped: the harness made the checkout (<checkout>) itself.
  - the step that clones broomva/bstack is replaced by a symlink $RUNNER_TEMP/bstack ->
    this repo, so the templates run against the scripts in this working tree. Exactly one
    such step must exist; a template that fetches bstack another way fails the run.
  - `if:` is evaluated over steps.*.outputs.*, vars.*, success()/failure()/always().
  - `${{ }}` expressions in env and run are substituted. One the simulator does not know
    fails the run rather than passing through as literal text.
  - `run:` is executed as GitHub's default shell does: bash --noprofile --norc -eo pipefail,
    with a fresh GITHUB_OUTPUT per step and stdin from /dev/null.

The environment is built from scratch, never inherited: HOME is $SIM/home (so
$HOME/.local/bin/claude is the fake), fakebin/ leads PATH (so `gh` and `claude` resolve
to fakes), and no token reaches a step unless the template's own env puts it there.
CI exports GH_TOKEN to every test; inheriting it would make "the diagnosis step holds no
token" unobservable.

Writes $SIM/job.json: {"result", "runner_temp", "outputs", "steps": [...]}.
Exit status: 0 when the job succeeded, 1 when it failed, 2 on a simulator error.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

import yaml

HERE = os.path.dirname(os.path.realpath(__file__))
REPO = os.path.realpath(os.path.join(HERE, "..", "..", ".."))
FAKEBIN = os.path.join(HERE, "fakebin")


def die(msg):
    print(f"sim: {msg}", file=sys.stderr)
    sys.exit(2)


if len(sys.argv) < 4:
    die(__doc__.strip().splitlines()[2].strip())
wf_path, sim, checkout = (os.path.realpath(a) for a in sys.argv[1:4])
given = {}
for a in sys.argv[4:]:
    if "=" not in a:
        die(f"expected NAME=VALUE, got {a!r}")
    k, v = a.split("=", 1)
    given[k] = v

wf = yaml.safe_load(open(wf_path))
jobs = wf.get("jobs") or {}
if len(jobs) != 1:
    die(f"{wf_path}: expected exactly one job, found {len(jobs)}")
job = next(iter(jobs.values()))

outputs = {}
EXPR = re.compile(r"\$\{\{\s*(.*?)\s*\}\}")
KNOWN = {
    "github.token": "FAKE-GITHUB-TOKEN",
    "github.repository": "owner/repo",
    "github.event.before": given.get("github.event.before", ""),
    "github.base_ref": given.get("github.base_ref", "main"),
}


def lookup(name):
    m = re.fullmatch(r"steps\.([\w-]+)\.outputs\.([\w-]+)", name)
    if m:
        return outputs.get(f"{m.group(1)}.{m.group(2)}", "")
    if name.startswith("vars."):
        return given.get(name, "")
    if name in KNOWN:
        return KNOWN[name]
    die(f"unknown expression {name!r}")


def value(expr):
    """A `${{ }}` body: a name, or `name || 'default'` (GitHub's or returns an operand)."""
    m = re.fullmatch(r"([\w.-]+)\s*\|\|\s*'([^']*)'", expr)
    if m:
        return lookup(m.group(1)) or m.group(2)
    if re.fullmatch(r"[\w.-]+", expr):
        return lookup(expr)
    die(f"unsupported expression {expr!r}")


def sub(text):
    return EXPR.sub(lambda m: str(value(m.group(1))), str(text))


def truthy(cond, failed):
    """Evaluate an `if:`. Names become literals; anything left over is a NameError."""
    c = EXPR.sub(lambda m: m.group(1), str(cond)).strip()
    status_fn = re.search(r"\b(success|failure|always|cancelled)\(\)", c)
    # Operators first, while the text holds only the template's own tokens.
    c = c.replace("||", " or ").replace("&&", " and ")
    c = re.sub(r"!(?!=)", " not ", c)
    c = re.sub(r"\bsuccess\(\)", repr(not failed), c)
    c = re.sub(r"\bfailure\(\)", repr(failed), c)
    c = re.sub(r"\balways\(\)", "True", c)
    c = re.sub(r"\bcancelled\(\)", "False", c)
    c = re.sub(r"\b(steps\.[\w-]+\.outputs\.[\w-]+|vars\.\w+)",
               lambda m: repr(lookup(m.group(1))), c)
    try:
        ok = bool(eval(c, {"__builtins__": {}}, {"True": True, "False": False}))
    except Exception as e:  # noqa: BLE001 - any failure here is a simulator gap
        die(f"cannot evaluate if: {cond!r} ({e})")
    # Like GitHub: without a status function, a step runs only while the job succeeds.
    return ok if status_fn else ok and not failed


def yaml_python_path():
    """PyYAML's directory, so python3 in a step finds it after HOME is replaced."""
    return os.path.dirname(os.path.dirname(os.path.realpath(yaml.__file__)))


home = os.path.join(sim, "home")
os.makedirs(os.path.join(home, ".local", "bin"), exist_ok=True)
fake_claude = os.path.join(home, ".local", "bin", "claude")
if not os.path.lexists(fake_claude):
    os.symlink(os.path.join(FAKEBIN, "claude"), fake_claude)

rt = tempfile.mkdtemp(prefix="rt-", dir=sim)
base = {
    "HOME": home,
    "XDG_CONFIG_HOME": os.path.join(home, ".config"),
    "PATH": FAKEBIN + os.pathsep + os.environ.get("PATH", "/usr/bin:/bin"),
    "SIM": sim,
    "RUNNER_TEMP": rt,
    "GITHUB_STEP_SUMMARY": os.path.join(sim, "summary.md"),
    "GITHUB_ACTIONS": "true",
    "CI": "true",
    "LANG": "C.UTF-8",
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_TERMINAL_PROMPT": "0",
    "PYTHONPATH": yaml_python_path(),
    "PYTHONDONTWRITEBYTECODE": "1",
}
bash = shutil.which("bash", path=base["PATH"]) or die("no bash on PATH")
# The templates run `export PATH="$HOME/.local/bin:$PATH"` and then `claude`. Whichever
# way it resolves, it must be the fake: a real claude on this machine is never reachable
# by name.
for path in (base["PATH"], os.path.join(home, ".local", "bin") + os.pathsep + base["PATH"]):
    for tool in ("claude", "gh"):
        found = shutil.which(tool, path=path)
        if not found or os.path.realpath(found) != os.path.join(FAKEBIN, tool):
            die(f"`{tool}` resolves to {found!r}, not the fake in {FAKEBIN}")

job_if = job.get("if")
steps, failed, fetched = [], False, 0
if job_if is not None and not truthy(job_if, False):
    result = "skipped"
else:
    base.update({k: sub(v) for k, v in (job.get("env") or {}).items()})
    for i, s in enumerate(job.get("steps") or []):
        name = s.get("name") or s.get("uses") or f"step {i}"
        rec = {"name": name, "id": s.get("id", "")}
        steps.append(rec)
        if "run" not in s:
            rec["outcome"] = "skipped (uses)"
            continue
        if "github.com/broomva/bstack" in s["run"]:
            fetched += 1
            os.symlink(REPO, os.path.join(rt, "bstack"))
            rec["outcome"] = "replaced (bstack -> this working tree)"
            continue
        runs = truthy(s["if"], failed) if "if" in s else not failed
        if not runs:
            rec["outcome"] = "skipped"
            print(f"[skip] {name}")
            continue
        env = dict(base)
        env.update({k: sub(v) for k, v in (s.get("env") or {}).items()})
        ghout = os.path.join(rt, f"output-{i}")
        open(ghout, "w").close()
        env["GITHUB_OUTPUT"] = ghout
        script = os.path.join(rt, f"step-{i}.sh")
        open(script, "w").write(sub(s["run"]))
        try:
            p = subprocess.run([bash, "--noprofile", "--norc", "-eo", "pipefail", script],
                               cwd=checkout, env=env, capture_output=True, text=True,
                               stdin=subprocess.DEVNULL, timeout=300)
            rc, out, err = p.returncode, p.stdout, p.stderr
        except subprocess.TimeoutExpired as e:
            rc, out, err = 124, str(e.stdout or ""), f"timed out: {e}"
        for line in open(ghout):
            if re.match(r"[\w-]+<<", line):
                die(f"step {name!r}: multi-line GITHUB_OUTPUT is not modelled")
            if "=" in line:
                k, v = line.rstrip("\n").split("=", 1)
                outputs[f"{rec['id']}.{k}"] = v
        rec.update(outcome="success" if rc == 0 else "failure", rc=rc, stdout=out, stderr=err)
        print(f"[{rc}] {name}")
        for label, text in (("out", out), ("err", err)):
            for ln in text.strip().splitlines()[-15:]:
                print(f"    {label}: {ln}")
        if rc != 0:
            failed = True
    if fetched != 1:
        die(f"expected exactly one step fetching broomva/bstack, found {fetched}")
    result = "failure" if failed else "success"

json.dump({"result": result, "runner_temp": rt, "outputs": outputs, "steps": steps},
          open(os.path.join(sim, "job.json"), "w"), indent=1)
print(f"job: {result}")
sys.exit(1 if result == "failure" else 0)
