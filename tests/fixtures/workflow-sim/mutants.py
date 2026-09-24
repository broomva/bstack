#!/usr/bin/env python3
"""Show that tests/workflow-templates.test.sh can fail: revert each fix on a scratch COPY
of its template and confirm the named scenario goes red. The real templates are only read.

    python3 tests/fixtures/workflow-sim/mutants.py

Runs the unmutated templates first (both arms: the baseline must be all green, or a red
mutant proves nothing). Each mutation's pattern must match exactly once, so a template
edit that moves the text makes the mutant fail loudly instead of passing vacuously.
Exit status 0 only when the baseline is green and every mutant is killed by its scenario.
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.realpath(__file__))
REPO = os.path.realpath(os.path.join(HERE, "..", "..", ".."))
WF = os.path.join(REPO, "references", "templates", "workflows")
TEST = os.path.join(REPO, "tests", "workflow-templates.test.sh")

# (label, template, regex, replacement, scenario that must go red)
MUTANTS = [
    ("bands: --state open -> --state all", "bands.yml",
     r'gh pr list --state open --head "bands/\$KEY"',
     'gh pr list --state all --head "bands/$KEY"', "3"),
    ("bands: drop --force", "bands.yml",
     r'push -q --force origin "\$branch"', 'push -q origin "$branch"', "3"),
    ("bands: drop the isCrossRepository filter", "bands.yml",
     r"--json number,isCrossRepository \\\n\s+-q '\[\.\[\] \| select\(\.isCrossRepository \| not\)\] \| length'",
     "--json number -q 'length'", "4"),
    ("bands: old diagnose-cmd, run from the checkout", "bands.yml",
     r'\(cd "\$diag" && python3 "\$RUNNER_TEMP/bstack/scripts/bands\.py" diagnose-cmd "\$bands_abs" '
     r'--intent \.bands/intent\.md --result "\$RUNNER_TEMP/result\.json"\) > "\$RUNNER_TEMP/argv\.json"',
     'python3 "$RUNNER_TEMP/bstack/scripts/bands.py" diagnose-cmd "$BANDS_FILE" '
     '--intent .bands/intent.md --result "$RUNNER_TEMP/result.json" > "$RUNNER_TEMP/argv.json"', "1"),
    ("intent-to-spec: drop --force", "intent-to-spec.yml",
     r'push -q --force origin "\$branch"', 'push -q origin "$branch"', "6"),
    ("intent-to-spec: git ls-remote branch check replaces the open-PR check", "intent-to-spec.yml",
     r'n=\$\(gh pr list --state open --head "spec/\$name" .*?\n.*?\n\s+\[ "\$\{n:-0\}" -gt 0 \] && \{ echo "skip \$f: spec/\$name already has an open PR"; continue; \}',
     'git ls-remote --exit-code --heads origin "spec/$name" >/dev/null 2>&1 '
     '&& { echo "skip $f: spec/$name exists on origin"; continue; }', "6"),
    ("intent-to-spec: old git ls-remote skip in the PR step", "intent-to-spec.yml",
     r'(\n(\s+))git switch -q -C "\$branch" origin/main',
     r'\1git ls-remote --exit-code --heads origin "$branch" >/dev/null 2>&1 && continue'
     r'\1git switch -q -C "$branch" origin/main', "6"),
    ("intent-to-spec: drop < /dev/null", "intent-to-spec.yml",
     r'--output-format json < /dev/null > "\$RUNNER_TEMP/\$name\.json"',
     '--output-format json > "$RUNNER_TEMP/$name.json"', "5"),
    # Two more, so the violation log and the token observation each have a kill on record.
    ("intent-to-spec: restore `gh auth setup-git` (error swallowed)", "intent-to-spec.yml",
     r'(\n(\s+))(while read -r f name; do\n\s+branch="spec/\$name")',
     r'\1gh auth setup-git || true\1\3', "5"),
    ("intent-to-spec: the draft step holds GH_TOKEN", "intent-to-spec.yml",
     r"(\n(\s+)CLAUDE_BIN: \$\{\{ vars\.CLAUDE_BIN \|\| 'claude' \}\})",
     r"\1\n\2GH_TOKEN: ${{ github.token }}", "5"),
]


def run(bands, intent):
    env = dict(os.environ, WF_BANDS=bands, WF_INTENT=intent)
    p = subprocess.run(["bash", TEST], env=env, capture_output=True, text=True)
    out = p.stdout + p.stderr
    verdicts = dict(re.findall(r"^  scenario (\d): (PASS|FAIL)$", out, re.M))
    return p.returncode, verdicts, out


def first_fail(out, scenario):
    block = re.search(rf"── scenario {scenario}:.*?(?=── scenario |\n\s*\[(?:pass|FAIL)\] all |\Z)",
                      out, re.S)
    m = re.search(r"\[FAIL\] (.*)", block.group(0)) if block else None
    return m.group(1) if m else "-"


scratch = tempfile.mkdtemp(prefix="workflow-mutants-")
print(f"scratch: {scratch}")
rc, verdicts, out = run(os.path.join(WF, "bands.yml"), os.path.join(WF, "intent-to-spec.yml"))
baseline_ok = rc == 0 and len(verdicts) == 8 and set(verdicts.values()) == {"PASS"}
print(f"baseline (unmutated): exit {rc}, {verdicts}  -> {'green' if baseline_ok else 'NOT GREEN'}")
if not baseline_ok:
    print(out)
    sys.exit(1)

all_killed = True
print(f"\n{'mutant':<72} {'want':>4}  {'red':<10} verdict   first failing assertion")
for i, (label, tpl, pattern, repl, want) in enumerate(MUTANTS):
    d = os.path.join(scratch, f"m{i}")
    os.makedirs(d)
    for name in ("bands.yml", "intent-to-spec.yml"):
        shutil.copy(os.path.join(WF, name), os.path.join(d, name))
    path = os.path.join(d, tpl)
    text = open(path).read()
    new, n = re.subn(pattern, repl, text)
    if n != 1:
        print(f"{label:<72} {want:>4}  pattern matched {n}x: VACUOUS")
        all_killed = False
        continue
    open(path, "w").write(new)
    rc, verdicts, out = run(os.path.join(d, "bands.yml"), os.path.join(d, "intent-to-spec.yml"))
    red = ",".join(sorted(k for k, v in verdicts.items() if v == "FAIL")) or "none"
    killed = rc != 0 and verdicts.get(want) == "FAIL"
    all_killed &= killed
    print(f"{label:<72} {want:>4}  {red:<10} {'KILLED' if killed else 'SURVIVED':<9} "
          f"{first_fail(out, want)}")
sys.exit(0 if all_killed else 1)
