"""Mutation proof for the context ledger: each rule it states is load-bearing.

For each mutant: copy scripts/leverage-sensor.py and scripts/context_ledger.py into a
temp dir, remove exactly one rule, and run the one test named to kill it against the
copy (CONTEXT_LEDGER_SCRIPTS). The test MUST fail by ASSERTION: a mutant that does not
compile, or a test that errors, would also go red and prove nothing, so the mutant is
compiled first and the verdict must read `failures=`, never `errors=`. A positive
control runs every killing test against an unmutated copy first, so a red here is the
mutant's doing and not the copy's.

The first mutant is the one the rule was written for: count a hook_success record's
`stdout` on top of its `content`, which bills every role-x turn twice.

    python3 tests/context_ledger_mutation.py
"""

import os
import py_compile
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
FILES = ("leverage-sensor.py", "context_ledger.py")

# (label, file, anchor, replacement, killing test)
MUTANTS = (
    ("dedup: stdout billed on top of content", "context_ledger.py",
     '        return content, "content"\n',
     '        return content + (att.get("stdout") or ""), "content"\n',
     "Sources.test_role_x_intake_content_and_stdout_counted_once"),
    ("dedup: JSON additionalContext and its twin both billed", "context_ledger.py",
     "        if self._pending[other].get(key, 0) > 0:\n",
     "        if False:\n",
     "Sources.test_ctx_board_json_and_its_twin_counted_once"),
    ("fork copies billed again", "context_ledger.py",
     '        if obj.get("forkedFrom"):\n',
     "        if False:\n",
     "Sources.test_fork_copies_are_skipped"),
    ("Stop stdout billed as injected", "context_ledger.py",
     'PLAIN_STDOUT_EVENTS = frozenset({"SessionStart", "UserPromptSubmit"})\n',
     'PLAIN_STDOUT_EVENTS = frozenset({"SessionStart", "UserPromptSubmit", "Stop"})\n',
     "Sources.test_stop_stdout_is_recorded_not_injected"),
    ("assistant text read as an action", "context_ledger.py",
     '                if isinstance(it, dict) and it.get("type") == "tool_use":\n',
     '                if isinstance(it, dict) and it.get("type") == "text":\n'
     '                    s.on_tool_use("Bash", {"command": it.get("text") or ""}, idx)\n'
     '                if isinstance(it, dict) and it.get("type") == "tool_use":\n',
     "NoProse.test_ledger_is_invariant_under_prose_replacement"),
    ("Agent prompt read as an action", "context_ledger.py",
     '        if name == "Read":\n',
     '        if name == "Agent":\n'
     '            self.on_tool_use("Bash", {"command": str(inp.get("prompt") or "")}, idx)\n'
     '        if name == "Read":\n',
     "NoProse.test_prose_naming_a_pointer_is_not_follow_through"),
    ("an open BEFORE the injection counts as follow-through", "context_ledger.py",
     "        if u and u[1] > first:\n",
     "        if u:\n",
     "FollowThrough.test_open_before_injection_is_not_follow_through"),
    ("a ledger with no injected byte reports live", "context_ledger.py",
     "    elif total_bytes == 0:\n",
     "    elif False:\n",
     "Liveness.test_sessions_with_no_injected_byte_are_blind"),
    ("a harness catalog counts as a pointer", "context_ledger.py",
     '        if not source.startswith("harness:"):\n',
     "        if True:\n",
     "Reflexes.test_harness_listing_is_not_a_pointer"),
    ("a broken ledger takes the sensor down", "leverage-sensor.py",
     "            files, kg_read_re, bash_read_targets, iter_lines)\n    except Exception as e:\n",
     "            files, kg_read_re, bash_read_targets, iter_lines)\n    except ZeroDivisionError as e:\n",
     "Cli.test_a_broken_ledger_is_an_error_and_spares_m1_to_m6"),
)


def run(scripts, test):
    env = dict(os.environ, CONTEXT_LEDGER_SCRIPTS=str(scripts), PYTHONDONTWRITEBYTECODE="1")
    r = subprocess.run([sys.executable, "-m", "unittest", f"tests.test_context_ledger.{test}"],
                       cwd=str(REPO), env=env, capture_output=True, text=True, timeout=300)
    verdict = re.findall(r"^(OK.*|FAILED \(.*\))$", r.stderr, re.MULTILINE)
    return r.returncode, verdict[-1] if verdict else "<no verdict>"


def copy_scripts(dest):
    dest.mkdir()
    for f in FILES:
        shutil.copy(REPO / "scripts" / f, dest / f)
    return dest


def main():
    passed = failed = 0

    def report(ok, label):
        nonlocal passed, failed
        print(f"  [{'pass' if ok else 'FAIL'}] {label}")
        if ok:
            passed += 1
        else:
            failed += 1

    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        pristine = copy_scripts(tmp / "pristine")
        for _, _, _, _, test in MUTANTS:
            rc, verdict = run(pristine, test)
            report(rc == 0 and verdict == "OK", f"positive control: {test} passes on an unmutated copy")
        for i, (label, fname, anchor, repl, test) in enumerate(MUTANTS):
            d = copy_scripts(tmp / f"m{i}")
            text = (d / fname).read_text()
            if text.count(anchor) != 1:
                report(False, f"{label}: anchor found {text.count(anchor)}x in {fname} (expected 1)")
                continue
            (d / fname).write_text(text.replace(anchor, repl))
            try:
                py_compile.compile(str(d / fname), cfile=str(tmp / f"m{i}.pyc"), doraise=True)
            except py_compile.PyCompileError as e:
                report(False, f"{label}: the mutant does not compile ({e.msg.splitlines()[-1]})")
                continue
            rc, verdict = run(d, test)
            report(rc != 0 and verdict == "FAILED (failures=1)",
                   f"mutant killed — {label} → {test} fails by assertion ({verdict})")
    print(f"\n  {passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
