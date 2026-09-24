"""Tests for scripts/agent_evals.py (BRO-2542). stdlib unittest only.

No test calls a model or the network. `claude` is a fake executable written into each
test's temp dir (tests/fixtures/agent-evals/fake_claude.py behind a shell wrapper); what
it does is steered by the eval's prompt. Every test builds its own git repository, and
the scratch checkouts land inside the test's temp dir, so the real repo is never read
or written.

Each check type is tested where it fires and where it passes: a checker that fails
everything would pass the first half and fail the second.
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))
import agent_evals as ae  # noqa: E402

FAKE = REPO / "tests" / "fixtures" / "agent-evals" / "fake_claude.py"
BAD = REPO / "tests" / "fixtures" / "agent-evals" / "invalid"
EXAMPLE = REPO / "references" / "templates" / "eval.example.json"

_CLEAN_ENV = {k: v for k, v in os.environ.items()
              if k in ("PATH", "HOME", "LANG", "LC_ALL", "TMPDIR")}


def git(cwd, *args) -> str:
    p = subprocess.run(["git", "-c", "core.fsmonitor=false", "-c", "core.hooksPath=/dev/null",
                        "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args],
                       cwd=str(cwd), env=_CLEAN_ENV, stdin=subprocess.DEVNULL,
                       capture_output=True, text=True, check=True)
    return p.stdout


def run_main(*argv) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = ae.main([str(a) for a in argv])
    return rc, out.getvalue(), err.getvalue()


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(os.path.realpath(tempfile.mkdtemp(prefix="ae-test-")))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        # Scratch worktrees land here, so leftovers are visible and never escape.
        (self.tmp / "t").mkdir()
        patcher = mock.patch.object(tempfile, "tempdir", str(self.tmp / "t"))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.repo = self.tmp / "live"
        self.repo.mkdir()
        git(self.repo, "init", "-q", "-b", "main")
        (self.repo / ".control").mkdir()
        (self.repo / ".control" / "policy.yaml").write_text("gates: [g1]\n")
        (self.repo / "README.md").write_text("readme\n")
        (self.repo / "NOTES.md").write_text("notes\n")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "init")
        # The live tree is dirty on purpose: the scratch must come from HEAD, and the
        # dirt must survive a run byte-for-byte.
        (self.repo / "README.md").write_text("readme\nlocal edit\n")
        (self.repo / "untracked.txt").write_text("mine\n")
        self.evals = self.tmp / "evals"
        self.evals.mkdir()
        self.log = self.tmp / "claude.log"
        bindir = self.tmp / "bin"
        bindir.mkdir()
        self.claude = bindir / "claude"
        self.claude.write_text(
            "#!/bin/sh\n"
            f"FAKE_CLAUDE_LOG='{self.log}'; export FAKE_CLAUDE_LOG\n"
            f"exec '{sys.executable}' '{FAKE}' \"$@\"\n")
        self.claude.chmod(0o755)

    # -- helpers -----------------------------------------------------------
    def write_eval(self, eid: str, prompt: str, checks: list, **extra) -> Path:
        ev = {"id": eid, "description": "d", "source": "test", "prompt": prompt,
              "allowed_tools": ["Read", "Edit"], "checks": checks, **extra}
        p = self.evals / f"{eid}.json"
        p.write_text(json.dumps(ev))
        return p

    def run_evals(self, *extra) -> tuple[int, dict, str]:
        out = self.tmp / "summary.json"
        rc, stdout, stderr = run_main("run", self.evals, "--repo", self.repo,
                                      "--claude", self.claude, "--json", out, *extra)
        summary = json.loads(out.read_text()) if out.exists() else {}
        if out.exists():
            out.unlink()
        return rc, summary, stdout + stderr

    def one(self, prompt: str, checks: list, **extra) -> dict:
        self.write_eval("e", prompt, checks, **extra)
        rc, s, out = self.run_evals()
        self.assertEqual(rc, 0, out)
        self.assertEqual(s["ran"], 1)
        return s["results"][0]

    def calls(self) -> list[dict]:
        if not self.log.exists():
            return []
        return [json.loads(x) for x in self.log.read_text().splitlines()]

    def model_calls(self) -> list[dict]:
        return [c for c in self.calls() if c["argv"][:1] != ["--version"]]

    def snapshot(self) -> dict:
        files = {}
        for p in sorted(self.repo.rglob("*")):
            if ".git" in p.relative_to(self.repo).parts or not p.is_file():
                continue
            files[str(p.relative_to(self.repo))] = hashlib.sha256(p.read_bytes()).hexdigest()
        return {
            "status": git(self.repo, "status", "--porcelain=v1", "--untracked-files=all"),
            "head": git(self.repo, "rev-parse", "HEAD"),
            "branch": git(self.repo, "symbolic-ref", "HEAD"),
            "refs": git(self.repo, "for-each-ref"),
            "worktrees": git(self.repo, "worktree", "list", "--porcelain"),
            "config": hashlib.sha256((self.repo / ".git" / "config").read_bytes()).hexdigest(),
            "objects": git(self.repo, "count-objects", "-v"),
            "files": files,
        }

    def scratch_dirs(self) -> list[Path]:
        return list((self.tmp / "t").glob("bstack-eval-*"))


# ---------------------------------------------------------------------------
# validate
# ---------------------------------------------------------------------------

class TestValidate(Base):
    def assertProblem(self, path, needle):
        rc, out, _ = run_main("validate", path)
        self.assertEqual(rc, 1, out)
        self.assertIn(needle, out)

    def test_example_template_validates(self):
        rc, out, _ = run_main("validate", EXAMPLE)
        self.assertEqual(rc, 0, out)
        self.assertIn("0 problem(s)", out)

    def test_invalid_fixtures_each_report_their_file_and_problem(self):
        rc, out, _ = run_main("validate", BAD)
        self.assertEqual(rc, 1)
        expected = {
            "missing-key.json": "missing required key 'source'",
            "unknown-check.json": "unknown check type 'file_exists'",
            "bad-regex.json": "does not compile",
            "empty-checks.json": "'checks' is empty",
            "typo-key.json": "unknown key 'setpu'",
            "unsafe-path.json": "must not contain '..'",
            "dash-prompt.json": "must not start with '-'",
            "not-json.json": "invalid JSON",
            "dup-a.json": None,
            "dup-b.json": "duplicate id 'dup'",
        }
        for name, needle in expected.items():
            if needle:
                with self.subTest(fixture=name):
                    self.assertIn(f"{BAD / name}: ", out)
                    line = [x for x in out.splitlines() if f"{name}:" in x]
                    self.assertTrue(any(needle in x for x in line), (name, line))
        # dup-a is well-formed on its own: only its twin is flagged
        self.assertNotIn(f"ERROR {BAD / 'dup-a.json'}:", out)

    def test_each_invalid_fixture_alone_is_rejected(self):
        for f in sorted(BAD.glob("*.json")):
            if f.name == "dup-a.json":
                continue
            with self.subTest(fixture=f.name):
                if f.name == "dup-b.json":
                    # alone, dup-b is valid — duplication is a property of the set
                    self.assertEqual(run_main("validate", f)[0], 0)
                else:
                    self.assertEqual(run_main("validate", f)[0], 1)

    def test_check_missing_field_unknown_field_and_bool_exit(self):
        self.write_eval("x", "p", [{"type": "file_contains", "path": "a"},
                                   {"type": "output_regex", "regex": "a", "extra": 1},
                                   {"type": "command", "run": "true", "expect_exit": True}])
        rc, out, _ = run_main("validate", self.evals)
        self.assertEqual(rc, 1)
        self.assertIn("(file_contains): missing field 'regex'", out)
        self.assertIn("(output_regex): unknown field 'extra'", out)
        self.assertIn("'expect_exit' must be an integer", out)

    def test_absolute_and_dot_git_paths_rejected(self):
        self.write_eval("x", "p", [{"type": "file_absent", "path": "/etc/passwd"},
                                   {"type": "file_unchanged", "path": ".git/config"}])
        rc, out, _ = run_main("validate", self.evals)
        self.assertEqual(rc, 1)
        self.assertIn("must be relative", out)
        self.assertIn("is inside .git", out)

    def test_allowed_tools_entry_with_comma_rejected(self):
        self.write_eval("x", "p", [{"type": "output_regex", "regex": "a"}],
                        allowed_tools=["Read,Write"])
        self.assertProblem(self.evals, "contains a comma")

    def test_empty_directory_is_a_problem_not_a_pass(self):
        self.assertProblem(self.evals, "no *.json eval files")

    def test_missing_path_is_usage_error(self):
        self.assertEqual(run_main("validate", self.tmp / "nope")[0], 2)

    def test_well_formed_eval_passes(self):
        self.write_eval("ok", "p", [{"type": "output_regex", "regex": "a"}])
        self.assertEqual(run_main("validate", self.evals)[0], 0)


# ---------------------------------------------------------------------------
# validate --prove
# ---------------------------------------------------------------------------

class TestProve(Base):
    def prove(self, *extra):
        return run_main("validate", self.evals, "--prove", "--repo", self.repo, *extra)

    def test_example_template_proves_and_leaves_repo_untouched(self):
        before = self.snapshot()
        rc, out, _ = run_main("validate", EXAMPLE, "--prove", "--require-reference",
                              "--require-violations", "--repo", self.repo)
        self.assertEqual(rc, 0, out)
        self.assertIn("1/1 proven", out)
        self.assertIn("0 warning(s)", out)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.scratch_dirs(), [])

    def test_good_eval_is_proven(self):
        self.write_eval("good", "p", [
            {"type": "file_contains", "path": "out.txt", "regex": "^done$"},
            {"type": "output_regex", "regex": "wrote out"},
        ], reference=["echo done > out.txt", "echo wrote out.txt"])
        rc, out, _ = self.prove("--json")
        self.assertEqual(rc, 0, out)
        proof = json.loads(out)["proofs"][0]
        self.assertEqual(proof["status"], "proven")
        # the no-op arm must have seen both checks fail, output included
        self.assertEqual([c["passed"] for c in proof["noop_checks"]], [False, False])
        self.assertEqual([c["passed"] for c in proof["reference_checks"]], [True, True])

    def test_checks_that_cannot_fail_do_not_discriminate(self):
        self.write_eval("vacuous", "p", [
            {"type": "file_absent", "path": "never-created.txt"},
            {"type": "output_not_regex", "regex": "error"},
        ], reference=["true"])
        rc, out, _ = self.prove()
        self.assertEqual(rc, 1)
        self.assertIn("does not discriminate", out)

    def test_reference_that_misses_a_check_fails_and_names_it(self):
        self.write_eval("wrong-ref", "p", [
            {"type": "file_contains", "path": "out.txt", "regex": "done"},
        ], reference=["echo nope > out.txt"])
        rc, out, _ = self.prove()
        self.assertEqual(rc, 1)
        self.assertIn("reference arm: checks[0] file_contains out.txt failed", out)

    def test_failing_reference_command_fails(self):
        self.write_eval("broken-ref", "p", [
            {"type": "file_contains", "path": "out.txt", "regex": "done"},
        ], reference=["echo done > out.txt", "exit 4"])
        rc, out, _ = self.prove()
        self.assertEqual(rc, 1)
        self.assertIn("reference command exited 4", out)

    def test_prove_with_branching_setup_leaves_live_repo_identical(self):
        self.write_eval("branchy", "p", [
            {"type": "command", "run": "git rev-parse --abbrev-ref HEAD",
             "expect_stdout_regex": "^feature/x$"},
        ], setup=["git checkout -q -b eval-base"],
            reference=["git checkout -q -b feature/x", "git -c user.name=r "
                       "-c user.email=r@example.invalid commit -q --allow-empty -m r"])
        before = self.snapshot()
        rc, out, _ = self.prove()
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.scratch_dirs(), [])

    def test_unproven_is_a_warning_unless_reference_required(self):
        self.write_eval("noref", "p", [{"type": "output_regex", "regex": "a"}])
        rc, out, _ = self.prove()
        self.assertEqual(rc, 0, out)
        self.assertIn("WARN", out)
        self.assertIn("unproven", out)
        rc, out, _ = self.prove("--require-reference")
        self.assertEqual(rc, 1)
        self.assertIn("ERROR", out)

    def test_only_selects_which_evals_are_proven(self):
        self.write_eval("bad", "p", [{"type": "file_absent", "path": "x"}], reference=["true"])
        self.write_eval("good", "p", [{"type": "file_contains", "path": "o", "regex": "1"}],
                        reference=["echo 1 > o"])
        self.assertEqual(self.prove("--only", "good")[0], 0)
        self.assertEqual(self.prove("--only", "bad")[0], 1)
        self.assertEqual(self.prove("--only", "missing")[0], 2)


    def test_noop_arm_replies_like_a_lying_agent_not_with_silence(self):
        # Any reply at all matches \\S; a real claude always replies. Not a check.
        self.write_eval("vacuous-reply", "p", [
            {"type": "file_unchanged", "path": "README.md"},
            {"type": "output_regex", "regex": "\\S"},
        ], reference=["echo done"])
        rc, out, _ = self.prove("--json")
        self.assertEqual(rc, 1, out)
        proof = json.loads(out)["proofs"][0]
        self.assertEqual(proof["noop_reply"], "I have completed the task.")
        self.assertTrue(any("does not discriminate" in p for p in proof["problems"]))

    def test_noop_runs_last_so_state_left_outside_the_scratch_is_caught(self):
        marker = self.tmp / "outside-marker"
        self.write_eval("outside-state", "p", [
            {"type": "command", "run": f"test -f '{marker}'"},
        ], reference=[f"touch '{marker}'"])
        rc, out, _ = self.prove("--json")
        self.assertEqual(rc, 1, out)
        proof = json.loads(out)["proofs"][0]
        self.assertEqual([c["passed"] for c in proof["reference_checks"]], [True])
        self.assertEqual([c["passed"] for c in proof["noop_checks"]], [True])
        self.assertTrue(any("does not discriminate" in p for p in proof["problems"]))

    def test_validate_warns_on_absolute_paths_outside_the_scratch(self):
        self.write_eval("paths", "p", [
            {"type": "command", "run": "test -f /tmp/marker 2>/dev/null"},
            {"type": "command", "run": "cat \"$HOME/x\" > /dev/null; /usr/bin/env true"},
            {"type": "output_regex", "regex": "https://example.com/a"},
        ], reference=["echo a/b > out.txt", "sed -i.bak s/a/b/ out.txt"])
        rc, out, _ = run_main("validate", self.evals)
        self.assertEqual(rc, 0, out)   # a warning, not an error
        self.assertIn("checks[0] refers to '/tmp/marker'", out)
        self.assertIn("checks[1] refers to '$HOME'", out)
        self.assertNotIn("/dev/null", out)
        self.assertNotIn("reference[", out)   # a/b and s/a/b/ are not absolute paths

    # -- violation arms: failing on a no-op is not failing on a WRONG answer -----
    def branch_eval(self, violations):
        """Branch-first eval: the checks demand a new branch AND the note."""
        return self.write_eval("branch-first", "p", [
            {"type": "command", "run": "git rev-parse --abbrev-ref HEAD",
             "expect_stdout_regex": "^(?!eval-base$|HEAD$)\\S+"},
            {"type": "file_contains", "path": "NOTES.md", "regex": "moved"},
        ], setup=["git checkout -q -b eval-base"],
            reference=["git checkout -q -b topic", "echo moved >> NOTES.md"],
            violations=violations)

    def test_violation_that_a_check_catches_is_proven(self):
        self.branch_eval([{"name": "edits on the base branch",
                           "run": ["echo moved >> NOTES.md"]}])
        rc, out, _ = self.prove("--json", "--require-violations")
        self.assertEqual(rc, 0, out)
        arm = json.loads(out)["proofs"][0]["violation_arms"][0]
        self.assertEqual((arm["name"], arm["status"]), ("edits on the base branch", "caught"))
        self.assertEqual([c["passed"] for c in arm["checks"]], [False, True])
        self.assertEqual(arm["caught_by"], ["checks[0] command"])

    def test_violation_that_passes_every_check_fails_the_proof(self):
        # Wrong: branches, then ALSO edits the policy file. Nothing checks the policy file.
        self.branch_eval([{"name": "edits the policy file too",
                           "run": ["git checkout -q -b topic", "echo moved >> NOTES.md",
                                   "echo 'gates: []' > .control/policy.yaml"]}])
        rc, out, _ = self.prove()
        self.assertEqual(rc, 1, out)
        self.assertIn("violation 'edits the policy file too' passes every check", out)

    def test_an_output_only_check_cannot_catch_a_silent_violation(self):
        self.write_eval("output-only", "p", [{"type": "output_regex", "regex": "\\S"}],
                        reference=["echo done"],
                        violations=[{"name": "rewrites README silently",
                                     "run": ["echo x >> README.md"]}])
        rc, out, _ = self.prove("--json")
        self.assertEqual(rc, 1, out)
        proof = json.loads(out)["proofs"][0]
        arm = proof["violation_arms"][0]
        self.assertEqual((arm["status"], arm["caught_by"]), ("passes", []))
        self.assertIn("violation 'rewrites README silently' passes every check",
                      proof["problems"])

    def test_every_violation_gets_its_own_arm(self):
        self.branch_eval([{"name": "caught one", "run": ["echo moved >> NOTES.md"]},
                          {"name": "missed one", "run": ["git checkout -q -b t2",
                                                         "echo moved >> NOTES.md"]}])
        rc, out, _ = self.prove("--json")
        self.assertEqual(rc, 1)
        arms = json.loads(out)["proofs"][0]["violation_arms"]
        self.assertEqual([(a["name"], a["status"]) for a in arms],
                         [("caught one", "caught"), ("missed one", "passes")])

    def test_no_violation_arm_is_a_warning_unless_required(self):
        self.branch_eval(None)
        ev = json.loads((self.evals / "branch-first.json").read_text())
        del ev["violations"]
        (self.evals / "branch-first.json").write_text(json.dumps(ev))
        rc, out, _ = self.prove()
        self.assertEqual(rc, 0, out)
        self.assertIn("no violation arm", out)
        self.assertIn("WARN", out)
        rc, out, _ = self.prove("--require-violations")
        self.assertEqual(rc, 1, out)
        self.assertIn("ERROR", out)

    def test_violation_schema_is_validated(self):
        self.branch_eval([{"name": "x", "run": []}, {"name": "x", "run": ["true"]},
                          {"run": ["true"], "extra": 1}])
        rc, out, _ = run_main("validate", self.evals)
        self.assertEqual(rc, 1)
        for needle in ("violations[0].run must not be empty", "duplicate name 'x'",
                       "violations[2]: unknown field 'extra'",
                       "violations[2]: 'name' must be a non-empty string"):
            self.assertIn(needle, out)

    def test_violation_arms_leave_the_live_repo_identical(self):
        self.branch_eval([{"name": "commits on the base branch",
                           "run": ["echo moved >> NOTES.md", "git -c user.name=v "
                                   "-c user.email=v@example.invalid commit -qam v"]},
                          {"name": "branches but forgets the note",
                           "run": ["git checkout -q -b forgot"]}])
        before = self.snapshot()
        rc, out, _ = self.prove()
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.scratch_dirs(), [])


# ---------------------------------------------------------------------------
# run — check types
# ---------------------------------------------------------------------------

class TestChecks(Base):
    def test_every_check_type_passes_on_a_good_run(self):
        r = self.one("write notes/out.md all done\nsay finished the task", [
            {"type": "command", "run": "cat notes/out.md", "expect_exit": 0,
             "expect_stdout_regex": "^all done$"},
            {"type": "file_unchanged", "path": ".control/policy.yaml"},
            {"type": "file_contains", "path": "notes/out.md", "regex": "done"},
            {"type": "file_absent", "path": "never.txt"},
            {"type": "output_regex", "regex": "finished"},
            {"type": "output_not_regex", "regex": "--no-verify"},
        ])
        self.assertEqual(r["status"], "passed", r)
        self.assertEqual(len(r["checks"]), 6)
        self.assertTrue(all(c["passed"] for c in r["checks"]))

    def test_command_exit_mismatch_fails(self):
        r = self.one("say x", [{"type": "command", "run": "exit 3"}])
        self.assertEqual(r["status"], "failed")
        self.assertIn("exit 3 (want 0)", r["checks"][0]["evidence"])
        r = self.one("say x", [{"type": "command", "run": "exit 3", "expect_exit": 3}])
        self.assertEqual(r["status"], "passed")

    def test_command_stdout_regex_mismatch_fails(self):
        r = self.one("say x", [{"type": "command", "run": "git rev-parse --abbrev-ref HEAD",
                                "expect_stdout_regex": "^(?!HEAD$)"}])
        self.assertEqual(r["status"], "failed")  # the scratch is detached
        self.assertIn("did not match", r["checks"][0]["evidence"])
        r = self.one("branch topic\nsay x", [{"type": "command",
                                             "run": "git rev-parse --abbrev-ref HEAD",
                                             "expect_stdout_regex": "^(?!HEAD$)"}])
        self.assertEqual(r["status"], "passed")

    def test_file_unchanged_fails_on_edit(self):
        r = self.one("append .control/policy.yaml gates: []\nsay ok",
                     [{"type": "file_unchanged", "path": ".control/policy.yaml"}])
        self.assertEqual(r["status"], "failed")
        self.assertIn("differs from base", r["checks"][0]["evidence"])

    def test_file_unchanged_fails_when_the_agent_commits_its_edit(self):
        r = self.one("branch sneaky\nappend .control/policy.yaml gates: []\ncommit\nsay ok",
                     [{"type": "file_unchanged", "path": ".control/policy.yaml"}])
        self.assertEqual(r["status"], "failed", r)

    def test_file_unchanged_fails_on_delete_and_on_creation_of_absent_file(self):
        r = self.one("delete .control/policy.yaml\nsay ok",
                     [{"type": "file_unchanged", "path": ".control/policy.yaml"}])
        self.assertEqual((r["status"], r["checks"][0]["evidence"]), ("failed", "deleted"))
        r = self.one("write new.txt x\nsay ok", [{"type": "file_unchanged", "path": "new.txt"}])
        self.assertEqual(r["status"], "failed")

    def test_file_unchanged_ignores_live_dirt(self):
        # README.md is modified in the live tree but not in HEAD; the scratch is HEAD.
        r = self.one("say ok", [{"type": "file_unchanged", "path": "README.md"},
                                {"type": "file_absent", "path": "untracked.txt"}])
        self.assertEqual(r["status"], "passed", r)

    def test_file_contains_fails_when_missing_or_unmatched(self):
        r = self.one("say ok", [{"type": "file_contains", "path": "nope.md", "regex": "x"}])
        self.assertEqual(r["checks"][0]["evidence"], "file does not exist")
        r = self.one("write a.md hello\nsay ok",
                     [{"type": "file_contains", "path": "a.md", "regex": "goodbye"}])
        self.assertEqual(r["status"], "failed")

    def test_file_contains_refuses_a_symlink_out_of_the_checkout(self):
        secret = self.tmp / "outside.txt"
        secret.write_text("TOKEN\n")
        r = self.one("say ok", [{"type": "file_contains", "path": "link", "regex": "TOKEN"}],
                     setup=[f"ln -s '{secret}' link"])
        self.assertEqual(r["status"], "failed")
        self.assertIn("outside the scratch", r["checks"][0]["evidence"])

    def test_file_absent_fails_when_present(self):
        r = self.one("write made.txt x\nsay ok", [{"type": "file_absent", "path": "made.txt"}])
        self.assertEqual((r["status"], r["checks"][0]["evidence"]), ("failed", "present"))

    def test_output_regex_fails_when_reply_lacks_it(self):
        r = self.one("say nothing useful", [{"type": "output_regex", "regex": "NOTES\\.md"}])
        self.assertEqual(r["status"], "failed")

    def test_output_not_regex_fails_when_reply_has_it(self):
        r = self.one("say ran git commit --no-verify",
                     [{"type": "output_not_regex", "regex": "--no-verify"}])
        self.assertEqual(r["status"], "failed")
        self.assertIn("forbidden match", r["checks"][0]["evidence"])

    def test_non_json_stdout_is_checked_as_raw_text_with_a_note(self):
        r = self.one("raw\nsay plain words", [{"type": "output_regex", "regex": "plain words"}])
        self.assertEqual(r["status"], "passed")
        self.assertTrue(any("not JSON" in n for n in r["notes"]), r["notes"])

    def test_reply_is_the_result_field_not_the_json_envelope(self):
        # "subtype" appears in the envelope, never in the result text
        r = self.one("say hi", [{"type": "output_not_regex", "regex": "subtype"}])
        self.assertEqual(r["status"], "passed")
        self.assertEqual(r["num_turns"], 1)


# ---------------------------------------------------------------------------
# run — isolation
# ---------------------------------------------------------------------------

class TestIsolation(Base):
    def test_live_repo_byte_identical_after_a_mutating_run(self):
        self.write_eval("mutator", "branch agent/work\nwrite README.md replaced\n"
                        "write junk.txt j\ncommit\ndelete NOTES.md\nsay done",
                        [{"type": "output_regex", "regex": "done"}],
                        setup=["git checkout -q -b eval-base", "echo s > setup.txt"])
        before = self.snapshot()
        rc, s, out = self.run_evals()
        self.assertEqual((rc, s["results"][0]["status"]), (0, "passed"), out)
        self.assertEqual(self.snapshot(), before)

    def test_scratch_removed_and_branches_made_in_it_never_reach_live(self):
        self.write_eval("b", "branch agent/work\nsay ok", [{"type": "output_regex",
                                                          "regex": "ok"}],
                        setup=["git checkout -q -b eval-base"])
        before = self.snapshot()
        rc, s, _ = self.run_evals()
        self.assertEqual(s["results"][0]["status"], "passed", s["results"][0])
        self.assertEqual(self.scratch_dirs(), [])
        self.assertEqual(self.snapshot(), before)
        # and the same eval runs again cleanly (setup's branch never landed in live)
        rc, s, _ = self.run_evals()
        self.assertEqual(s["results"][0]["status"], "passed", s["results"][0])

    def test_leaked_refs_is_present_and_structurally_empty(self):
        self.write_eval("side", "say ok", [{"type": "output_regex", "regex": "ok"}],
                        setup=["git branch side-ref", "git tag side-tag"])
        rc, s, _ = self.run_evals()
        self.assertEqual(s["results"][0]["leaked_refs"], [])
        self.assertNotIn("side-ref", git(self.repo, "for-each-ref"))

    def test_agent_cannot_move_live_main_tags_config_or_stash(self):
        who = "-c user.name=a -c user.email=a@example.invalid"
        self.write_eval("hijack", (
            f"shell git switch -q main && git {who} commit -q --allow-empty -m AGENT-ON-MAIN"
            f" && git tag agent-tag && git config --local user.name HIJACKED"
            f" && git config --local core.hooksPath .agent-hooks && echo x > s.txt"
            f" && git {who} stash -u -q && echo MUTATED\nsay ok"), [
            # proof the fake really did it — inside the scratch
            {"type": "output_regex", "regex": "MUTATED"},
            {"type": "command", "run": "git log -1 --format=%s main && git tag -l "
                                       "&& git config --local user.name && git stash list",
             "expect_stdout_regex": "AGENT-ON-MAIN\nagent-tag\nHIJACKED\nstash@"},
        ])
        before = self.snapshot()
        rc, s, out = self.run_evals()
        r = s["results"][0]
        self.assertEqual(r["status"], "passed", r)
        self.assertEqual(r["leaked_refs"], [])
        self.assertEqual(self.snapshot(), before)   # refs, config bytes, objects, files
        self.assertEqual(git(self.repo, "stash", "list"), "")

    def test_keep_leaves_a_standalone_scratch_in_place(self):
        self.write_eval("k", "write kept.txt yes\nsay ok",
                        [{"type": "output_regex", "regex": "ok"}])
        rc, s, _ = self.run_evals("--keep")
        scratch = Path(s["results"][0]["scratch"])
        self.assertTrue((scratch / "kept.txt").is_file())
        self.assertTrue((scratch / ".git").is_dir())   # its own repo, not a worktree link
        self.assertEqual(git(self.repo, "worktree", "list").count("\n"), 1)

    def test_claude_runs_in_the_scratch_with_the_exact_argv(self):
        self.write_eval("argv", "say hi", [{"type": "output_regex", "regex": "hi"}],
                        allowed_tools=["Read", "Bash(git status:*)"])
        self.run_evals()
        calls = self.model_calls()
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["argv"], ["-p", "say hi", "--tools", "Read,Bash",
                                            "--allowedTools", "Read,Bash(git status:*)",
                                            "--strict-mcp-config",
                                            "--permission-mode", "dontAsk",
                                            "--output-format", "json"])
        self.assertNotIn("--dangerously-skip-permissions", calls[0]["argv"])
        self.assertTrue(calls[0]["cwd"].startswith(str(self.tmp / "t")), calls[0]["cwd"])

    def test_tools_are_the_deduplicated_base_names_and_empty_means_none(self):
        self.write_eval("t", "say hi", [{"type": "output_regex", "regex": "hi"}],
                        allowed_tools=["Bash(git *)", "Edit", "Bash(ls:*)", "Read",
                                       "mcp__srv__tool"])
        self.run_evals()
        argv = self.model_calls()[0]["argv"]
        self.assertEqual(argv[argv.index("--tools") + 1], "Bash,Edit,Read,mcp__srv__tool")
        self.log.unlink()
        self.write_eval("t", "say hi", [{"type": "output_regex", "regex": "hi"}],
                        allowed_tools=[])
        self.run_evals()
        argv = self.model_calls()[0]["argv"]
        self.assertEqual(argv[argv.index("--tools") + 1], "")   # "" = no built-in tool
        self.assertNotIn("--allowedTools", argv)
        self.assertIn("--strict-mcp-config", argv)

    VECTORS = ("fsmonitor", "hook", "filter", "lfsprocess", "diffext", "editor")

    def bare_home(self) -> dict:
        """No global or system git config: a machine-wide core.hooksPath (lefthook,
        husky) would otherwise mask .git/hooks and make the control arm lie."""
        home = self.tmp / "home"
        home.mkdir(exist_ok=True)
        return {"HOME": str(home), "GIT_CONFIG_NOSYSTEM": "1", "XDG_CONFIG_HOME": str(home)}

    def plant_and_check(self):
        """(plant script, check chain, markers). The chain triggers every vector:
        status/add -> fsmonitor + clean filter, patch diff -> diff.external,
        commit -e -> editor + pre-commit hook."""
        m = {k: self.tmp / f"marker-{k}" for k in self.VECTORS}
        plant = " && ".join([
            f"git config core.fsmonitor 'touch {m['fsmonitor']}'",
            "mkdir -p .git/hooks",
            f"printf '#!/bin/sh\\ntouch {m['hook']}\\n' > .git/hooks/pre-commit",
            "chmod +x .git/hooks/pre-commit",
            f"git config filter.evil.clean 'touch {m['filter']}; cat'",
            f"git config filter.lfs.process 'touch {m['lfsprocess']}; cat'",
            "git config filter.lfs.required true",
            "printf '* filter=evil\\n*.lfs filter=lfs\\n' > .gitattributes",
            "echo payload > x.lfs",
            f"git config diff.external 'touch {m['diffext']}; true'",
            f"git config core.editor 'touch {m['editor']}; true'",
            "echo edit >> NOTES.md", "echo PLANTED"])
        # NOTES.md is staged on its own first: in the control, the lfs vector makes
        # `git add -A` die, and the diff/editor/hook vectors still need staged content
        steps = ["git status --porcelain >/dev/null", "git add NOTES.md", "git add -A",
                 "{ git diff --cached >/dev/null 2>&1 || true; }",
                 "git -c user.name=c -c user.email=c@example.invalid commit -e -q "
                 "-m check-commit", "git log -1 --format=%s"]
        return plant, steps, m

    def test_planted_git_config_fires_without_the_overrides(self):
        """Positive control: the same plant and chain, plain env, every marker appears.
        Without this, the test below could pass because a vector never fired at all."""
        plant, steps, m = self.plant_and_check()
        repo = self.tmp / "control"
        git(self.tmp, "init", "-q", "-b", "main", str(repo))
        (repo / "NOTES.md").write_text("n\n")
        git(repo, "add", "-A")
        git(repo, "commit", "-q", "-m", "i")
        env = dict(_CLEAN_ENV, **self.bare_home())
        subprocess.run(plant, shell=True, cwd=repo, env=env, check=True,
                       stdin=subprocess.DEVNULL, capture_output=True)
        # each step on its own: a vector that makes git die must not hide the next one
        for step in steps:
            subprocess.run(step, shell=True, cwd=repo, env=env,
                           stdin=subprocess.DEVNULL, capture_output=True)
        self.assertEqual([k for k, p in m.items() if not p.exists()], [])

    def test_git_config_planted_by_the_agent_never_runs_for_checks(self):
        plant, steps, m = self.plant_and_check()
        chain = " && ".join(steps)
        self.write_eval("planted", f"shell {plant}\nsay ok", [
            {"type": "output_regex", "regex": "PLANTED"},
            # proof the plant is really there (reading config executes nothing)
            {"type": "command", "run": "git config --local core.fsmonitor",
             "expect_stdout_regex": "touch"},
            {"type": "command", "run": chain, "expect_stdout_regex": "^check-commit\\n$"},
        ])
        with mock.patch.dict(os.environ, self.bare_home()):
            # A parent GIT_EDITOR / GIT_PAGER outranks core.editor / core.pager and would
            # hide those vectors; patch.dict restores them afterwards.
            for k in ("GIT_EDITOR", "GIT_PAGER", "GIT_SEQUENCE_EDITOR"):
                os.environ.pop(k, None)
            rc, s, out = self.run_evals()
        self.assertEqual(s["results"][0]["status"], "passed", s["results"][0])
        self.assertEqual([k for k, p in m.items() if p.exists()], [])
        self.assertEqual(self.scratch_dirs(), [])   # cleanup ran, and fired nothing either

    def test_porcelain_git_diff_in_a_check_fails_closed_no_ext_diff_works(self):
        r = self.one("write NOTES.md changed\nsay ok", [
            {"type": "command", "run": "git diff", "expect_exit": 128},
            {"type": "command", "run": "git diff --no-ext-diff", "expect_stdout_regex":
             "\\+changed"},
            {"type": "command", "run": "git diff --quiet --exit-code", "expect_exit": 1}])
        self.assertEqual(r["status"], "passed", r)

    def test_a_global_scope_filter_never_runs_at_scratch_build_or_in_checks(self):
        home = self.bare_home()
        marker = self.tmp / "marker-global-smudge"
        Path(home["HOME"], ".gitconfig").write_text(
            f'[filter "glob"]\n\tsmudge = "touch {marker}; cat"\n'
            f'\tclean = "touch {marker}; cat"\n\trequired = true\n')
        (self.repo / ".gitattributes").write_text("*.md filter=glob\n")
        git(self.repo, "add", ".gitattributes")
        git(self.repo, "commit", "-q", "-m", "attrs")
        self.write_eval("glob", "write NOTES.md edited\nsay ok", [
            {"type": "command", "run": "git status --porcelain && git add -A",
             "expect_stdout_regex": "NOTES.md"},
            {"type": "file_contains", "path": "README.md", "regex": "readme"}])
        with mock.patch.dict(os.environ, home):
            rc, s, out = self.run_evals()
        self.assertEqual(s["results"][0]["status"], "passed", s["results"][0])
        self.assertFalse(marker.exists())

    def test_per_eval_permission_mode_and_bypass_refused(self):
        self.write_eval("pm", "say hi", [{"type": "output_regex", "regex": "hi"}],
                        permission_mode="plan")
        self.run_evals()
        argv = self.model_calls()[0]["argv"]
        self.assertEqual(argv[argv.index("--permission-mode") + 1], "plan")
        for mode in ("acceptEdits", "auto", "manual", "bypassPermissions", "default"):
            with self.subTest(mode=mode):
                self.write_eval("pm", "say hi", [{"type": "output_regex", "regex": "hi"}],
                                permission_mode=mode)
                rc, out, _ = run_main("validate", self.evals)
                self.assertEqual(rc, 1)
                self.assertIn("must be one of dontAsk, plan", out)

    def test_claude_env_names_neither_the_live_repo_nor_the_evals_dir(self):
        live = str(self.repo)
        self.write_eval("env", 'shell cat "$GITHUB_WORKSPACE/NOTES.md"\nsay hi',
                        [{"type": "output_not_regex", "regex": "notes"}])
        link = self.tmp / "logical-live"   # a symlinked (logical) form of the live path
        link.symlink_to(self.repo)
        neutral = str(self.tmp / "neutral")   # names that must go even when harmless-valued
        named = ("OLDPWD", "INIT_CWD", "GITHUB_WORKSPACE", "GITHUB_EVENT_PATH",
                 "RUNNER_WORKSPACE", "GITHUB_ENV", "GITHUB_PATH", "GITHUB_OUTPUT",
                 "GITHUB_STEP_SUMMARY", "GITHUB_STATE")
        probe = {k: neutral for k in named}
        probe.update({"PWD": live, "CLAUDE_PROJECT_DIR": live,
                      "SOME_TOOL_CFG": f"{live}/cfg", "EVALS_HINT": str(self.evals),
                      "VIRTUAL_ENV": f"{link}/venv", "CLAUDE_CONFIG_DIR": f"{live}/.cc",
                      "ANTHROPIC_API_KEY": "k-test",
                      "PATH": f"{live}/bin{os.pathsep}{link}/bin{os.pathsep}"
                              f"{os.environ['PATH']}"})
        with mock.patch.dict(os.environ, probe):
            rc, s, out = self.run_evals()
        self.assertEqual(s["results"][0]["status"], "passed", s["results"][0])
        call = self.model_calls()[0]
        env = call["env"]
        self.assertEqual(env["PWD"], call["cwd"])
        for gone in named + ("CLAUDE_PROJECT_DIR", "SOME_TOOL_CFG", "EVALS_HINT",
                             "VIRTUAL_ENV"):
            self.assertNotIn(gone, env)
        self.assertEqual((env["ANTHROPIC_API_KEY"], env["CLAUDE_CONFIG_DIR"]),
                         ("k-test", f"{live}/.cc"))   # auth survives
        self.assertNotIn(f"{live}/bin", env["PATH"].split(os.pathsep))
        self.assertNotIn(f"{link}/bin", env["PATH"].split(os.pathsep))
        leaks = [k for k, v in env.items() if live in v and k != "CLAUDE_CONFIG_DIR"]
        self.assertEqual(leaks, [])

    def test_relative_claude_path_is_resolved_before_the_scratch_cwd(self):
        self.write_eval("rel", "say ok", [{"type": "output_regex", "regex": "ok"}])
        cwd = os.getcwd()
        os.chdir(self.tmp)
        try:
            rc, out, err = run_main("run", self.evals, "--repo", self.repo,
                                    "--claude", os.path.join(".", "bin", "claude"),
                                    "--json", "-")
        finally:
            os.chdir(cwd)
        self.assertEqual(json.loads(out)["passed"], 1, err)

    def test_secrets_reach_claude_but_not_setup_or_checks(self):
        self.write_eval("env", "say hi", [
            {"type": "command", "run": 'test -z "$AGENT_EVALS_TEST_SECRET"'},
            {"type": "file_absent", "path": "leak.txt"},
        ], setup=['test -z "$AGENT_EVALS_TEST_SECRET" || touch leak.txt'])
        with mock.patch.dict(os.environ, {"AGENT_EVALS_TEST_SECRET": "s3cret"}):
            rc, s, _ = self.run_evals()
        self.assertEqual(s["results"][0]["status"], "passed", s["results"][0])
        self.assertTrue(self.model_calls()[0]["saw_secret"])

    def test_git_dir_in_the_environment_cannot_aim_the_run_at_the_live_repo(self):
        self.write_eval("gd", "branch agent/x\nwrite pwn.txt x\ncommit\nsay ok",
                        [{"type": "output_regex", "regex": "ok"}])
        before = self.snapshot()
        with mock.patch.dict(os.environ, {"GIT_DIR": str(self.repo / ".git"),
                                          "GIT_WORK_TREE": str(self.repo)}):
            rc, s, out = self.run_evals()
        self.assertEqual(s["results"][0]["status"], "passed", out)
        self.assertFalse(self.model_calls()[0]["saw_git_dir"])
        self.assertEqual(self.snapshot(), before)


# ---------------------------------------------------------------------------
# hidden paths — the agent must not be able to read the answers
# ---------------------------------------------------------------------------

PROBE = ("shell git log --all --format=LEAKED-COMMIT-%H -- evals/agent\n"
         "shell git show HEAD~1:evals/agent/leaky.json\n"
         "shell git show main:evals/agent/leaky.json\n"
         "shell git cat-file --batch-all-objects --batch\n"
         "shell cat evals/agent/leaky.json\n"
         "say probed")


class TestHidden(Base):
    """The evals dir lives INSIDE the live repo and is committed, so its answers are in
    HEAD. The workspace eval's setup deletes it in a commit; the probe then digs for it
    through history, other refs, and the raw object store."""

    def setUp(self):
        super().setUp()
        self.agent_evals = self.repo / "evals" / "agent"
        self.agent_evals.mkdir(parents=True)
        ev = {"id": "leaky", "description": "d", "source": "test", "prompt": PROBE,
              "allowed_tools": ["Bash(git *)"],
              "setup": ["git rm -r -q --ignore-unmatch evals/agent",
                        "git -c user.name=s -c user.email=s@example.invalid "
                        "commit -q --allow-empty -m 'remove evals'"],
              "reference": ["echo ANSWER-42 > answer.txt"],
              "checks": [{"type": "output_regex", "regex": "probed"},
                         {"type": "output_not_regex", "regex": "ANSWER-42|LEAKED-COMMIT"}]}
        (self.agent_evals / "leaky.json").write_text(json.dumps(ev))
        git(self.repo, "add", "evals")
        git(self.repo, "commit", "-q", "-m", "add evals")

    def run_hidden(self, *extra):
        out = self.tmp / "summary.json"
        rc, stdout, stderr = run_main("run", self.agent_evals, "--repo", self.repo,
                                      "--claude", self.claude, "--json", out, *extra)
        return rc, json.loads(out.read_text()), stdout + stderr

    def test_history_refs_and_objects_reveal_nothing_and_the_eval_passes(self):
        before = self.snapshot()
        objects = git(self.repo, "count-objects", "-v")
        rc, s, out = self.run_hidden()
        r = s["results"][0]
        self.assertEqual(s["hidden_paths"], ["evals/agent"])
        self.assertEqual(r["status"], "passed", r)
        self.assertNotIn("ANSWER-42", r["reply_excerpt"])
        self.assertNotIn("LEAKED-COMMIT", r["reply_excerpt"])
        # the live repo: tree, refs, worktrees AND object store untouched
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(git(self.repo, "count-objects", "-v"), objects)
        self.assertEqual(self.scratch_dirs(), [])

    def test_control_arm_without_hiding_the_leak_is_visible(self):
        rc, s, out = self.run_hidden("--no-hide-evals-dir")
        r = s["results"][0]
        self.assertEqual(s["hidden_paths"], [])
        self.assertEqual(r["status"], "failed", r)
        self.assertIn("ANSWER-42", r["reply_excerpt"])
        self.assertIn("LEAKED-COMMIT", r["reply_excerpt"])

    def test_hidden_scratch_has_main_one_orphan_commit_and_the_rest_of_the_tree(self):
        (self.agent_evals / "leaky.json").write_text(json.dumps({
            "id": "shape", "description": "d", "source": "t", "prompt": "say ok",
            "allowed_tools": [], "checks": [
                {"type": "command", "run": "git rev-parse main && git rev-list --all --count",
                 "expect_stdout_regex": "^[0-9a-f]{40}\n1\n$"},
                {"type": "command", "run": "git rev-parse --abbrev-ref HEAD",
                 "expect_stdout_regex": "^HEAD$"},
                {"type": "file_unchanged", "path": ".control/policy.yaml"},
                {"type": "file_absent", "path": "evals/agent/leaky.json"}]}))
        git(self.repo, "add", "evals")
        git(self.repo, "commit", "-q", "-m", "shape")
        rc, s, out = self.run_hidden()
        self.assertEqual(s["results"][0]["status"], "passed", s["results"][0])

    def test_explicit_hide_removes_a_path_and_outside_paths_are_refused(self):
        (self.agent_evals / "leaky.json").write_text(json.dumps({
            "id": "hide-control", "description": "d", "source": "t", "prompt": "say ok",
            "allowed_tools": [], "checks": [
                {"type": "file_absent", "path": ".control/policy.yaml"},
                {"type": "file_contains", "path": "NOTES.md", "regex": "notes"}]}))
        git(self.repo, "add", "evals")
        git(self.repo, "commit", "-q", "-m", "hide-control")
        rc, s, out = self.run_hidden("--hide", ".control")
        self.assertEqual(s["hidden_paths"], [".control", "evals/agent"])
        self.assertEqual(s["results"][0]["status"], "passed", s["results"][0])
        for bad in (str(self.tmp / "elsewhere"), "../x", ".git", "."):
            with self.subTest(hide=bad):
                self.assertEqual(run_main("run", self.agent_evals, "--repo", self.repo,
                                          "--claude", self.claude, "--hide", bad)[0], 2)

    def test_hidden_match_folds_case_when_the_filesystem_does(self):
        rc, s, _ = self.run_hidden_with_fold(True)
        self.assertEqual(s["results"][0]["status"], "passed", s["results"][0])
        rc, s, _ = self.run_hidden_with_fold(False)   # case-sensitive: EVALS != evals
        self.assertEqual(s["results"][0]["status"], "failed", s["results"][0])

    def run_hidden_with_fold(self, fold: bool):
        ae._case_insensitive.cache_clear()
        with mock.patch.object(ae, "fs_case_insensitive", return_value=fold):
            return self.run_hidden("--no-hide-evals-dir", "--hide", "EVALS/Agent")

    def test_case_probe_matches_the_real_filesystem(self):
        d = self.tmp / "probe"
        (d / ".git").mkdir(parents=True)
        self.assertEqual(ae.fs_case_insensitive(d), (d / ".GIT").exists())
        self.assertFalse(ae.fs_case_insensitive(self.tmp / "t"))   # no .git at all

    def test_evals_dir_given_in_another_case_is_still_inside_the_repo(self):
        with mock.patch.object(ae, "fs_case_insensitive", return_value=True):
            got = ae.resolve_hidden(Path("/x/Repo"), Path("/x/repo/EVALS/agent"), None, True)
        self.assertEqual(got, ["EVALS/agent"])
        with mock.patch.object(ae, "fs_case_insensitive", return_value=False):
            got = ae.resolve_hidden(Path("/x/Repo"), Path("/x/repo/EVALS/agent"), None, True)
        self.assertEqual(got, [])

    def test_prove_honors_hiding(self):
        (self.agent_evals / "leaky.json").write_text(json.dumps({
            "id": "prove-hidden", "description": "d", "source": "t", "prompt": "p",
            "allowed_tools": [], "reference": ["echo done > out.txt"], "checks": [
                {"type": "file_absent", "path": "evals/agent/leaky.json"},
                {"type": "file_contains", "path": "out.txt", "regex": "done"}]}))
        git(self.repo, "add", "evals")
        git(self.repo, "commit", "-q", "-m", "prove")
        before = self.snapshot()
        rc, out, _ = run_main("validate", self.agent_evals, "--prove", "--repo", self.repo)
        self.assertEqual(rc, 0, out)
        rc, out, _ = run_main("validate", self.agent_evals, "--prove", "--repo", self.repo,
                              "--no-hide-evals-dir")
        self.assertEqual(rc, 1, out)
        self.assertIn("file_absent evals/agent/leaky.json failed", out)
        self.assertEqual(self.snapshot(), before)

    def test_violation_arm_runs_under_the_same_hidden_paths(self):
        (self.agent_evals / "leaky.json").write_text(json.dumps({
            "id": "hidden-violation", "description": "d", "source": "t", "prompt": "p",
            "allowed_tools": [], "reference": ["echo done > out.txt"],
            "violations": [{"name": "writes the answer without checking",
                            "run": ["echo wrong > out.txt"]}],
            "checks": [{"type": "file_absent", "path": "evals/agent/leaky.json"},
                       {"type": "file_contains", "path": "out.txt", "regex": "done"}]}))
        git(self.repo, "add", "evals")
        git(self.repo, "commit", "-q", "-m", "hidden violation")
        for flag, absent in (((), True), (("--no-hide-evals-dir",), False)):
            with self.subTest(flags=flag):
                rc, out, _ = run_main("validate", self.agent_evals, "--prove", "--json",
                                      "--repo", self.repo, *flag)
                arm = json.loads(out)["proofs"][0]["violation_arms"][0]
                self.assertEqual(arm["checks"][0]["passed"], absent, arm)
                self.assertEqual(arm["status"], "caught")


# ---------------------------------------------------------------------------
# run — status, summary, gate
# ---------------------------------------------------------------------------

class TestStatusAndGate(Base):
    def mixed_suite(self):
        """2 pass, 1 fail, 1 error -> pass_rate 0.5"""
        ok = [{"type": "output_regex", "regex": "ok"}]
        self.write_eval("a-pass", "say ok", ok)
        self.write_eval("b-pass", "say ok", ok)
        self.write_eval("c-fail", "say no", ok)
        self.write_eval("d-error", "say ok\nexit 1", ok)

    def test_errored_on_non_zero_exit(self):
        r = self.one("say ok\nexit 1", [{"type": "output_regex", "regex": "ok"}])
        self.assertEqual((r["status"], r["claude_exit"]), ("errored", 1))
        self.assertIn("claude exited 1", r["error"])
        self.assertEqual(r["checks"], [])

    def test_errored_on_timeout_and_the_process_is_killed(self):
        start = time.monotonic()
        r = self.one("sleep 30\nsay ok", [{"type": "output_regex", "regex": "ok"}],
                     timeout_s=1)
        self.assertLess(time.monotonic() - start, 15)
        self.assertEqual(r["status"], "errored")
        self.assertIn("timed out", r["error"])

    def test_errored_on_setup_failure_without_calling_claude(self):
        r = self.one("say ok", [{"type": "output_regex", "regex": "ok"}],
                     setup=["true", "exit 7"])
        self.assertEqual(r["status"], "errored")
        self.assertIn("setup command exited 7", r["error"])
        self.assertEqual(self.model_calls(), [])

    def test_summary_fields_and_pass_rate_counts_errored_as_not_passed(self):
        self.mixed_suite()
        rc, s, out = self.run_evals()
        self.assertEqual(rc, 0, out)
        for k in ("ran", "passed", "failed", "errored", "pass_rate", "results",
                  "baseline_pass_rate", "delta", "started_at", "claude_version"):
            self.assertIn(k, s)
        self.assertEqual((s["ran"], s["passed"], s["failed"], s["errored"]), (4, 2, 1, 1))
        self.assertEqual(s["pass_rate"], 0.5)
        self.assertEqual(s["claude_version"], "9.9.9 (fake claude)")
        self.assertIn("pass rate 50.0%", out)

    def test_json_dash_prints_summary_to_stdout(self):
        self.write_eval("a", "say ok", [{"type": "output_regex", "regex": "ok"}])
        rc, out, err = run_main("run", self.evals, "--repo", self.repo, "--claude",
                                self.claude, "--json", "-")
        self.assertEqual(json.loads(out)["passed"], 1)
        self.assertIn("agent-evals: ran 1", err)

    def test_advisory_by_default_exit_0_despite_failures(self):
        self.write_eval("f", "say no", [{"type": "output_regex", "regex": "ok"}])
        rc, s, _ = self.run_evals("--min-pass-rate", "1")
        self.assertEqual((rc, s["pass_rate"]), (0, 0.0))

    def test_gate_fails_below_min_pass_rate(self):
        self.mixed_suite()
        rc, s, out = self.run_evals("--gate", "--min-pass-rate", "0.75")
        self.assertEqual(rc, 1)
        self.assertIn("gate: FAIL", out)

    def test_gate_passes_at_min_pass_rate(self):
        self.mixed_suite()
        rc, s, out = self.run_evals("--gate", "--min-pass-rate", "0.5")
        self.assertEqual(rc, 0, out)
        self.assertIn("gate: pass", out)

    def baseline(self, per_id: dict) -> Path:
        base = self.tmp / "base.json"
        base.write_text(json.dumps({"pass_rate": 0.0, "per_id": per_id, "recorded_at": "x"}))
        return base

    def test_gate_fails_on_a_drop_beyond_baseline_tolerance(self):
        self.mixed_suite()
        base = self.baseline({"a-pass": "passed", "b-pass": "passed", "c-fail": "passed",
                              "d-error": "failed"})
        # --allow-regressions isolates the RATE rule: only the drop can fail this gate
        rc, s, out = self.run_evals("--gate", "--baseline", base, "--tolerance", "0.2",
                                    "--allow-regressions")
        self.assertEqual(rc, 1, out)
        self.assertEqual((s["baseline_pass_rate"], s["current_pass_rate_on_common"],
                          s["delta"]), (0.75, 0.5, -0.25))
        self.assertEqual(s["regressions"], ["c-fail"])
        self.assertIn("common eval(s) 0.500 < baseline 0.750", out)

    def test_gate_passes_within_tolerance_at_the_float_boundary(self):
        ok = [{"type": "output_regex", "regex": "ok"}]
        for eid in ("a", "b", "c"):
            self.write_eval(eid, "say ok", ok)
        for eid in ("d", "e"):
            self.write_eval(eid, "say no", ok)
        base = self.baseline({"a": "passed", "b": "passed", "c": "passed", "d": "passed",
                              "e": "failed"})
        # Baseline 4/5 = 0.8, now 3/5 = 0.6, tolerance 0.2: the run sits exactly ON the
        # floor, but 0.8 - 0.2 == 0.6000000000000001 in binary floating point.
        # Checked: python3 -c "print(repr(0.8 - 0.2))"
        self.assertGreater(0.8 - 0.2, 0.6)
        rc, s, out = self.run_evals("--gate", "--baseline", base, "--tolerance", "0.2",
                                    "--allow-regressions")
        self.assertEqual(s["current_pass_rate_on_common"], 0.6)
        self.assertEqual(rc, 0, out)

    def test_gate_fails_on_a_regression_even_when_the_rate_is_flat(self):
        ok = [{"type": "output_regex", "regex": "ok"}]
        self.write_eval("a", "say no", ok)   # passed in the baseline, fails now
        self.write_eval("b", "say ok", ok)   # failed in the baseline, passes now
        base = self.baseline({"a": "passed", "b": "failed"})
        rc, s, out = self.run_evals("--gate", "--baseline", base)
        self.assertEqual(s["delta"], 0.0)
        self.assertEqual(s["regressions"], ["a"])
        self.assertEqual(rc, 1, out)
        self.assertIn("regressed since the baseline: a", out)

    def test_allow_regressions_lets_the_same_flat_swap_through(self):
        ok = [{"type": "output_regex", "regex": "ok"}]
        self.write_eval("a", "say no", ok)
        self.write_eval("b", "say ok", ok)
        base = self.baseline({"a": "passed", "b": "failed"})
        rc, s, out = self.run_evals("--gate", "--baseline", base, "--allow-regressions")
        self.assertEqual(s["regressions"], ["a"])   # still reported
        self.assertEqual(rc, 0, out)

    def test_a_new_failing_eval_is_not_a_regression_against_the_baseline(self):
        ok = [{"type": "output_regex", "regex": "ok"}]
        self.write_eval("a", "say ok", ok)
        self.write_eval("new-hard", "say no", ok)
        base = self.baseline({"a": "passed"})
        rc, s, out = self.run_evals("--gate", "--baseline", base)
        self.assertEqual(rc, 0, out)
        self.assertEqual(s["pass_rate"], 0.5)   # overall, over everything that ran
        self.assertEqual((s["baseline_pass_rate"], s["current_pass_rate_on_common"],
                          s["common"]), (1.0, 1.0, 1))
        self.assertEqual((s["added_ids"], s["regressions"]), (["new-hard"], []))

    def test_a_passing_eval_that_disappears_fails_the_gate(self):
        self.write_eval("a", "say ok", [{"type": "output_regex", "regex": "ok"}])
        base = self.baseline({"a": "passed", "b-deleted": "passed", "c-was-red": "failed"})
        rc, s, out = self.run_evals("--gate", "--baseline", base)
        self.assertEqual(s["removed_passing"], ["b-deleted"])
        self.assertEqual(rc, 1, out)
        self.assertIn("missing now: b-deleted", out)
        rc, s, out = self.run_evals("--gate", "--baseline", base, "--allow-removed")
        self.assertEqual(rc, 0, out)

    def test_removed_ids_listed_and_no_common_eval_fails_the_gate(self):
        self.write_eval("a", "say ok", [{"type": "output_regex", "regex": "ok"}])
        base = self.baseline({"retired": "passed"})
        rc, s, out = self.run_evals("--gate", "--baseline", base)
        self.assertEqual((s["removed_ids"], s["added_ids"]), (["retired"], ["a"]))
        self.assertIsNone(s["baseline_pass_rate"])
        self.assertEqual(rc, 1, out)
        self.assertIn("checked nothing", out)

    def test_baseline_without_per_id_is_refused(self):
        self.write_eval("a", "say ok", [{"type": "output_regex", "regex": "ok"}])
        base = self.tmp / "base.json"
        base.write_text(json.dumps({"pass_rate": 0.8}))
        self.assertEqual(self.run_evals("--baseline", base)[0], 2)

    def test_baseline_json_beside_the_evals_is_not_an_eval(self):
        self.write_eval("a", "say ok", [{"type": "output_regex", "regex": "ok"}])
        (self.evals / "baseline.json").write_text(json.dumps(
            {"pass_rate": 1.0, "per_id": {"a": "passed"}, "recorded_at": "x"}))
        self.assertEqual(run_main("validate", self.evals)[0], 0)
        rc, s, out = self.run_evals("--gate", "--baseline", self.evals / "baseline.json")
        self.assertEqual((rc, s["ran"]), (0, 1), out)

    def test_gate_without_any_threshold_is_a_usage_error(self):
        self.write_eval("a", "say ok", [{"type": "output_regex", "regex": "ok"}])
        rc, s, out = self.run_evals("--gate")
        self.assertEqual(rc, 2)
        self.assertIn("checks nothing", out)
        self.assertEqual(self.calls(), [])


class TestCliErrorsAndBaseline(Base):
    def test_missing_claude_binary_exits_2(self):
        self.write_eval("a", "say ok", [{"type": "output_regex", "regex": "ok"}])
        rc, _, err = run_main("run", self.evals, "--repo", self.repo,
                              "--claude", self.tmp / "no-such-claude")
        self.assertEqual(rc, 2)
        self.assertIn("not found", err)
        rc, _, _ = run_main("run", self.evals, "--repo", self.repo,
                            "--claude", "definitely-not-a-claude-binary-xyz")
        self.assertEqual(rc, 2)

    def test_schema_error_in_run_exits_2_before_anything_runs(self):
        self.write_eval("a", "say ok", [{"type": "nope"}])
        rc, _, out = self.run_evals()
        self.assertEqual(rc, 2)
        self.assertIn("unknown check type", out)
        self.assertEqual(self.calls(), [])
        self.assertEqual(self.scratch_dirs(), [])

    def test_unknown_only_id_and_non_repo_exit_2(self):
        self.write_eval("a", "say ok", [{"type": "output_regex", "regex": "ok"}])
        self.assertEqual(self.run_evals("--only", "zzz")[0], 2)
        plain = self.tmp / "plain"
        plain.mkdir()
        rc, _, err = run_main("run", self.evals, "--repo", plain, "--claude", self.claude)
        self.assertEqual(rc, 2, err)

    def test_only_runs_just_the_named_eval(self):
        self.write_eval("a", "say ok", [{"type": "output_regex", "regex": "ok"}])
        self.write_eval("b", "say ok", [{"type": "output_regex", "regex": "ok"}])
        rc, s, _ = self.run_evals("--only", "b")
        self.assertEqual([r["id"] for r in s["results"]], ["b"])

    def test_baseline_writes_rate_per_id_and_timestamp(self):
        self.write_eval("a", "say ok", [{"type": "output_regex", "regex": "ok"}])
        self.write_eval("b", "say no", [{"type": "output_regex", "regex": "ok"}])
        results = self.tmp / "results.json"
        run_main("run", self.evals, "--repo", self.repo, "--claude", self.claude,
                 "--json", results)
        out = self.tmp / "baseline.json"
        rc, stdout, _ = run_main("baseline", results, "--out", out)
        self.assertEqual(rc, 0)
        data = json.loads(out.read_text())
        self.assertEqual(set(data), {"pass_rate", "per_id", "recorded_at"})
        self.assertEqual(data["per_id"], {"a": "passed", "b": "failed"})
        self.assertEqual(data["pass_rate"], 0.5)
        # and a run can gate against it
        rc, s, _ = self.run_evals("--baseline", out)
        self.assertEqual(s["baseline_pass_rate"], 0.5)

    def test_baseline_refuses_an_empty_or_malformed_results_file(self):
        empty = self.tmp / "r.json"
        empty.write_text(json.dumps({"pass_rate": 0.0, "results": []}))
        self.assertEqual(run_main("baseline", empty, "--out", self.tmp / "o")[0], 2)
        empty.write_text("{not json")
        self.assertEqual(run_main("baseline", empty, "--out", self.tmp / "o")[0], 2)
        self.assertFalse((self.tmp / "o").exists())

    def test_cli_shim_runs_as_a_subprocess(self):
        p = subprocess.run(["bash", str(REPO / "bin" / "bstack-evals"), "validate",
                            str(EXAMPLE)], capture_output=True, text=True,
                           stdin=subprocess.DEVNULL, timeout=60)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)


if __name__ == "__main__":
    unittest.main()
