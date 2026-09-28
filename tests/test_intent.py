"""Tests for scripts/intent.py (BRO-2542). stdlib unittest only.

Each rule the docstring states is tested in both directions: the lint fires on the
defect, and a filled-in intent with the same shape passes. A checker that rejects
everything would pass half of these and fail the other half.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))
import intent  # noqa: E402

TEMPLATE = REPO / "references" / "templates" / "intent.md"

FILLED = """# Intent: Faster release notes

Author: Ada Lovelace. Status: draft.

## Problem

Release notes take a day to assemble by hand; three of the last five were late.

## Proposed outcome

Notes are drafted from merged PR titles within an hour of the tag.

## Affected users and systems

Maintainers who cut releases; the release workflow and the changelog.

## Constraints

No new external service. The changelog format must not change.

## Open questions

None.
"""


def run(*argv: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = intent.main(list(argv))
    return rc, out.getvalue(), err.getvalue()


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="intent-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def write(self, text: str, name: str = "i.md") -> Path:
        p = self.tmp / name
        p.write_bytes(text.encode("utf-8"))
        return p

    def lint(self, text: str) -> list[str]:
        return intent.lint_text(text)


class TestNew(Base):
    def test_new_creates_dated_file_with_title_author_and_draft(self):
        rc, out, _ = run("new", "faster-release-notes", "--title", "Faster release notes",
                         "--author", "Ada Lovelace", "--dir", str(self.tmp / "intent"),
                         "--date", "2026-09-23")
        self.assertEqual(rc, 0)
        path = self.tmp / "intent" / "2026-09-23-faster-release-notes.md"
        self.assertEqual(out.strip(), str(path))
        text = path.read_text()
        self.assertTrue(text.startswith("# Intent: Faster release notes\n"))
        self.assertIn("Author: Ada Lovelace. Status: draft.", text)
        for s in intent.SECTIONS:
            self.assertIn(f"## {s}", text)

    def test_new_title_defaults_from_slug(self):
        rc, _, _ = run("new", "cache-warmup", "--author", "A", "--dir", str(self.tmp),
                       "--date", "2026-01-02")
        self.assertEqual(rc, 0)
        self.assertIn("# Intent: Cache warmup",
                      (self.tmp / "2026-01-02-cache-warmup.md").read_text())

    def test_new_refuses_to_overwrite(self):
        args = ("new", "x", "--author", "A", "--dir", str(self.tmp), "--date", "2026-01-02")
        self.assertEqual(run(*args)[0], 0)
        path = self.tmp / "2026-01-02-x.md"
        path.write_text("hand edits")
        rc, _, err = run(*args)
        self.assertEqual(rc, 1)
        self.assertIn("refusing to overwrite", err)
        self.assertEqual(path.read_text(), "hand edits")

    def test_new_rejects_bad_slugs_and_writes_nothing(self):
        for slug in ("Upper", "has space", "a/b", "a_b", "..", ""):
            with self.subTest(slug=slug):
                rc, _, err = run("new", slug, "--author", "A", "--dir", str(self.tmp / "d"),
                                 "--date", "2026-01-02")
                self.assertEqual(rc, 2)
                self.assertIn("slug", err)
        self.assertFalse((self.tmp / "d").exists())

    def test_new_rejects_bad_dates(self):
        for date in ("2026-13-01", "26-01-01", "2026-02-30", "today"):
            with self.subTest(date=date):
                rc, _, _ = run("new", "x", "--author", "A", "--dir", str(self.tmp),
                               "--date", date)
                self.assertEqual(rc, 2)

    def test_new_with_custom_template(self):
        tpl = self.write("# Intent: <title>\n\nAuthor: <name>. Status: draft.\n\nCUSTOM\n",
                         "tpl.md")
        rc, _, _ = run("new", "x", "--author", "B", "--dir", str(self.tmp / "o"),
                       "--template", str(tpl), "--date", "2026-01-02")
        self.assertEqual(rc, 0)
        self.assertIn("CUSTOM", (self.tmp / "o" / "2026-01-02-x.md").read_text())
        self.assertEqual(run("new", "y", "--template", str(self.tmp / "nope.md"),
                             "--dir", str(self.tmp))[0], 2)

    def test_new_without_known_author_leaves_placeholder_for_lint(self):
        with mock.patch.object(intent, "git_user_name", return_value=None):
            rc, out, _ = run("new", "x", "--dir", str(self.tmp), "--date", "2026-01-02")
        self.assertEqual(rc, 0)
        problems = intent.lint_text(Path(out.strip()).read_text())
        self.assertTrue(any("<name>" in p for p in problems), problems)

    def test_fresh_intent_fails_lint_on_every_section_placeholder(self):
        run("new", "x", "--author", "A", "--title", "T", "--dir", str(self.tmp),
            "--date", "2026-01-02")
        rc, out, _ = run("lint", str(self.tmp / "2026-01-02-x.md"))
        self.assertEqual(rc, 1)
        self.assertEqual(out.count("unfilled placeholder"), len(intent.SECTIONS))


class TestLint(Base):
    def test_filled_intent_passes(self):
        self.assertEqual(self.lint(FILLED), [])
        rc, out, _ = run("lint", str(self.write(FILLED)))
        self.assertEqual(rc, 0, out)

    def test_missing_section_is_named(self):
        text = FILLED.replace("## Constraints\n\nNo new external service. The changelog "
                              "format must not change.\n\n", "")
        probs = self.lint(text)
        self.assertEqual(probs, ["0: missing section '## Constraints'"])

    def test_empty_section_is_flagged(self):
        text = FILLED.replace("None.\n", "\n")
        probs = self.lint(text)
        self.assertEqual(len(probs), 1)
        self.assertIn("'## Open questions' is empty", probs[0])

    def test_section_holding_only_a_comment_is_empty(self):
        text = FILLED.replace("None.\n", "<!-- fill me -->\n")
        self.assertTrue(any("is empty" in p for p in self.lint(text)))

    def test_leftover_placeholder_reported_with_line(self):
        ph = '<What is still unknown, and who can answer each question. Write "None." when there are none.>'
        text = FILLED.replace("None.", ph)
        probs = self.lint(text)
        self.assertEqual(len(probs), 1)
        line = FILLED.split("\n").index("None.") + 1
        self.assertEqual(probs[0], f"{line}: unfilled placeholder {ph}")

    def test_angle_bracket_prose_is_not_a_placeholder_wrapped_or_not(self):
        for body in ("We measured throughput a < b before the change,\nand tail latency "
                     "y > x once it shipped to real users.",
                     "Keep p99 < 200ms and error rate > 0 alerts; see <Who decides?>."):
            with self.subTest(body=body):
                self.assertEqual(self.lint(FILLED.replace("None.", body)), [])

    def test_lint_uses_the_placeholders_of_the_template_in_use(self):
        tpl = self.write("# Intent: <title>\n\nAuthor: <name>. Status: draft.\n\n"
                         "## Problem\n\n<Custom prompt>\n", "custom-tpl.md")
        doc = self.write(FILLED.replace("None.", "<Custom prompt>"), "doc.md")
        self.assertEqual(run("lint", str(doc))[0], 0)          # not a shipped placeholder
        rc, out, _ = run("lint", str(doc), "--template", str(tpl))
        self.assertEqual(rc, 1, out)
        self.assertIn("unfilled placeholder <Custom prompt>", out)
        self.assertEqual(run("lint", str(doc), "--template", str(self.tmp / "no.md"))[0], 2)

    def test_angle_brackets_in_code_and_autolinks_are_not_placeholders(self):
        text = FILLED.replace(
            "None.",
            "Does `Vec<String>` survive? See <https://example.com/x>.\n\n"
            "```\nlet v: Vec<u8> = vec![];\n```\n")
        self.assertEqual(self.lint(text), [])

    def test_invalid_status_flagged_and_each_valid_one_accepted(self):
        probs = self.lint(FILLED.replace("Status: draft.", "Status: shipped."))
        self.assertEqual(len(probs), 1)
        self.assertIn("'shipped' is not one of", probs[0])
        for st in intent.STATUSES:
            with self.subTest(status=st):
                self.assertEqual(self.lint(FILLED.replace("draft", st)), [])

    def test_missing_header_and_title(self):
        text = FILLED.replace("Author: Ada Lovelace. Status: draft.\n", "")
        self.assertIn("0: missing header line 'Author: <name>. Status: draft.'",
                      self.lint(text))
        text = FILLED.replace("# Intent: Faster release notes", "# Faster release notes")
        self.assertTrue(any("missing title line" in p for p in self.lint(text)))

    def test_author_with_a_period_parses(self):
        text = FILLED.replace("Ada Lovelace", "A. D. Lovelace")
        self.assertEqual(self.lint(text), [])
        self.assertEqual(intent.parse(text)["author"], "A. D. Lovelace")

    def test_duplicate_section_flagged(self):
        text = FILLED + "\n## Problem\n\nAgain.\n"
        probs = self.lint(text)
        self.assertEqual(len(probs), 1)
        self.assertIn("appears 2 times", probs[0])

    def test_heading_inside_code_fence_is_not_a_section(self):
        text = FILLED.replace("## Constraints\n", "```\n## Constraints\n```\n")
        self.assertIn("0: missing section '## Constraints'", self.lint(text))

    def test_lint_many_files_exit_1_if_any_bad_and_json_shape(self):
        good = self.write(FILLED, "good.md")
        bad = self.write(FILLED.replace("None.", ""), "bad.md")
        rc, out, _ = run("lint", str(good), str(bad), "--json")
        self.assertEqual(rc, 1)
        data = json.loads(out)
        self.assertEqual(data["failed"], 1)
        self.assertEqual([f["problems"] == [] for f in data["files"]], [True, False])

    def test_lint_missing_file_is_usage_error(self):
        self.assertEqual(run("lint", str(self.tmp / "nope.md"))[0], 2)


class TestWrapped(Base):
    def wrap(self, text: str, width: int = 72) -> str:
        """Reflow every prose paragraph the way prettier --prose-wrap / `gq` would."""
        out = []
        for para in text.split("\n\n"):
            if para.startswith("#") or para.startswith("Author:"):
                out.append(para)
            else:
                out.append(textwrap.fill(" ".join(para.split()), width=width))
        return "\n\n".join(out)

    def test_template_reflowed_at_72_columns_still_fails_on_every_placeholder(self):
        run("new", "wrapped", "--author", "A", "--title", "T", "--dir", str(self.tmp),
            "--date", "2026-01-02")
        f = self.tmp / "2026-01-02-wrapped.md"
        wrapped = self.wrap(f.read_text())
        self.assertTrue(any(len(ln) <= 72 and ln.startswith("<") and not ln.endswith(">")
                            for ln in wrapped.split("\n")),
                        "fixture must actually split a placeholder across lines")
        f.write_text(wrapped)
        rc, out, _ = run("lint", str(f))
        self.assertEqual(rc, 1, out)
        self.assertEqual(out.count("unfilled placeholder"), len(intent.SECTIONS), out)
        # reported on the line where the placeholder starts, rejoined onto one line
        self.assertIn("unfilled placeholder <What is wrong or missing today, who feels it, "
                      "and the evidence that it is real.>", out)

    def test_brackets_in_different_paragraphs_are_not_one_placeholder(self):
        text = FILLED.replace("None.", "If a < b holds, stop.\n\nOtherwise c > d.")
        self.assertEqual(self.lint(text), [])


class TestStatus(Base):
    def test_status_prints_the_header_status(self):
        rc, out, _ = run("status", str(self.write(FILLED)))
        self.assertEqual((rc, out.strip()), (0, "draft"))
        rc, out, _ = run("status", str(self.write(FILLED)), "--json")
        self.assertEqual(json.loads(out)["author"], "Ada Lovelace")

    def test_status_without_header_or_with_unknown_status_exits_1(self):
        self.assertEqual(run("status", str(self.write("# Intent: x\n", "n.md")))[0], 1)
        p = self.write(FILLED.replace("draft", "wip"), "w.md")
        self.assertEqual(run("status", str(p))[0], 1)

    def test_set_status_rewrites_only_the_status_token(self):
        crlf = FILLED.replace("\n", "\r\n")
        p = self.write(crlf)
        rc, out, _ = run("set-status", str(p), "accepted")
        self.assertEqual(rc, 0, out)
        after = p.read_bytes()
        expected = crlf.replace("Status: draft.", "Status: accepted.").encode()
        self.assertEqual(after, expected)
        self.assertEqual(run("status", str(p))[1].strip(), "accepted")

    def test_set_status_with_a_comment_on_the_header_line(self):
        head = "Author: Ada Lovelace. Status: draft. <!-- draft | accepted | rejected -->"
        p = self.write(FILLED.replace("Author: Ada Lovelace. Status: draft.", head))
        rc, out, err = run("set-status", str(p), "accepted")
        self.assertEqual(rc, 0, err)
        self.assertIn("Author: Ada Lovelace. Status: accepted. <!-- draft | accepted | "
                      "rejected -->", p.read_text())
        self.assertEqual(run("status", str(p))[1].strip(), "accepted")

    def test_set_status_when_a_comment_ends_on_the_header_line(self):
        p = self.write(FILLED.replace("Author: Ada", "<!-- note\n-->Author: Ada"))
        rc, out, err = run("set-status", str(p), "rejected")
        self.assertEqual(rc, 0, err)
        self.assertIn("-->Author: Ada Lovelace. Status: rejected.", p.read_text())

    def test_set_status_rejects_unknown_status_and_leaves_file(self):
        p = self.write(FILLED)
        rc, _, err = run("set-status", str(p), "done")
        self.assertEqual(rc, 2)
        self.assertIn("not one of", err)
        self.assertEqual(p.read_text(), FILLED)

    def test_set_status_without_header_exits_1(self):
        p = self.write("# Intent: x\n\nno header\n")
        self.assertEqual(run("set-status", str(p), "accepted")[0], 1)
        self.assertEqual(p.read_text(), "# Intent: x\n\nno header\n")


class TestAuthorFromGit(Base):
    def test_a_planted_git_config_override_does_not_become_the_author(self):
        home = self.tmp / "home"
        home.mkdir()
        (home / ".gitconfig").write_text("[user]\n\tname = Real Author\n")
        planted = {"HOME": str(home), "GIT_CONFIG_NOSYSTEM": "1",
                   "GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "user.name",
                   "GIT_CONFIG_VALUE_0": "Planted Author",
                   "GIT_CONFIG_PARAMETERS": "'user.name=Planted Author'"}
        # Outside any repository: a repo's local config would outrank HOME's.
        cwd = os.getcwd()
        os.chdir(self.tmp)
        self.addCleanup(os.chdir, cwd)
        with mock.patch.dict(os.environ, planted):
            # Positive control: plain git honours the override.
            got = subprocess.run(["git", "config", "user.name"], capture_output=True,
                                 text=True).stdout.strip()
            self.assertEqual(got, "Planted Author")
            rc, out, _ = run("new", "author-probe", "--dir", str(self.tmp / "intent"),
                             "--date", "2026-09-27")
        self.assertEqual(rc, 0)
        text = Path(out.strip()).read_text()
        self.assertIn("Author: Real Author.", text)
        self.assertNotIn("Planted", text)


class TestPending(Base):
    """`pending` builds the intent-to-spec work list from the files, never from a push."""

    def intent(self, name: str, status: str) -> Path:
        d = self.tmp / "intent"
        d.mkdir(exist_ok=True)
        p = d / name
        p.write_text(FILLED.replace("Status: draft.", f"Status: {status}."))
        return p

    def pending(self, *extra: str) -> tuple[int, str, str]:
        return run("pending", "--dir", str(self.tmp / "intent"),
                   "--specs", str(self.tmp / "docs" / "specs"), *extra)

    def test_lists_accepted_intents_without_a_spec_and_nothing_else(self):
        a = self.intent("2026-09-01-alpha.md", "accepted")
        self.intent("2026-09-02-beta.md", "accepted")
        self.intent("2026-09-03-draft.md", "draft")
        self.intent("2026-09-04-rejected.md", "rejected")
        (self.tmp / "intent" / "README.md").write_text("# Intents\n\nOne file per intent.\n")
        specs = self.tmp / "docs" / "specs"
        specs.mkdir(parents=True)
        (specs / "2026-09-02-beta.md").write_text("# spec\n")
        rc, out, _ = self.pending()
        self.assertEqual((rc, out.splitlines()), (0, [f"{a} 2026-09-01-alpha"]))

    def test_an_intent_becomes_pending_again_when_its_spec_is_gone(self):
        a = self.intent("2026-09-01-alpha.md", "accepted")
        specs = self.tmp / "docs" / "specs"
        specs.mkdir(parents=True)
        spec = specs / "2026-09-01-alpha.md"
        spec.write_text("# spec\n")
        self.assertEqual(self.pending()[1], "")
        spec.unlink()
        self.assertEqual(self.pending()[1].splitlines(), [f"{a} 2026-09-01-alpha"])

    def test_a_status_line_inside_a_code_fence_does_not_count(self):
        d = self.tmp / "intent"
        d.mkdir()
        (d / "README.md").write_text(
            "# Intents\n\n```\nAuthor: Example. Status: accepted.\n```\n")
        self.assertEqual(self.pending()[1], "")

    def test_a_name_unsafe_for_a_branch_or_read_is_skipped_with_a_warning(self):
        # git check-ref-format rejects spec/.draft, spec/v1.lock, spec/x. and spec/a..b;
        # `read` would split "a b"; ";" is shell.
        bad = ["a b.md", "x;y.md", ".draft.md", "v1.lock.md", "x..md", "a..b.md"]
        for name in bad:
            self.intent(name, "accepted")
        ok = self.intent("2026-09-01-ok.md", "accepted")
        dotted = self.intent("v1.2.md", "accepted")
        rc, out, err = self.pending()
        self.assertEqual((rc, out.splitlines()), (0, [f"{ok} 2026-09-01-ok", f"{dotted} v1.2"]))
        for name in bad:
            self.assertIn(name, err)

    def test_branch_safe_never_accepts_a_name_git_rejects(self):
        names = [".draft", "v1.lock", "x.", "a..b", "a b", "x;y", "-lead", "x@y", "x~y",
                 "x^y", "x:y", "x?y", "x*y", "x[y", "2026-09-01-alpha", "v1.2", "a.b.c"]
        for n in names:
            git_ok = subprocess.run(["git", "check-ref-format", "--branch", f"spec/{n}"],
                                    capture_output=True).returncode == 0
            if intent.branch_safe(n):
                self.assertTrue(git_ok, n)
        self.assertTrue(intent.branch_safe("2026-09-01-alpha"))

    def test_an_unreadable_intent_is_skipped_with_a_warning(self):
        (self.tmp / "intent").mkdir()
        (self.tmp / "intent" / "2026-09-01-bad.md").write_bytes(b"# Intent: x\n\xff\xfe\n")
        rc, out, err = self.pending()
        self.assertEqual((rc, out), (0, ""))
        self.assertIn("2026-09-01-bad.md: unreadable", err)

    def test_a_spec_path_that_is_a_symlink_counts_as_present(self):
        self.intent("2026-09-01-alpha.md", "accepted")
        specs = self.tmp / "docs" / "specs"
        specs.mkdir(parents=True)
        (specs / "2026-09-01-alpha.md").symlink_to(self.tmp / "nowhere.md")   # dangling
        rc, out, err = self.pending()
        self.assertEqual((rc, out), (0, ""))
        self.assertIn("is a symlink; not drafting through it", err)

    def test_a_symlinked_intent_is_skipped(self):
        target = self.tmp / "elsewhere.md"
        target.write_text(FILLED.replace("Status: draft.", "Status: accepted."))
        (self.tmp / "intent").mkdir()
        (self.tmp / "intent" / "2026-09-01-link.md").symlink_to(target)
        rc, out, err = self.pending()
        self.assertEqual((rc, out), (0, ""))
        self.assertIn("2026-09-01-link.md: a symlink", err)

    def test_missing_intent_dir_is_an_empty_list_not_an_error(self):
        self.assertEqual(self.pending()[:2], (0, ""))

    def test_json_shape(self):
        a = self.intent("2026-09-01-alpha.md", "accepted")
        rc, out, _ = self.pending("--json")
        self.assertEqual((rc, json.loads(out)), (0, [{"file": str(a), "name": "2026-09-01-alpha"}]))


class TestShippedTemplate(Base):
    def test_template_has_the_course_shape(self):
        text = TEMPLATE.read_text()
        self.assertTrue(text.startswith("# Intent: <title>\n"))
        self.assertIn("\nAuthor: <name>. Status: draft.\n", text)
        doc = intent.parse(text)
        self.assertEqual([s["name"] for s in doc["sections"]], list(intent.SECTIONS))

    def test_template_fails_lint_only_on_placeholders(self):
        probs = intent.lint_text(TEMPLATE.read_text())
        self.assertTrue(probs)
        self.assertTrue(all("unfilled placeholder" in p for p in probs), probs)
        # title + author + one per section
        self.assertEqual(len(probs), 2 + len(intent.SECTIONS))

    def test_cli_shim_runs_as_a_subprocess(self):
        p = subprocess.run(["bash", str(REPO / "bin" / "bstack-intent"), "status",
                            str(self.write(FILLED))], capture_output=True, text=True,
                           stdin=subprocess.DEVNULL, timeout=60)
        self.assertEqual((p.returncode, p.stdout.strip()), (0, "draft"), p.stderr)


if __name__ == "__main__":
    unittest.main()
