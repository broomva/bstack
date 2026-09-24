"""Contract suite for scripts/test_lock.py — the test-lock mechanism (BRO-2542).

Every test builds its own throwaway git repository under a TemporaryDirectory,
with the operator's global and system git config switched off (GIT_CONFIG_GLOBAL
points at /dev/null), so nothing here reads or writes the real repository and
nothing touches the network.

Most tests drive the CLI as a subprocess, because that is how every consumer —
the PreToolUse hook, CI, a human — invokes it. A few call the parser directly
where the contract is a pure function (Bash write-target extraction).

Run from the repo root:  python3 -m unittest -v tests.test_test_lock
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "test_lock.py"
HOOK_SH = REPO / "scripts" / "test-lock-hook.sh"
BIN = REPO / "bin" / "bstack-test-lock"

sys.path.insert(0, str(REPO))
from scripts import test_lock as tl  # noqa: E402

TEST = "tests/test_bug.py"


class Repo:
    """A scratch repository with a committed reproduction test at TEST."""

    def __init__(self, tmp: str, *, seed: bool = True) -> None:
        self.tmp = os.path.realpath(tmp)
        self.root = os.path.join(self.tmp, "repo")
        os.makedirs(self.root)
        self.git("init", "-q")
        self.git("symbolic-ref", "HEAD", "refs/heads/main")
        self.git("config", "user.name", "Test Lock")
        self.git("config", "user.email", "test-lock@example.invalid")
        self.git("config", "commit.gpgsign", "false")
        if seed:
            self.write(TEST, "def test_bug():\n    assert fix() == 2\n")
            self.write("src/app.py", "def fix():\n    return 1\n")
            self.commit("seed")

    # -- environment -------------------------------------------------------
    def env(self, **extra: str) -> dict[str, str]:
        env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": self.tmp,
            "LANG": "C",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_EDITOR": "true",
            "GIT_MERGE_AUTOEDIT": "no",
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        env.update(extra)
        return env

    # -- repository helpers ------------------------------------------------
    def git(self, *args: str, cwd: str | None = None) -> str:
        p = subprocess.run(["git", *args], cwd=cwd or self.root, env=self.env(),
                           capture_output=True, text=True)
        if p.returncode != 0:
            raise AssertionError(f"git {' '.join(args)} failed: {p.stderr}")
        return p.stdout

    def write(self, rel: str, text: str) -> str:
        full = os.path.join(self.root, rel)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w") as fh:
            fh.write(text)
        return full

    def commit(self, msg: str, *trailers: str, allow_empty: bool = False) -> str:
        self.git("add", "-A")
        args = ["commit", "-q", "-m", msg]
        for t in trailers:
            args += ["--trailer", t]
        if allow_empty:
            args.append("--allow-empty")
        self.git(*args)
        return self.head()

    def head(self) -> str:
        return self.git("rev-parse", "HEAD").strip()

    def lock(self, *paths: str) -> str:
        return self.commit("lock the repro", *[f"Test-Lock: {p}" for p in paths], allow_empty=True)

    # -- CLI ---------------------------------------------------------------
    def run(self, *args: str, cwd: str | None = None, stdin: str | None = None,
            argv0: list[str] | None = None, **env: str) -> subprocess.CompletedProcess:
        cmd = (argv0 or [sys.executable, str(SCRIPT)]) + list(args)
        return subprocess.run(cmd, cwd=cwd or self.root, env=self.env(**env), input=stdin,
                              capture_output=True, text=True, timeout=60)

    def hook(self, tool: str, tool_input: dict, cwd: str | None = None, **env: str):
        payload = {"tool_name": tool, "tool_input": tool_input, "cwd": cwd or self.root}
        return self.run("hook", stdin=json.dumps(payload), **env)

    def bash(self, command: str, cwd: str | None = None, **env: str):
        return self.hook("Bash", {"command": command}, cwd=cwd, **env)

    def edit(self, path: str, cwd: str | None = None, tool: str = "Edit", key: str = "file_path"):
        return self.hook(tool, {key: path, "old_string": "a", "new_string": "b"}, cwd=cwd)


class Base(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.r = Repo(self._tmp.name)

    def assertExit(self, p: subprocess.CompletedProcess, code: int) -> None:
        self.assertEqual(p.returncode, code,
                         f"expected exit {code}, got {p.returncode}\nstdout: {p.stdout}\nstderr: {p.stderr}")


# --------------------------------------------------------------------------- #
# verify — committed history
# --------------------------------------------------------------------------- #
class VerifyTests(Base):
    def test_clean_when_only_the_code_changes(self):
        self.r.lock(TEST)
        self.r.write("src/app.py", "def fix():\n    return 2\n")
        self.r.commit("fix the bug")
        self.assertExit(self.r.run("verify"), 0)

    def test_later_edit_to_locked_test_fails_and_names_sha_path_route(self):
        lock = self.r.lock(TEST)
        self.r.write(TEST, "def test_bug():\n    assert True\n")
        bad = self.r.commit("make it pass")
        p = self.r.run("verify")
        self.assertExit(p, 1)
        self.assertIn(bad[:7], p.stdout)
        self.assertIn(TEST, p.stdout)
        self.assertIn(lock[:7], p.stdout)
        self.assertIn(f"Test-Unlock: {TEST}", p.stdout)
        self.assertIn("release route", p.stdout)

    def test_unlock_before_the_edit_releases(self):
        self.r.lock(TEST)
        self.r.commit("human: the expectation was wrong", f"Test-Unlock: {TEST}", allow_empty=True)
        self.r.write(TEST, "def test_bug():\n    assert fix() == 3\n")
        self.r.commit("correct the expectation")
        self.assertExit(self.r.run("verify"), 0)
        self.assertExit(self.r.run("check-path", TEST), 0)

    def test_unlock_after_the_edit_does_not_launder_it(self):
        self.r.lock(TEST)
        self.r.write(TEST, "def test_bug():\n    assert True\n")
        self.r.commit("weaken")
        self.r.commit("unlock after the fact", f"Test-Unlock: {TEST}", allow_empty=True)
        self.assertExit(self.r.run("verify"), 1)

    def test_unlock_on_the_editing_commit_itself_is_a_release(self):
        self.r.lock(TEST)
        self.r.write(TEST, "def test_bug():\n    assert fix() == 3\n")
        self.r.commit("correct the expectation", f"Test-Unlock: {TEST}")
        self.assertExit(self.r.run("verify"), 0)

    def test_unlock_of_another_path_does_not_release(self):
        self.r.lock(TEST)
        self.r.commit("unlock something else", "Test-Unlock: tests/other.py", allow_empty=True)
        self.r.write(TEST, "x = 1\n")
        self.r.commit("edit")
        self.assertExit(self.r.run("verify"), 1)

    def test_rename_of_locked_path_fails(self):
        self.r.lock(TEST)
        self.r.git("mv", TEST, "tests/test_moved.py")
        self.r.commit("move it out of the way")
        p = self.r.run("verify", "--json")
        self.assertExit(p, 1)
        kinds = {v["kind"] for v in json.loads(p.stdout)["violations"]}
        self.assertIn("renamed", kinds)

    def test_delete_of_locked_path_fails(self):
        self.r.lock(TEST)
        self.r.git("rm", "-q", TEST)
        self.r.commit("delete the failing test")
        p = self.r.run("verify", "--json")
        self.assertExit(p, 1)
        self.assertEqual([v["kind"] for v in json.loads(p.stdout)["violations"]], ["deleted"])

    def test_lock_commit_may_itself_add_the_test(self):
        self.r.write("tests/test_new.py", "def test_new():\n    assert 0\n")
        self.r.commit("repro", "Test-Lock: tests/test_new.py")
        self.assertExit(self.r.run("verify"), 0)
        self.assertExit(self.r.run("check-path", "tests/test_new.py"), 2)

    def test_directory_lock_covers_children(self):
        self.r.lock("tests")
        self.r.write("tests/conftest.py", "import pytest\n")
        self.r.commit("sneak a fixture in")
        self.assertExit(self.r.run("verify"), 1)
        self.assertExit(self.r.run("check-path", "tests/anything.py"), 2)
        self.assertExit(self.r.run("check-path", "testsuite/x.py"), 0)

    def test_json_shape(self):
        lock = self.r.lock(TEST)
        self.r.write(TEST, "x = 1\n")
        bad = self.r.commit("edit")
        p = self.r.run("verify", "--json")
        self.assertExit(p, 1)
        d = json.loads(p.stdout)
        self.assertFalse(d["ok"])
        self.assertEqual(d["locks"][0]["path"], TEST)
        v = d["violations"][0]
        self.assertEqual((v["sha"], v["path"], v["kind"], v["lock_sha"]), (bad, TEST, "modified", lock))
        self.assertIn("Test-Unlock", v["release_route"])

    def test_edit_then_restore_is_a_warning_edit_only_fails(self):
        original = Path(self.r.root, TEST).read_text()
        lock = self.r.lock(TEST)
        self.r.write(TEST, "def test_bug():\n    assert True\n")
        weaken = self.r.commit("weaken")
        self.assertExit(self.r.run("verify"), 1)  # edit only
        self.r.write(TEST, original)
        restore = self.r.commit("restore the repro")
        p = self.r.run("verify", "--json")
        self.assertExit(p, 0)
        d = json.loads(p.stdout)
        self.assertEqual(d["violations"], [])
        self.assertEqual(d["touched_and_restored"],
                         [{"path": TEST, "lock_sha": lock, "commits": [weaken, restore], "paths": [TEST]}])
        text = self.r.run("verify")
        self.assertExit(text, 0)
        self.assertIn("touched_and_restored", text.stdout)
        self.assertIn(weaken[:7], text.stdout)

    def test_relock_after_an_edit_does_not_launder_it(self):
        self.r.lock(TEST)
        self.r.write(TEST, "def test_bug():\n    assert True\n")
        self.assertExit(self.r.run("commit", TEST, "-m", "re-pin the weakened test"), 0)
        self.assertExit(self.r.run("verify"), 1)

    def test_relock_reports_one_violation_against_the_latest_lock(self):
        self.r.lock(TEST)
        relock = self.r.lock(TEST)  # re-pins the same content
        self.r.write(TEST, "x = 1\n")
        self.r.commit("edit")
        p = self.r.run("verify", "--json")
        self.assertExit(p, 1)
        self.assertEqual([v["lock_sha"] for v in json.loads(p.stdout)["violations"]], [relock])

    def test_bad_base_is_a_usage_error_not_a_pass(self):
        self.r.lock(TEST)
        self.assertExit(self.r.run("verify", "--base", "no-such-ref"), 2)


class DriftTests(Base):
    """A merge carries no per-commit change list: the content comparison is the
    only thing standing between a side-branch edit and a green verify."""

    def _merged_side_edit(self) -> tuple[str, str]:
        fork = self.r.head()
        lock = self.r.lock(TEST)
        self.r.git("checkout", "-q", "-b", "side", fork)
        self.r.write(TEST, "def test_bug():\n    assert True\n")
        side = self.r.commit("weaken on a side branch")
        self.r.git("checkout", "-q", "main")
        self.r.git("merge", "-q", "--no-ff", "--no-edit", "side")
        return lock, side

    def test_side_branch_edit_merged_after_lock_fails_verify(self):
        self._merged_side_edit()
        self.assertExit(self.r.run("verify"), 1)

    def test_content_drift_is_caught_when_the_walk_orders_the_edit_first(self):
        lock, side = self._merged_side_edit()
        env = self.r.env()
        with mock.patch.dict(os.environ, env, clear=True):
            s = tl.scan(self.r.root, with_changes=True)
            # Force the order in which the per-commit walk cannot attribute the
            # side edit to the lock: side commit first, lock second.
            order = {side: 0, lock: 1}
            s.commits.sort(key=lambda c: order.get(c.sha, -1 if c.subject == "seed" else 2))
            _, violations, _, _ = tl.committed_violations(s)
        self.assertEqual([v.kind for v in violations], ["content-drift"])
        self.assertEqual(violations[0].lock_sha, lock)


# --------------------------------------------------------------------------- #
# verify --worktree
# --------------------------------------------------------------------------- #
class WorktreeTests(Base):
    def test_uncommitted_edit_caught_only_with_worktree(self):
        self.r.lock(TEST)
        self.r.write(TEST, "def test_bug():\n    assert True\n")
        self.assertExit(self.r.run("verify"), 0)
        p = self.r.run("verify", "--worktree")
        self.assertExit(p, 1)
        self.assertIn(f"git checkout HEAD -- {TEST}", p.stdout)
        self.r.git("checkout", "HEAD", "--", TEST)
        self.assertExit(self.r.run("verify", "--worktree"), 0)

    def test_staged_edit_caught(self):
        self.r.lock(TEST)
        self.r.write(TEST, "x = 1\n")
        self.r.git("add", TEST)
        p = self.r.run("verify", "--worktree", "--json")
        self.assertExit(p, 1)
        self.assertIn("staged", {v["kind"] for v in json.loads(p.stdout)["violations"]})

    def test_worktree_delete_and_untracked_under_locked_dir_caught(self):
        self.r.lock("tests")
        os.remove(os.path.join(self.r.root, TEST))
        self.r.write("tests/conftest.py", "x = 1\n")
        p = self.r.run("verify", "--worktree", "--json")
        self.assertExit(p, 1)
        self.assertEqual({v["kind"] for v in json.loads(p.stdout)["violations"]}, {"deleted", "untracked"})

    def test_unrelated_worktree_edit_is_clean(self):
        self.r.lock(TEST)
        self.r.write("src/app.py", "def fix():\n    return 2\n")
        self.assertExit(self.r.run("verify", "--worktree"), 0)


# --------------------------------------------------------------------------- #
# list, range and base
# --------------------------------------------------------------------------- #
class RangeTests(Base):
    def test_list_shows_path_sha_subject(self):
        lock = self.r.lock(TEST)
        p = self.r.run("list")
        self.assertExit(p, 0)
        self.assertIn(f"{lock[:7]}  {TEST}  lock the repro", p.stdout)
        d = json.loads(self.r.run("list", "--json").stdout)
        self.assertEqual(d["locks"], [{"path": TEST, "sha": lock, "short": lock[:7],
                                       "subject": "lock the repro"}])

    def test_several_locks_in_one_commit(self):
        self.r.write("tests/test_two.py", "def test_two():\n    assert 0\n")
        self.r.commit("second repro")
        lock = self.r.lock(TEST, "tests/test_two.py")
        d = json.loads(self.r.run("list", "--json").stdout)
        self.assertEqual(sorted(x["path"] for x in d["locks"]), sorted([TEST, "tests/test_two.py"]))
        self.assertTrue(all(x["sha"] == lock for x in d["locks"]))
        self.assertExit(self.r.edit(os.path.join(self.r.root, TEST)), 2)
        self.assertExit(self.r.edit(os.path.join(self.r.root, "tests/test_two.py")), 2)

    def test_lock_before_base_is_inactive(self):
        self.r.lock(TEST)
        base = self.r.commit("merged upstream", allow_empty=True)
        self.r.write(TEST, "x = 1\n")
        self.r.commit("edit after the base")
        self.assertExit(self.r.run("check-path", TEST), 2)  # whole history: active
        self.assertExit(self.r.run("check-path", TEST, "--base", base), 0)
        self.assertExit(self.r.run("verify", "--base", base), 0)
        self.assertIn("no active test locks", self.r.run("list", BSTACK_TEST_LOCK_BASE=base).stdout)
        self.assertExit(self.r.edit(os.path.join(self.r.root, TEST)), 2)
        self.assertExit(self.r.run("hook", stdin=json.dumps({
            "tool_name": "Edit", "tool_input": {"file_path": os.path.join(self.r.root, TEST)},
            "cwd": self.r.root}), BSTACK_TEST_LOCK_BASE=base), 0)

    def test_default_base_is_merge_base_with_origin_main(self):
        self.r.lock(TEST)
        self.r.git("update-ref", "refs/remotes/origin/main", self.r.head())
        self.r.commit("feature work", allow_empty=True)
        d = json.loads(self.r.run("list", "--json").stdout)
        self.assertEqual(d["locks"], [])
        self.assertIn("origin/main", d["base_source"])

    def test_max_commits_caps_the_walk(self):
        self.r.lock(TEST)
        self.r.commit("one", allow_empty=True)
        self.r.commit("two", allow_empty=True)
        d = json.loads(self.r.run("list", "--json", "--max-commits", "2").stdout)
        self.assertEqual(d["locks"], [])
        self.assertTrue(d["truncated"])
        self.assertEqual(len(json.loads(self.r.run("list", "--json").stdout)["locks"]), 1)

    def test_malformed_trailer_is_warned_not_honored(self):
        self.r.commit("bad lock", "Test-Lock: ../outside.py", allow_empty=True)
        d = json.loads(self.r.run("list", "--json").stdout)
        self.assertEqual(d["locks"], [])
        self.assertTrue(any("malformed" in w for w in d["warnings"]))

    def test_trailer_path_is_normalized(self):
        lock = self.r.commit("hand-written lock", "Test-Lock: ./tests//test_bug.py/", allow_empty=True)
        d = json.loads(self.r.run("list", "--json").stdout)
        self.assertEqual([(x["path"], x["sha"]) for x in d["locks"]], [(TEST, lock)])
        self.assertExit(self.r.edit(os.path.join(self.r.root, TEST)), 2)

    def test_not_a_repo_is_a_usage_error_for_list_and_verify(self):
        outside = os.path.join(self.r.tmp, "plain")
        os.makedirs(outside)
        self.assertExit(self.r.run("list", cwd=outside), 2)
        self.assertExit(self.r.run("verify", cwd=outside), 2)


# --------------------------------------------------------------------------- #
# check-path
# --------------------------------------------------------------------------- #
class CheckPathTests(Base):
    def test_relative_absolute_and_dot_forms(self):
        self.r.lock(TEST)
        for form in (TEST, "./" + TEST, os.path.join(self.r.root, TEST), "tests//./test_bug.py"):
            with self.subTest(form=form):
                self.assertExit(self.r.run("check-path", form), 2)
        self.assertExit(self.r.run("check-path", "src/app.py"), 0)
        self.assertExit(self.r.run("check-path", os.path.join(self.r.root, "src/app.py")), 0)

    def test_from_a_subdirectory_both_readings_are_checked(self):
        self.r.lock(TEST)
        sub = os.path.join(self.r.root, "src")
        self.assertExit(self.r.run("check-path", "../" + TEST, cwd=sub), 2)
        self.assertExit(self.r.run("check-path", TEST, cwd=sub), 2)
        self.assertExit(self.r.run("check-path", "app.py", cwd=sub), 0)

    def test_outside_any_repo_is_unlocked(self):
        outside = os.path.join(self.r.tmp, "plain")
        os.makedirs(outside)
        self.assertExit(self.r.run("check-path", "x.py", cwd=outside), 0)


# --------------------------------------------------------------------------- #
# commit
# --------------------------------------------------------------------------- #
class CommitTests(Base):
    def test_nothing_staged_creates_an_empty_lock_commit(self):
        before = self.r.head()
        p = self.r.run("commit", TEST, "-m", "lock the repro")
        self.assertExit(p, 0)
        head = self.r.head()
        self.assertNotEqual(head, before)
        self.assertEqual(self.r.git("show", "--name-only", "--format=", head).strip(), "")
        self.assertEqual(self.r.git("log", "-1", "--format=%(trailers:key=Test-Lock,valueonly)").strip(), TEST)
        self.assertExit(self.r.run("check-path", TEST), 2)

    def test_new_test_is_committed_alone_other_staged_work_stays_staged(self):
        self.r.write("tests/test_new.py", "def test_new():\n    assert 0\n")
        self.r.write("src/app.py", "def fix():\n    return 2\n")
        self.r.git("add", "src/app.py")
        self.assertExit(self.r.run("commit", "tests/test_new.py", "-m", "repro"), 0)
        self.assertEqual(self.r.git("show", "--name-only", "--format=", "HEAD").split(), ["tests/test_new.py"])
        self.assertIn("M  src/app.py", self.r.git("status", "--porcelain"))

    def test_one_trailer_per_path(self):
        self.r.write("tests/test_two.py", "x = 1\n")
        self.assertExit(self.r.run("commit", TEST, "./tests/test_two.py", "-m", "two repros"), 0)
        trailers = self.r.git("log", "-1", "--format=%(trailers:key=Test-Lock,valueonly)").split()
        self.assertEqual(sorted(trailers), sorted([TEST, "tests/test_two.py"]))

    def test_missing_path_exits_2_and_commits_nothing(self):
        before = self.r.head()
        self.assertExit(self.r.run("commit", "tests/nope.py", "-m", "x"), 2)
        self.assertEqual(self.r.head(), before)


# --------------------------------------------------------------------------- #
# hook — file tools
# --------------------------------------------------------------------------- #
class HookFileTests(Base):
    def test_edit_on_locked_path_blocks_with_the_message(self):
        lock = self.r.lock(TEST)
        p = self.r.edit(os.path.join(self.r.root, TEST))
        self.assertExit(p, 2)
        self.assertEqual(p.stderr.strip(), (
            f"BLOCKED (test-lock): {TEST} is locked by {lock[:7]} (Test-Lock trailer). "
            f"Fix the code, not the test. Releasing it takes a commit with "
            f"'Test-Unlock: {TEST}' — a human decision visible in review."))

    def test_edit_on_unlocked_path_allows(self):
        self.r.lock(TEST)
        p = self.r.edit(os.path.join(self.r.root, "src/app.py"))
        self.assertExit(p, 0)
        self.assertEqual(p.stderr, "")

    def test_every_file_tool_is_guarded(self):
        self.r.lock(TEST)
        full = os.path.join(self.r.root, TEST)
        for tool, key in (("Write", "file_path"), ("MultiEdit", "file_path"), ("NotebookEdit", "notebook_path")):
            with self.subTest(tool=tool):
                self.assertExit(self.r.edit(full, tool=tool, key=key), 2)

    def test_relative_file_path_resolves_against_cwd(self):
        self.r.lock(TEST)
        self.assertExit(self.r.edit("test_bug.py", cwd=os.path.join(self.r.root, "tests")), 2)

    def test_symlink_alias_to_locked_file_blocks(self):
        self.r.lock(TEST)
        os.symlink(os.path.join(self.r.root, TEST), os.path.join(self.r.root, "alias.py"))
        self.assertExit(self.r.edit(os.path.join(self.r.root, "alias.py")), 2)

    def test_case_folded_path_blocks_when_git_ignores_case(self):
        self.r.lock(TEST)
        self.r.git("config", "core.ignorecase", "true")
        self.assertExit(self.r.edit(os.path.join(self.r.root, "Tests/Test_Bug.py")), 2)

    def test_not_a_repo_allows(self):
        outside = os.path.join(self.r.tmp, "plain")
        os.makedirs(outside)
        p = self.r.edit(os.path.join(outside, "x.py"), cwd=outside)
        self.assertExit(p, 0)
        self.assertEqual(p.stderr, "")

    def test_repo_without_locks_allows(self):
        self.assertExit(self.r.edit(os.path.join(self.r.root, TEST)), 0)

    def test_other_tools_are_ignored(self):
        self.r.lock(TEST)
        self.assertExit(self.r.hook("Read", {"file_path": os.path.join(self.r.root, TEST)}), 0)


# --------------------------------------------------------------------------- #
# hook — Bash (best effort)
# --------------------------------------------------------------------------- #
class HookBashTests(Base):
    def setUp(self) -> None:
        super().setUp()
        self.lock = self.r.lock(TEST)

    def test_sed_in_place_blocks(self):
        p = self.r.bash(f"sed -i s/a/b/ {TEST}")
        self.assertExit(p, 2)
        self.assertIn(f"{TEST} is locked by {self.lock[:7]}", p.stderr)

    def test_reads_and_test_runs_are_allowed(self):
        for cmd in (f"cat {TEST}", f"pytest {TEST} 2>&1 | tee /tmp/test-lock-run.log",
                    f"cat {TEST} > /tmp/copy.py", f"grep -n assert {TEST}", f"sed -n 1p {TEST}",
                    f"cp {TEST} /tmp/snapshot.py", "git checkout -b feature", "ls tests > /tmp/ls.txt"):
            with self.subTest(cmd=cmd):
                self.assertExit(self.r.bash(cmd), 0)

    def test_write_constructs_block(self):
        for cmd in (f"echo 'assert True' > {TEST}", f"printf x >> ./{TEST}", f"echo x | tee -a {TEST}",
                    f"perl -pi -e 's/2/1/' {TEST}", f"mv {TEST} /tmp/gone.py", f"cp /tmp/x.py {TEST}",
                    f"rm -f {TEST}", "rm -rf tests", f"truncate -s 0 {TEST}", f"dd if=/dev/null of={TEST}",
                    f"git checkout HEAD~1 -- {TEST}", f"git restore --source=HEAD~1 {TEST}",
                    f"git rm {TEST}", f"cd tests && sed -i s/2/1/ test_bug.py",
                    f"bash -c 'echo pass > {TEST}'", "find tests -name '*.py' -delete",
                    "cp /tmp/x.py tests/*.py"):
            with self.subTest(cmd=cmd):
                self.assertExit(self.r.bash(cmd), 2)

    def test_restoring_the_committed_content_is_allowed(self):
        for cmd in (f"git checkout HEAD -- {TEST}", f"git checkout -- {TEST}", f"git restore {TEST}",
                    f"git restore --staged --worktree --source=HEAD {TEST}"):
            with self.subTest(cmd=cmd):
                self.assertExit(self.r.bash(cmd), 0)

    def test_committing_a_release_is_not_the_agents_call(self):
        p = self.r.bash(f'git commit --allow-empty -m wip --trailer "Test-Unlock: {TEST}"')
        self.assertExit(p, 2)
        self.assertIn("human decision", p.stderr)

    def test_untokenizable_command_falls_back_to_the_coarse_rule(self):
        self.assertExit(self.r.bash(f"echo 'unterminated > {TEST}"), 2)
        self.assertExit(self.r.bash("echo 'unterminated"), 0)


class BashParserUnitTests(unittest.TestCase):
    """The extraction contract, without git."""

    def targets(self, cmd: str, cwd: str = "/r") -> list[tuple[str, str]]:
        out = tl.bash_write_targets(cmd, cwd)
        assert out is not None
        return out

    def test_fd_duplication_is_not_a_file(self):
        self.assertEqual(self.targets("pytest t.py 2>&1"), [])
        self.assertEqual(self.targets("pytest t.py 2>&1 >/dev/null"), [("/dev/null", "/r")])

    def test_cd_changes_the_resolution_directory(self):
        self.assertEqual(self.targets("cd sub && rm x.py"), [("x.py", "/r/sub")])

    def test_copy_into_an_existing_directory_writes_the_basename(self):
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "tests"))
            self.assertEqual(self.targets("cp -r fixtures tests", cwd=d), [("tests/fixtures", d)])

    def test_unparseable_returns_none(self):
        self.assertIsNone(tl.bash_write_targets("echo 'open", "/r"))


# --------------------------------------------------------------------------- #
# git hygiene — a repository's config must not get to run code
# --------------------------------------------------------------------------- #
class GitHygieneTests(Base):
    def _marker_program(self) -> tuple[str, str]:
        marker = os.path.join(self.r.tmp, "ran")
        prog = os.path.join(self.r.tmp, "prog.sh")
        with open(prog, "w") as fh:
            fh.write(f'#!/bin/sh\necho "$0 $*" >> "{marker}"\nexit 1\n')
        os.chmod(prog, 0o755)
        return prog, marker

    def _signed_lock_commit(self) -> str:
        """A lock commit carrying a (bogus) signature header, so a `git log` that
        honours log.showSignature has to call gpg.program on it."""
        tree = self.r.git("rev-parse", "HEAD^{tree}").strip()
        body = (f"tree {tree}\nparent {self.r.head()}\n"
                "author T <t@example.invalid> 1700000000 +0000\n"
                "committer T <t@example.invalid> 1700000000 +0000\n"
                "gpgsig -----BEGIN PGP SIGNATURE-----\n \n iQEzBAABCAAdFiEE\n -----END PGP SIGNATURE-----\n"
                f"\nsigned lock\n\nTest-Lock: {TEST}\n")
        p = subprocess.run(["git", "hash-object", "-t", "commit", "-w", "--stdin"], cwd=self.r.root,
                           env=self.r.env(), input=body, capture_output=True, text=True, check=True)
        sha = p.stdout.strip()
        self.r.git("update-ref", "HEAD", sha)
        return sha

    def test_fsmonitor_and_signature_programs_never_run(self):
        prog, marker = self._marker_program()
        self._signed_lock_commit()
        self.r.git("config", "log.showSignature", "true")
        self.r.git("config", "gpg.program", prog)
        self.r.git("config", "core.fsmonitor", prog)
        self.r.write(TEST, "x = 1\n")  # dirty the worktree so index reads happen
        self.r.write("tests/test_new.py", "y = 1\n")
        self.assertExit(self.r.run("list"), 0)
        self.assertExit(self.r.run("verify", "--worktree"), 1)
        self.assertExit(self.r.run("check-path", TEST), 2)
        self.assertExit(self.r.edit(os.path.join(self.r.root, TEST)), 2)
        self.assertExit(self.r.run("commit", "tests/test_new.py", "-m", "second repro"), 0)
        ran = Path(marker).read_text() if os.path.exists(marker) else ""
        self.assertEqual(ran, "", f"a repo-configured program ran: {ran}")

    def test_git_sees_only_the_filtered_environment(self):
        dump = os.path.join(self.r.tmp, "env.txt")
        hook = os.path.join(self.r.root, ".git", "hooks", "pre-commit")
        with open(hook, "w") as fh:
            fh.write(f'#!/bin/sh\nenv > "{dump}"\n')
        os.chmod(hook, 0o755)
        self.assertExit(self.r.run("commit", TEST, "-m", "lock", SECRET_TOKEN="do-not-leak"), 0)
        names = {line.split("=", 1)[0] for line in Path(dump).read_text().splitlines() if "=" in line}
        self.assertNotIn("SECRET_TOKEN", names)
        self.assertNotIn("PYTHONDONTWRITEBYTECODE", names)
        self.assertIn("GIT_CONFIG_GLOBAL", names)  # GIT_* crosses over
        self.assertIn("HOME", names)


class SigningEnvTests(Base):
    """SSH_AUTH_SOCK, GNUPGHOME and GPG_TTY reach `git commit` so signing works,
    and reach no git call on the hook, verify, list or check-path paths."""

    SIGNING = {"SSH_AUTH_SOCK": "/tmp/agent.sock", "GNUPGHOME": "/tmp/gnupg", "GPG_TTY": "/dev/ttys999"}

    def _spy(self) -> tuple[str, str]:
        real = shutil.which("git")
        self.assertIsNotNone(real)
        bindir = os.path.join(self.r.tmp, "spybin")
        os.makedirs(bindir)
        dump = os.path.join(self.r.tmp, "git-env.txt")
        with open(os.path.join(bindir, "git"), "w") as fh:
            fh.write(f'#!/bin/sh\n{{ echo "=== $*"; env; }} >> "{dump}"\nexec "{real}" "$@"\n')
        os.chmod(os.path.join(bindir, "git"), 0o755)
        return bindir + os.pathsep + os.environ.get("PATH", "/usr/bin:/bin"), dump

    def _calls(self, dump: str) -> list[tuple[str, set[str]]]:
        calls = []
        for block in Path(dump).read_text().split("=== ")[1:]:
            argv, _, rest = block.partition("\n")
            calls.append((argv, {ln.split("=", 1)[0] for ln in rest.splitlines() if "=" in ln}))
        return calls

    def test_hook_verify_list_check_path_never_see_signing_env(self):
        path, dump = self._spy()
        self.r.lock(TEST)
        env = dict(self.SIGNING, PATH=path)
        self.assertExit(self.r.run("hook", stdin=json.dumps({
            "tool_name": "Edit", "cwd": self.r.root,
            "tool_input": {"file_path": os.path.join(self.r.root, TEST)}}), **env), 2)
        self.assertExit(self.r.bash(f"sed -i s/a/b/ {TEST}", **env), 2)
        self.assertExit(self.r.run("verify", "--worktree", **env), 0)
        self.assertExit(self.r.run("list", **env), 0)
        self.assertExit(self.r.run("check-path", TEST, **env), 2)
        calls = self._calls(dump)
        self.assertGreater(len(calls), 5)
        for argv, names in calls:
            self.assertFalse(names & set(self.SIGNING), f"signing env reached: git {argv}")

    def test_commit_passes_signing_env_to_git_commit(self):
        path, dump = self._spy()
        self.assertExit(self.r.run("commit", TEST, "-m", "lock", PATH=path, **self.SIGNING), 0)
        commit_calls = [names for argv, names in self._calls(dump) if " commit " in f" {argv} "]
        self.assertEqual(len(commit_calls), 1)
        self.assertTrue(set(self.SIGNING) <= commit_calls[0])


# --------------------------------------------------------------------------- #
# hook — failure handling and the shipped entry points
# --------------------------------------------------------------------------- #
class HookFailOpenTests(Base):
    def test_malformed_json_allows_with_one_line_warning(self):
        self.r.lock(TEST)
        for raw in ("{not json", "", "[1, 2]"):
            with self.subTest(raw=raw):
                p = self.r.run("hook", stdin=raw)
                self.assertExit(p, 0)
                self.assertTrue(p.stderr.startswith("test-lock: hook error, allowing"), p.stderr)
                self.assertEqual(len(p.stderr.strip().splitlines()), 1)

    def test_internal_error_allows_with_warning(self):
        self.r.lock(TEST)
        p = self.r.hook("Edit", {"file_path": os.path.join(self.r.root, TEST)},
                        BSTACK_TEST_LOCK_BASE="no-such-ref")
        self.assertExit(p, 0)
        self.assertIn("hook error, allowing", p.stderr)

    def test_shell_hook_wrapper_blocks(self):
        self.r.lock(TEST)
        payload = json.dumps({"tool_name": "Edit", "cwd": self.r.root,
                              "tool_input": {"file_path": os.path.join(self.r.root, TEST)}})
        p = self.r.run(stdin=payload, argv0=["bash", str(HOOK_SH)])
        self.assertExit(p, 2)
        self.assertIn("BLOCKED (test-lock)", p.stderr)

    def test_bin_shim_dispatches(self):
        lock = self.r.lock(TEST)
        p = self.r.run("list", argv0=["bash", str(BIN)])
        self.assertExit(p, 0)
        self.assertIn(lock[:7], p.stdout)


if __name__ == "__main__":
    unittest.main()
