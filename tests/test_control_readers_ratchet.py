#!/usr/bin/env python3
"""Exercise the reader ratchet in an isolated Git fixture, including a dead-check control.

Fixture readers are assembled from two string halves so this file never holds the
legacy name next to a suffix and is not itself counted by the check it tests.
"""
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

SOURCE = Path(__file__).resolve().parent.parent / "scripts"
CHECKER = "scripts/check_control_readers.py"
LEGACY = '".' + 'control/'
RENAMED = '".' + 'broomva/'


class RatchetTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        scripts = self.root / "scripts"
        scripts.mkdir()
        shutil.copy(SOURCE / "check_control_readers.py", scripts / "check_control_readers.py")
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        (self.root / "old.py").write_text(f'read({LEGACY}asks")\n')
        # The checker counts itself (its selftest strings), as the workspace's does.
        (scripts / "control_readers_baseline.json").write_text(
            json.dumps({"count": 2, "readers": ["old.py", CHECKER]})
        )
        self.stage()

    def stage(self):
        subprocess.run(["git", "add", "-A"], cwd=self.root, check=True)

    def commit(self, message):
        self.stage()
        subprocess.run(["git", "-c", "core.hooksPath=/dev/null", "-c", "user.name=t", "-c", "user.email=t@example.invalid",
                        "commit", "-q", "-m", message], cwd=self.root, check=True)

    def base(self):
        """Commit the fixture as the base branch's state and return its ref."""
        self.commit("base")
        subprocess.run(["git", "tag", "base"], cwd=self.root, check=True)
        return "base"

    def regenerate_baseline(self):
        printed = self.check("--print-baseline")
        self.assertEqual(printed.returncode, 0, printed.stdout + printed.stderr)
        (self.root / "scripts/control_readers_baseline.json").write_text(printed.stdout)

    def check(self, *args):
        return subprocess.run(["python3", CHECKER, *args],
                              cwd=self.root, capture_output=True, text=True)

    def test_baseline_fixture_passes(self):
        result = self.check()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Unpaired readers: 2; baseline: 2", result.stdout)

    def test_added_reader_fails_and_names_file(self):
        (self.root / "new.py").write_text(f'read({LEGACY}policy.yaml")\n')
        self.stage()
        result = self.check()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("new.py", result.stdout)

    def test_added_paired_reader_passes(self):
        (self.root / "dual.py").write_text(f'read({LEGACY}asks") or read({RENAMED}asks")\n')
        self.stage()
        result = self.check()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("dual.py", result.stdout)
        self.assertIn("Unpaired readers: 2; baseline: 2", result.stdout)

    def test_pairing_is_by_suffix(self):
        # The renamed root with another suffix does not pair the legacy reference.
        (self.root / "half.py").write_text(f'read({LEGACY}asks") or read({RENAMED}audit")\n')
        self.stage()
        result = self.check()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("half.py", result.stdout)

    def test_removed_reader_passes_and_reports_lower_count(self):
        (self.root / "old.py").write_text("pass\n")
        result = self.check()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("fell to 1", result.stdout)

    def test_swapping_one_reader_for_another_fails(self):
        # The count stays at the baseline, but new.py was never in it.
        (self.root / "old.py").write_text("pass\n")
        (self.root / "new.py").write_text(f'read({LEGACY}policy.yaml")\n')
        self.stage()
        result = self.check()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("new.py", result.stdout)
        self.assertIn("Unpaired readers: 2; baseline: 2", result.stdout)

    def test_comparison_is_live(self):
        (self.root / "new.py").write_text(f'read({LEGACY}policy.yaml")\n')
        self.stage()
        checker = self.root / CHECKER
        original = checker.read_text()
        self.assertIn("    if new:\n", original)
        checker.write_text(original.replace("    if new:\n", "    if False:\n", 1))
        result = self.check()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_baseline_with_wrong_count_is_an_error(self):
        (self.root / "scripts/control_readers_baseline.json").write_text(
            json.dumps({"count": 5, "readers": ["old.py", CHECKER]}))
        result = self.check()
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)

    def test_print_baseline_lists_current_readers_and_writes_nothing(self):
        before = (self.root / "scripts/control_readers_baseline.json").read_text()
        result = self.check("--print-baseline")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(json.loads(result.stdout), {"count": 2, "readers": ["old.py", CHECKER]})
        self.assertEqual((self.root / "scripts/control_readers_baseline.json").read_text(), before)

    def test_this_test_file_is_not_a_reader(self):
        shutil.copy(Path(__file__), self.root / "test_copy.py")
        self.stage()
        result = self.check()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    # ── the baseline is compared with the base branch's copy (round 1, finding 1) ──

    def test_reader_added_with_regenerated_baseline_fails_against_base(self):
        # The reviewer's reproduction: add a reader and write it into the baseline.
        ref = self.base()
        (self.root / "actual.py").write_text(f'p = {LEGACY}asks"\n')
        self.stage()
        self.regenerate_baseline()
        self.commit("widen")
        alone = self.check()
        self.assertEqual(alone.returncode, 0, alone.stdout + alone.stderr)  # the hole without a base
        result = self.check("--base-ref", ref)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("Baseline gained readers", result.stdout)
        self.assertIn("  actual.py", result.stdout)

    def test_baseline_removal_passes_against_base(self):
        ref = self.base()
        (self.root / "old.py").write_text("pass\n")
        self.stage()
        self.regenerate_baseline()
        self.commit("shrink")
        result = self.check("--base-ref", ref)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Unpaired readers: 1; baseline: 1", result.stdout)

    def test_renamed_reader_passes_against_base(self):
        ref = self.base()
        subprocess.run(["git", "mv", "old.py", "moved.py"], cwd=self.root, check=True)
        self.regenerate_baseline()
        self.commit("rename")
        result = self.check("--base-ref", ref)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Baseline entry renamed: old.py -> moved.py", result.stdout)

    def test_removal_does_not_pay_for_an_unrelated_addition(self):
        # A dropped entry admits only the file it was renamed to.
        ref = self.base()
        (self.root / "old.py").unlink()
        (self.root / "other.py").write_text(f'read({LEGACY}policy.yaml")\n')
        self.stage()
        self.regenerate_baseline()
        self.commit("swap")
        result = self.check("--base-ref", ref)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("  other.py", result.stdout)

    def test_first_introduction_of_baseline_skips_with_note(self):
        baseline = self.root / "scripts/control_readers_baseline.json"
        saved = baseline.read_text()
        baseline.unlink()
        ref = self.base()
        baseline.write_text(saved)
        self.commit("introduce")
        result = self.check("--base-ref", ref)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("does not exist at base; base comparison skipped", result.stdout)

    def test_unknown_base_ref_is_an_error(self):
        self.base()
        result = self.check("--base-ref", "no-such-ref")
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("not a commit", result.stderr)

    # ── documented limits (round 1, findings 2 and 4; round 3, the pairing unit): pinned so a change is deliberate ──

    def test_constructed_path_is_not_counted(self):
        (self.root / "built.py").write_text('p = "." + "' + 'control/asks"\n')
        self.stage()
        result = self.check()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("built.py", result.stdout)
        self.assertIn("Unpaired readers: 2; baseline: 2", result.stdout)

    def test_comment_mention_is_counted(self):
        (self.root / "noted.py").write_text(f'# migration note: {LEGACY[1:]}asks\n')
        self.stage()
        result = self.check()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("noted.py", result.stdout)
        self.assertIn("Comments count too", result.stdout)

    def test_pairing_unit_is_file_and_first_component(self):
        # Round 3 (maestro review of the same checker): two different paths under one
        # first component pair each other in the same file. Kept as the contract, to
        # match the workspace's sensor; this pins it so a change is deliberate.
        (self.root / "same_component.py").write_text(
            f'legacy = {LEGACY}asks/private"\ncurrent = {RENAMED}asks/unrelated"\n')
        self.stage()
        result = self.check()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("same_component.py", result.stdout)
        self.assertIn("Unpaired readers: 2; baseline: 2", result.stdout)
        # The same two paths in two different files: each file is judged alone.
        (self.root / "same_component.py").write_text(f'legacy = {LEGACY}asks/private"\n')
        (self.root / "other_file.py").write_text(f'current = {RENAMED}asks/unrelated"\n')
        self.stage()
        result = self.check()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("same_component.py", result.stdout)
        self.assertIn("first path component", result.stdout)

    # ── round 4: names are whole path components, never substrings ──

    def write_reader(self, rel):
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f'p = {LEGACY}asks"\n')

    def test_directory_ending_in_a_skipped_name_is_counted(self):
        # The reviewer's reproduction: `mydocs` is not `docs`.
        self.write_reader("scripts/mydocs/probe.py")
        self.stage()
        result = self.check()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("scripts/mydocs/probe.py", result.stdout)
        self.assertIn("Unpaired readers: 3; baseline: 2", result.stdout)

    def test_skipped_directories_match_whole_components_at_any_depth(self):
        for rel in ("docs/x.py", "a/docs/x.py", "a/research/x.py", "node_modules/x.js"):
            self.write_reader(rel)
        self.stage()
        result = self.check()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Unpaired readers: 2; baseline: 2", result.stdout)

    def test_names_containing_a_skipped_name_are_counted(self):
        counted = ("scripts/research_notes/x.py", "archived/x.py", "my." + "control/x.py")
        for rel in counted:
            self.write_reader(rel)
        self.stage()
        result = self.check()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        for rel in counted:
            self.assertIn(rel, result.stdout)
        self.assertIn("Unpaired readers: 5; baseline: 2", result.stdout)

    def test_root_must_start_a_path_component(self):
        # `my.control/asks` names another folder, not the legacy one.
        (self.root / "other.py").write_text('p = "my.' + 'control/asks"\n')
        self.stage()
        result = self.check()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("other.py", result.stdout)

    def test_hint_states_the_first_introduction_skip(self):
        self.write_reader("new.py")
        self.stage()
        result = self.check()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("no\nbaseline file yet", result.stdout)


if __name__ == "__main__":
    unittest.main()
