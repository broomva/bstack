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


if __name__ == "__main__":
    unittest.main()
