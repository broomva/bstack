"""Mutation proof for the context ledger: each rule it states is load-bearing.

For each mutant: copy scripts/leverage-sensor.py and scripts/context_ledger.py into a
temp dir, remove exactly one rule, and run the one test named to kill it against the
copy (CONTEXT_LEDGER_SCRIPTS). The test MUST fail by ASSERTION: a mutant that does not
compile, or a test that errors, would also go red and prove nothing, so the mutant is
compiled first and the verdict must read `failures=`, never `errors=`. A positive
control runs every killing test against an unmutated copy first, so a red here is the
mutant's doing and not the copy's.

The first mutant is the one the rule was written for: count a hook_success record's
`stdout` on top of its `content`, which bills every role-x turn twice. The rest remove
one rule each: twin pairing and its turn scope, fork skipping, the Stop exclusion, the
four ways prose could be read as a use, slug matching, ordering, both liveness states,
harness catalogs as pointers, error containment, the brief exclusion and subagent scope.

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
L, S = "context_ledger.py", "leverage-sensor.py"
MUTANTS = (
    ("dedup: stdout billed on top of content", L,
     '        return content, "content"\n',
     '        return content + (att.get("stdout") or ""), "content"\n',
     "Sources.test_role_x_intake_content_and_stdout_counted_once"),
    ("dedup: JSON additionalContext and its twin both billed", L,
     "                twin_of[j], twin_of[k] = k, j\n",
     "                pass\n",
     "Sources.test_ctx_board_json_and_its_twin_counted_once"),
    ("twin pairing crosses turns", L,
     '                key = (c["turn"], c["event"], c["text"].strip())\n',
     '                key = (c["event"], c["text"].strip())\n',
     "Sources.test_pairing_does_not_cross_turns"),
    ("fork copies billed again", L,
     '        if obj.get("forkedFrom"):\n',
     "        if False:\n",
     "Sources.test_fork_copies_are_skipped"),
    ("Stop stdout billed as injected", L,
     'PLAIN_STDOUT_EVENTS = frozenset({"SessionStart", "UserPromptSubmit"})\n',
     'PLAIN_STDOUT_EVENTS = frozenset({"SessionStart", "UserPromptSubmit", "Stop"})\n',
     "Sources.test_stop_stdout_is_recorded_not_injected"),
    ("assistant text read as an action", L,
     '                if isinstance(it, dict) and it.get("type") == "tool_use":\n',
     '                if isinstance(it, dict) and it.get("type") == "text":\n'
     '                    self._on_tool_use("Bash", {"command": it.get("text") or ""}, idx, ts)\n'
     '                if isinstance(it, dict) and it.get("type") == "tool_use":\n',
     "NoProse.test_ledger_is_invariant_under_prose_replacement"),
    ("Agent prompt read as an action", L,
     '        if name == "Read":\n',
     '        if name == "Agent":\n'
     '            self._on_tool_use("Bash", {"command": str(inp.get("prompt") or "")}, idx, ts)\n'
     '        if name == "Read":\n',
     "NoProse.test_prose_naming_a_pointer_is_not_follow_through"),
    ("kg load matched anywhere in a command", L,
     "            for seg in shell_segments(cmd):\n",
     '            for m in re.finditer(r"kg(?:\\.py)?\\s+load\\s+(\\S+)", cmd):\n'
     "                self._kg_load([m.group(1)], idx, ts)\n"
     "            for seg in shell_segments(cmd):\n",
     "NoProse.test_commit_message_is_not_a_use"),
    ("Grep pattern read for ids", L,
     '            self._read_path(str(inp.get("path") or ""), idx, ts)\n        elif name == "Glob":\n',
     '            self._read_path(str(inp.get("path") or "") + " " + str(inp.get("pattern") or ""), idx, ts)\n'
     '        elif name == "Glob":\n',
     "NoProse.test_grep_pattern_is_not_a_use"),
    ("every MCP field read, prose included", L,
     '                if isinstance(v, str) and k.lower().endswith("id"):\n',
     "                if isinstance(v, str):\n",
     "NoProse.test_mcp_prose_field_is_not_a_use"),
    ("a bare query word names a one-word slug", L,
     '                    ("-" in slug and slug in toks)\n',
     "                    (slug in toks)\n",
     "FollowThrough.test_single_word_slug_needs_its_type"),
    ("an open BEFORE the injection counts as follow-through", L,
     "        if u and later(u[1], u[2]):\n",
     "        if u:\n",
     "FollowThrough.test_open_before_injection_is_not_follow_through"),
    ("a ledger with no billed byte reports live", L,
     "    elif total == 0:\n",
     "    elif False:\n",
     "Liveness.test_sessions_with_no_injected_byte_are_blind"),
    ("a source gone dark reports live", L,
     "    elif unbilled or unknown_event:\n",
     "    elif False:\n",
     "Liveness.test_partial_when_a_shown_record_bills_nothing"),
    ("a harness catalog counts as a pointer", L,
     '    if source.startswith("harness:"):\n        return found, False\n',
     "",
     "Reflexes.test_harness_listing_is_not_a_pointer"),
    ("a broken ledger takes the sensor down", S,
     "            files, kg_read_re, bash_read_targets, iter_lines, subagent_files)\n    except Exception as e:\n",
     "            files, kg_read_re, bash_read_targets, iter_lines, subagent_files)\n    except ZeroDivisionError as e:\n",
     "Cli.test_a_broken_ledger_is_an_error_and_spares_m1_to_m6"),
    ("the brief prints calibrating rows", S,
     "                and not (brief and is_calibrating(r))]\n",
     "                ]\n",
     "Cli.test_brief_leaves_calibrating_rows_out"),
    ("subagent transcripts left out", S,
     "        ledger = context_ledger_block(files, kg_read_re,\n"
     "                                      window_files(subagent_glob(glob_pat), window))\n",
     "        ledger = context_ledger_block(files, kg_read_re)\n",
     "Cli.test_subagent_transcripts_are_found"),
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
