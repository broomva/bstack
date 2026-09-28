"""Tests for scripts/plan_drift.py (BRO-2542).

Every test builds its own git repository under a temp dir, with HOME and git's
global/system config pointed away from the operator's machine. No network, no
real repo. Each firing case is paired with the case that proves the checker is
not simply reporting drift on everything.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))
import plan_drift  # noqa: E402

PLAN_AB = """# Plan: demo

## Goal
Ship a and b.

## Files that change
- `src/a.py` — add the parser
- `src/b.py` — wire it in

## Risks
None.
"""


class RepoCase(unittest.TestCase):
    """A temp repo on `main` with one base commit, then a `feature` branch."""

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.td = Path(self._td.name)
        self.repo = self.td / "repo"
        self.repo.mkdir()
        home = self.td / "home"
        home.mkdir()
        (self.td / "gitconfig").write_text("")
        env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        env.pop("CLAUDE_CONFIG_DIR", None)
        env.update({
            "HOME": str(home),
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": str(self.td / "gitconfig"),
            "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
            "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid",
        })
        self._env = mock.patch.dict(os.environ, env, clear=True)
        self._env.start()
        self.git("init", "-q", "-b", "main")
        self.write("README.md", "base\n")
        self.write("src/a.py", "a = 0\n")
        self.write("src/b.py", "b = 0\n")
        self.commit("base")
        self.git("checkout", "-q", "-b", "feature")

    def tearDown(self):
        self._env.stop()
        self._td.cleanup()

    def git(self, *args: str) -> str:
        p = subprocess.run(["git", "-c", "core.fsmonitor=false", "-C", str(self.repo), *args],
                           capture_output=True, text=True, stdin=subprocess.DEVNULL)
        if p.returncode != 0:
            raise AssertionError(f"git {args} failed: {p.stderr}")
        return p.stdout

    def write(self, rel: str, text: str) -> None:
        p = self.repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)

    def commit(self, msg: str) -> str:
        self.git("add", "-A")
        self.git("commit", "-q", "-m", msg)
        return self.git("rev-parse", "HEAD").strip()

    def run_cli(self, *args: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = plan_drift.main(["--repo", str(self.repo), *args])
        return rc, out.getvalue(), err.getvalue()

    def report(self, *args: str) -> dict:
        rc, out, err = self.run_cli("--base", "main", "--json", *args)
        self.assertEqual(rc, 0, err)
        return json.loads(out)


class TestMatchAndDrift(RepoCase):
    def test_match_when_diff_equals_plan(self):
        self.write("plan.md", PLAN_AB)
        self.write("src/a.py", "a = 1\n")
        self.write("src/b.py", "b = 1\n")
        self.commit("implement per plan")
        r = self.report()
        self.assertEqual(r["status"], "match", r)
        self.assertEqual(r["plan"], "plan.md")
        self.assertEqual(r["plan_source"], "changed-in-range")
        self.assertEqual(r["unplanned"], [])
        self.assertEqual(r["untouched"], [])
        self.assertEqual(r["sync_violations"], [])
        self.assertEqual(r["match_ratio"], 1.0)

    def test_unplanned_file_is_drift(self):
        self.write("plan.md", PLAN_AB)
        self.write("src/a.py", "a = 1\n")
        self.write("src/b.py", "b = 1\n")
        self.commit("plan")
        self.write("src/c.py", "c = 1\n")
        self.commit("sneak in c")
        r = self.report()
        self.assertEqual(r["status"], "drift")
        self.assertEqual(r["unplanned"], ["src/c.py"])
        self.assertEqual(r["match_ratio"], round(2 / 3, 4))

    def test_untouched_literal_entry_is_drift(self):
        self.write("plan.md", PLAN_AB)
        self.write("src/a.py", "a = 1\n")
        self.commit("only a")
        r = self.report()
        self.assertEqual(r["status"], "drift")
        self.assertEqual(r["untouched"], ["src/b.py"])
        self.assertEqual(r["unplanned"], [])

    def test_human_output_names_each_finding(self):
        self.write("plan.md", PLAN_AB)
        self.write("src/a.py", "a = 1\n")
        self.commit("plan + a")
        self.write("src/c.py", "c = 1\n")
        self.commit("c, silently")
        rc, out, _ = self.run_cli("--base", "main")
        self.assertEqual(rc, 0)
        self.assertIn("plan-drift: drift", out)
        self.assertIn("UNPLANNED  src/c.py", out)
        self.assertIn("UNTOUCHED  src/b.py", out)
        self.assertIn("SYNC", out)


class TestSameCommitRule(RepoCase):
    def test_sync_satisfied_when_plan_updated_in_same_commit(self):
        self.write("plan.md", PLAN_AB)
        self.write("src/a.py", "a = 1\n")
        self.write("src/b.py", "b = 1\n")
        self.commit("plan + a + b")
        self.write("src/c.py", "c = 1\n")
        self.write("plan.md", PLAN_AB.replace("- `src/b.py` — wire it in",
                                              "- `src/b.py` — wire it in\n- `src/c.py` — departure"))
        self.commit("depart, and say so in the same commit")
        r = self.report()
        self.assertEqual(r["sync_violations"], [])
        self.assertEqual(r["status"], "match", r)

    def test_sync_violated_even_when_a_later_commit_fixes_the_plan(self):
        # The final diff matches the final plan, so only the per-commit check can
        # see that c.py landed one commit before the plan admitted it.
        self.write("plan.md", PLAN_AB)
        self.write("src/a.py", "a = 1\n")
        self.write("src/b.py", "b = 1\n")
        self.commit("plan + a + b")
        self.write("src/c.py", "c = 1\n")
        bad = self.commit("depart silently")
        self.write("plan.md", PLAN_AB.replace("- `src/b.py` — wire it in",
                                              "- `src/b.py` — wire it in\n- `src/c.py` — departure"))
        self.commit("catch the plan up afterwards")
        r = self.report()
        self.assertEqual(r["unplanned"], [])
        self.assertEqual([v["sha"] for v in r["sync_violations"]], [bad])
        self.assertEqual(r["sync_violations"][0]["unplanned"], ["src/c.py"])
        self.assertEqual(r["sync_violations"][0]["plan_at_commit"], "present")
        self.assertEqual(r["status"], "drift")


class TestParsing(RepoCase):
    def test_glob_entries_cover_nested_paths(self):
        self.write("plan.md", "## Files that change\n- `src/**/*.py`\n- `docs/*.md`\n")
        self.write("src/a.py", "a = 1\n")
        self.write("src/deep/er/x.py", "x = 1\n")
        self.write("docs/guide.md", "g\n")
        self.commit("globbed")
        r = self.report()
        self.assertEqual(r["status"], "match", r)
        self.assertEqual(r["untouched"], [], "a glob promises no particular file")

    def test_single_star_does_not_cross_a_directory(self):
        self.write("plan.md", "## Files that change\n- `src/*.py`\n")
        self.write("src/a.py", "a = 1\n")
        self.write("src/deep/x.py", "x = 1\n")
        self.commit("one level only")
        r = self.report()
        self.assertEqual(r["unplanned"], ["src/deep/x.py"])

    def test_heading_variants(self):
        for heading in ("## Files that change", "### FILES THAT CHANGE",
                        "## Files that change (expected)"):
            found, entries = plan_drift.parse_plan(f"# P\n{heading}\n- `x/y.py`\n## Next\n- `z.py`\n")
            self.assertTrue(found, heading)
            self.assertEqual(entries, ["x/y.py"], heading)
        for heading in ("# Files that change", "#### Files that change", "## Changed files"):
            found, entries = plan_drift.parse_plan(f"{heading}\n- `x/y.py`\n")
            self.assertFalse(found, heading)
            self.assertEqual(entries, [], heading)

    def test_entry_forms_backticks_commas_and_prose(self):
        text = (
            "## Files that change\n"
            "- `scripts/a.py`, `scripts/b.py` — the two tools, e.g. the parser.\n"
            "* bin/tool (new shim)\n"
            "tests/x.py, tests/y.py tests/z.py\n"
            "The wrapper also changes. See docs/\n"
            "```\n# not a heading\nfenced/path.txt\n```\n"
            "## After\n- `outside.py`\n"
        )
        found, entries = plan_drift.parse_plan(text)
        self.assertTrue(found)
        self.assertEqual(entries, [
            "scripts/a.py", "scripts/b.py", "bin/tool", "tests/x.py", "tests/y.py",
            "tests/z.py", "docs/", "fenced/path.txt",
        ])

    def test_missing_section_is_drift_with_a_warning(self):
        self.write("plan.md", "# Plan\n#### Files that change\n- `src/a.py`\n")
        self.write("src/a.py", "a = 1\n")
        self.commit("level-4 heading does not count")
        r = self.report()
        self.assertFalse(r["section_found"])
        self.assertEqual(r["status"], "drift")
        self.assertIn("plan has no '## Files that change' section", r["drift_reasons"])


class TestDiscovery(RepoCase):
    def test_pr_body_names_the_plan(self):
        self.write("docs/design/feature.md", PLAN_AB)
        self.commit("plan lives outside the discovery globs")
        self.git("branch", "-f", "main", "HEAD")
        self.write("src/a.py", "a = 1\n")
        self.write("src/b.py", "b = 1\n")
        self.commit("implement")
        body = self.td / "body.md"
        body.write_text("## Summary\nDoes the thing.\n\nPlan: `docs/design/feature.md`\n")
        r = self.report("--pr-body", str(body))
        self.assertEqual(r["plan"], "docs/design/feature.md")
        self.assertEqual(r["plan_source"], "pr-body")
        self.assertEqual(r["status"], "match", r)

    def test_a_pr_body_plan_outside_the_checkout_is_never_read(self):
        outside = self.td / "outside.md"
        outside.write_text(PLAN_AB)
        self.write("src/a.py", "a = 1\n")
        self.write("src/b.py", "b = 1\n")
        (self.repo / "docs").mkdir()
        (self.repo / "docs" / "link.md").symlink_to(outside)
        self.commit("implement, with a symlink that leaves the repo")
        body = self.td / "body.md"
        for ref in (str(outside), "../outside.md", "docs/link.md"):
            body.write_text(f"Does the thing.\n\nPlan: `{ref}`\n")
            r = self.report("--pr-body", str(body))
            self.assertEqual(r["status"], "no_plan", (ref, r))
            self.assertEqual(r["pr_plan_rejected"], ref)
            self.assertIsNone(r["plan"])

    def test_explicit_plan_flag_wins(self):
        self.write("plan.md", "## Files that change\n- `src/b.py`\n")
        self.write("docs/plans/real.md", PLAN_AB)
        self.write("src/a.py", "a = 1\n")
        self.write("src/b.py", "b = 1\n")
        self.commit("two plans")
        r = self.report("--plan", "docs/plans/real.md")
        self.assertEqual(r["plan_source"], "flag")
        # plan.md is an ordinary changed file when it is not the chosen plan.
        self.assertEqual(r["unplanned"], ["plan.md"])

    def test_no_plan_exits_zero_even_under_strict(self):
        self.write("src/a.py", "a = 1\n")
        self.commit("no plan anywhere")
        rc, out, _ = self.run_cli("--base", "main", "--json", "--strict")
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(out)["status"], "no_plan")

    def test_ambiguous_plans_listed_and_strict_exits_one(self):
        self.write("plan.md", PLAN_AB)
        self.write("docs/plans/other.md", PLAN_AB)
        self.commit("two candidates")
        rc, out, _ = self.run_cli("--base", "main", "--json")
        self.assertEqual(rc, 0)
        r = json.loads(out)
        self.assertEqual(r["status"], "ambiguous_plan")
        self.assertEqual(r["candidates"], ["docs/plans/other.md", "plan.md"])
        rc, _, _ = self.run_cli("--base", "main", "--strict")
        self.assertEqual(rc, 1)

    def test_pr_body_resolves_an_ambiguity(self):
        self.write("plan.md", PLAN_AB)
        self.write("docs/plans/other.md", "## Files that change\n- `nothing.py`\n")
        self.write("src/a.py", "a = 1\n")
        self.write("src/b.py", "b = 1\n")
        self.commit("two candidates")
        body = self.td / "body.md"
        body.write_text("**Plan:** plan.md\n")
        r = self.report("--pr-body", str(body))
        self.assertEqual(r["plan"], "plan.md")
        self.assertEqual(r["plan_source"], "pr-body")

    def test_custom_plans_glob(self):
        self.write("specs/feature.plan.md", PLAN_AB)
        self.write("src/a.py", "a = 1\n")
        self.write("src/b.py", "b = 1\n")
        self.commit("plan under a custom name")
        self.assertEqual(self.report()["status"], "no_plan")
        r = self.report("--plans-glob", "specs/*.plan.md")
        self.assertEqual(r["plan"], "specs/feature.plan.md")
        self.assertEqual(r["status"], "match", r)


class TestExitCodesAndIgnore(RepoCase):
    def _drift(self):
        self.write("plan.md", PLAN_AB)
        self.write("src/a.py", "a = 1\n")
        self.write("src/b.py", "b = 1\n")
        self.write("src/c.py", "c = 1\n")
        self.commit("drift")

    def test_strict_exit_codes(self):
        self._drift()
        self.assertEqual(self.run_cli("--base", "main")[0], 0, "advisory by default")
        self.assertEqual(self.run_cli("--base", "main", "--json")[0], 0)
        self.assertEqual(self.run_cli("--base", "main", "--strict")[0], 1)
        self.assertEqual(self.run_cli("--base", "main", "--strict", "--json")[0], 1)

    def test_strict_match_exits_zero(self):
        self.write("plan.md", PLAN_AB)
        self.write("src/a.py", "a = 1\n")
        self.write("src/b.py", "b = 1\n")
        self.commit("clean")
        self.assertEqual(self.run_cli("--base", "main", "--strict")[0], 0)

    def test_changelog_ignored_by_default_and_plan_file_never_counted(self):
        self.write("plan.md", PLAN_AB)
        self.write("src/a.py", "a = 1\n")
        self.write("src/b.py", "b = 1\n")
        self.write("CHANGELOG.md", "- entry\n")
        self.commit("with changelog")
        r = self.report()
        self.assertEqual(r["status"], "match", r)
        self.assertEqual(r["ignored"], ["CHANGELOG.md"])
        self.assertNotIn("plan.md", r["unplanned"] + r["matched"])

    def test_ignore_flag_replaces_the_default(self):
        self.write("plan.md", PLAN_AB)
        self.write("src/a.py", "a = 1\n")
        self.write("src/b.py", "b = 1\n")
        self.write("CHANGELOG.md", "- entry\n")
        self.write("deps/x.lock", "lock\n")
        self.commit("lockfile + changelog")
        r = self.report("--ignore", "**/*.lock")
        self.assertEqual(r["ignored"], ["deps/x.lock"])
        self.assertEqual(r["unplanned"], ["CHANGELOG.md"])


class TestBaseAndErrors(RepoCase):
    def test_base_falls_back_to_origin_main(self):
        self.git("update-ref", "refs/remotes/origin/main", "main")
        self.write("plan.md", PLAN_AB)
        self.write("src/a.py", "a = 1\n")
        self.write("src/b.py", "b = 1\n")
        self.commit("implement")
        rc, out, err = self.run_cli("--json")
        self.assertEqual(rc, 0, err)
        r = json.loads(out)
        self.assertEqual(r["base"]["ref"], "origin/main")
        self.assertEqual(r["status"], "match")

    def test_unresolvable_base_exits_two(self):
        rc, _, err = self.run_cli("--json")
        self.assertEqual(rc, 2)
        self.assertIn("pass --base", err)
        rc, _, err = self.run_cli("--base", "no-such-ref")
        self.assertEqual(rc, 2)
        self.assertIn("no-such-ref", err)

    def test_missing_plan_exits_two(self):
        self.write("src/a.py", "a = 1\n")
        self.commit("x")
        rc, _, err = self.run_cli("--base", "main", "--plan", "nope/plan.md")
        self.assertEqual(rc, 2)
        self.assertIn("nope/plan.md", err)

    def test_json_path_writes_file_and_keeps_human_stdout(self):
        # The exact shape the sdlc-gates workflow template uses: JSON artifact to a
        # file, human report on stdout for the step summary.
        self.write("plan.md", PLAN_AB)
        self.write("src/a.py", "a = 1\n")
        self.write("src/b.py", "b = 1\n")
        self.commit("implement")
        body = self.td / "body.md"
        body.write_text("Plan: plan.md\n")
        dest = self.td / "out" / "plan-drift.json"
        dest.parent.mkdir()
        rc, out, err = self.run_cli("--base", "main", "--pr-body", str(body), "--json", str(dest))
        self.assertEqual(rc, 0, err)
        self.assertIn("plan-drift: match", out)
        self.assertEqual(json.loads(dest.read_text())["status"], "match")
        rc, _, err = self.run_cli("--base", "main", "--json", str(self.td / "missing-dir" / "x.json"))
        self.assertEqual(rc, 2)
        self.assertIn("--json", err)

    def test_git_env_is_filtered(self):
        with mock.patch.dict(os.environ, {"SECRET_TOKEN": "x", "GIT_TRACE": "0"}):
            env = plan_drift.git_env()
        self.assertNotIn("SECRET_TOKEN", env)
        self.assertEqual(env.get("GIT_TRACE"), "0")
        self.assertTrue(set(env) <= {"PATH", "HOME", "LANG", "LC_ALL", "TMPDIR"}
                        | {k for k in env if k.startswith("GIT_")})

    def test_git_env_denies_relocation_and_config_injection(self):
        planted = {"GIT_DIR": "/elsewhere/.git", "GIT_WORK_TREE": "/elsewhere",
                   "GIT_INDEX_FILE": "/elsewhere/index", "GIT_CONFIG_PARAMETERS": "'a.b=c'",
                   "GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "core.pager",
                   "GIT_CONFIG_VALUE_0": "evil", "GIT_EXTERNAL_DIFF": "evil",
                   "GIT_EXEC_PATH": "/elsewhere/bin", "GIT_CEILING_DIRECTORIES": "/"}
        with mock.patch.dict(os.environ, {**planted, "GIT_TRACE": "0",
                                          "GIT_CONFIG_GLOBAL": "/dev/null"}):
            env = plan_drift.git_env()
        for k in planted:
            self.assertNotIn(k, env)
        self.assertEqual((env.get("GIT_TRACE"), env.get("GIT_CONFIG_GLOBAL")), ("0", "/dev/null"))

    def test_an_exported_git_dir_does_not_redirect_the_analysis(self):
        other = self.td / "other"
        other.mkdir()
        subprocess.run(["git", "init", "-q", "-b", "main", str(other)], check=True,
                       capture_output=True)
        self.write("plan.md", PLAN_AB)
        self.write("src/a.py", "a = 1\n")
        self.write("src/b.py", "b = 1\n")
        self.commit("implement")
        with mock.patch.dict(os.environ, {"GIT_DIR": str(other / ".git")}):
            r = self.report()
        self.assertEqual((r["status"], r["plan"]), ("match", "plan.md"), r)

    def test_log_never_runs_the_configured_gpg_program(self):
        marker = self.td / "gpg-ran"
        fake = self.td / "fake-gpg"
        fake.write_text(f"#!/bin/sh\ntouch '{marker}'\nexit 1\n")
        fake.chmod(0o755)
        self.git("config", "log.showSignature", "true")
        self.git("config", "gpg.program", str(fake))
        self.write("plan.md", PLAN_AB)
        self.write("src/a.py", "a = 1\n")
        self.write("src/b.py", "b = 1\n")
        head = self.commit("implement")
        # Give the commit a signature header so that showing signatures runs gpg.program.
        raw = self.git("cat-file", "commit", head)
        head_end = raw.index("\n\n")
        sig = ("gpgsig -----BEGIN PGP SIGNATURE-----\n \n iQEzBAABCAAdFiEE\n"
               " -----END PGP SIGNATURE-----")
        signed = raw[:head_end] + "\n" + sig + raw[head_end:]
        new = subprocess.run(["git", "-C", str(self.repo), "hash-object", "-t", "commit", "-w",
                              "--stdin"], input=signed, capture_output=True, text=True,
                             check=True).stdout.strip()
        self.git("update-ref", "refs/heads/feature", new)
        # Positive control: git itself runs gpg.program for this commit.
        self.git("log", "-1", "--format=%H", "feature")
        self.assertTrue(marker.exists(), "the fixture must make `git log` run gpg.program")
        marker.unlink()
        self.report()
        self.assertFalse(marker.exists(), "plan-drift ran the configured gpg.program")

    def test_an_invalid_glob_in_the_plan_is_a_usage_error_not_drift(self):
        self.write("plan.md", "## Files that change\n- `src/[z-a].py`\n")
        self.write("src/a.py", "a = 1\n")
        self.commit("bad plan")
        for extra in ((), ("--strict",)):
            rc, _, err = self.run_cli("--base", "main", *extra)
            self.assertEqual(rc, 2, err)
            self.assertIn("is not a valid glob", err)

    def test_shim_runs_as_a_subprocess(self):
        self.write("plan.md", PLAN_AB)
        self.write("src/a.py", "a = 1\n")
        self.write("src/b.py", "b = 1\n")
        self.commit("implement")
        p = subprocess.run(
            ["bash", str(REPO_ROOT / "bin" / "bstack-plan-drift"), "--repo", str(self.repo),
             "--base", "main", "--json"],
            capture_output=True, text=True, stdin=subprocess.DEVNULL,
        )
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(json.loads(p.stdout)["status"], "match")


if __name__ == "__main__":
    unittest.main()
