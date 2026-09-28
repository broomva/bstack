#!/usr/bin/env bash
# tests/linear-backlink.test.sh — BRO-2542. references/templates/workflows/linear-backlink.yml
#
# Behaviour, by running the template's own inline Python (offline: no LINEAR_API_KEY, so it
# never reaches the network):
#   - without LINEAR_ID_PATTERN nothing is linked, and the summary says why;
#   - with a team-key pattern, "SHA-256" in a title is not a ticket, and a lowercase
#     branch ID is.
# Structure: the trigger is pull_request_target, which hands a fork's merged PR the secret,
# so no step may check out or run code. A copy with a checkout step added is flagged, to
# show that the check can fail.
#
# There is deliberately NO skip path: missing PyYAML fails the run.
#
# Run from anywhere:
#   bash tests/linear-backlink.test.sh

set -uo pipefail

BSTACK_REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

python3 - "$BSTACK_REPO/references/templates/workflows/linear-backlink.yml" <<'PY'
import os
import re
import subprocess
import sys
import tempfile

import yaml

WF = sys.argv[1]
doc = yaml.safe_load(open(WF))
passed = failed = 0


def check(desc, ok, detail=""):
    global passed, failed
    passed, failed = (passed + 1, failed) if ok else (passed, failed + 1)
    print(f"  [{'pass' if ok else 'FAIL'}] {desc}" + (f"\n         {detail}" if detail and not ok else ""))


def structure_problems(d):
    out = []
    on = d.get(True, d.get("on")) or {}
    if "pull_request_target" in on:
        for job, spec in (d.get("jobs") or {}).items():
            for st in spec.get("steps") or []:
                uses = str(st.get("uses") or "")
                if uses.startswith("actions/checkout"):
                    out.append(f"job {job}: a checkout under pull_request_target")
                if uses and not uses.startswith("actions/"):
                    out.append(f"job {job}: runs a third-party action under pull_request_target")
    return out


print("── linear-backlink.yml (BRO-2542) ─────────────────────────────────")
check("the trigger is pull_request_target", "pull_request_target" in (doc.get(True) or {}))
check("no step checks out or runs code", not structure_problems(doc))
bad = yaml.safe_load(open(WF))
next(iter(bad["jobs"].values()))["steps"].insert(0, {"uses": "actions/checkout@v4"})
check("a copy with a checkout step added is flagged", bool(structure_problems(bad)))

def pattern_default(d):
    """The ID_PATTERN the step gets when LINEAR_ID_PATTERN is unset: '' is the only safe one."""
    env = next(iter(d["jobs"].values()))["steps"][0].get("env") or {}
    m = re.fullmatch(r"\$\{\{\s*vars\.LINEAR_ID_PATTERN\s*(?:\|\|\s*'([^']*)'\s*)?\}\}",
                     str(env.get("ID_PATTERN", "")))
    return None if m is None else (m.group(1) or "")


check("ID_PATTERN has no default: unset means nothing is linked", pattern_default(doc) == "")
bad = yaml.safe_load(open(WF))
next(iter(bad["jobs"].values()))["steps"][0]["env"]["ID_PATTERN"] = \
    "${{ vars.LINEAR_ID_PATTERN || '[A-Z]{2,5}-[0-9]+' }}"
check("a copy with the old catch-all default is flagged", pattern_default(bad) != "")

run = next(iter(doc["jobs"].values()))["steps"][0]["run"]
m = re.search(r"python3 - <<'PY'\n(.*?)\nPY\s*$", run, re.S)
check("found the inline Python", m is not None)
code = m.group(1) if m else ""


def backlink(**env):
    with tempfile.NamedTemporaryFile("r", suffix=".md") as summary:
        full = {"PATH": os.environ.get("PATH", ""), "GITHUB_STEP_SUMMARY": summary.name,
                "PR_TITLE": "", "PR_BODY": "", "PR_BRANCH": "", "PR_URL": "https://example.invalid/pr/1",
                "PR_NUMBER": "1", "MERGE_SHA": "0" * 40, **env}
        p = subprocess.run([sys.executable, "-c", code], env=full, capture_output=True, text=True,
                           stdin=subprocess.DEVNULL, timeout=30)
        return p.returncode, open(summary.name).read(), p.stderr


rc, out, err = backlink(PR_TITLE="Fix SHA-256 hashing (BRO-12)")
check("no pattern: exit 0, nothing linked, the summary says why",
      rc == 0 and "LINEAR_ID_PATTERN is not set" in out and "BRO-12" not in out, out + err)

rc, out, err = backlink(ID_PATTERN="BRO-[0-9]+", PR_TITLE="Fix SHA-256 hashing (BRO-12)")
check("SHA-256 is not a ticket; BRO-12 is", rc == 0 and "BRO-12" in out and "SHA-256" not in out,
      out + err)

rc, out, err = backlink(ID_PATTERN="BRO-[0-9]+", PR_BRANCH="feature/bro-7-widget")
check("a lowercase branch ID counts", rc == 0 and "BRO-7" in out, out + err)

rc, out, err = backlink(ID_PATTERN="BRO-[0-9]+",
                        PR_BODY="Implements BRO-3.\nIt relates to BRO-99.\nCloses BRO-5")
check("the first body line and a Closes line count; a later citation does not",
      rc == 0 and "BRO-3" in out and "BRO-5" in out and "BRO-99" not in out, out + err)

print()
print("── Summary ────────────────────────────────────────────────────────")
print(f"  passed: {passed}")
print(f"  failed: {failed}")
sys.exit(1 if failed else 0)
PY
