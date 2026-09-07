import unittest
from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures"


class FrontmatterParserTest(unittest.TestCase):
    def test_good_plan_parses(self):
        from scripts.wave import parse_plan_frontmatter
        fm = parse_plan_frontmatter(FIXTURES / "plan-good-a.md")
        self.assertEqual(fm["worktree"], "../life-spec-e-sub-b")
        self.assertEqual(fm["branch"], "feat/spec-e-sub-b")
        self.assertEqual(fm["base"], "main")
        self.assertEqual(fm["slug"], "spec-e-sub-b")
        self.assertEqual(fm["linear"], "BRO-1023")

    def test_missing_frontmatter_raises(self):
        from scripts.wave import parse_plan_frontmatter, WaveError
        with self.assertRaises(WaveError) as ctx:
            parse_plan_frontmatter(FIXTURES / "plan-missing-fm.md")
        self.assertIn("no frontmatter", str(ctx.exception).lower())

    def test_missing_wave_block_raises(self):
        from scripts.wave import parse_plan_frontmatter, WaveError
        with self.assertRaises(WaveError) as ctx:
            parse_plan_frontmatter(FIXTURES / "plan-bad-fm.md")
        self.assertIn("wave", str(ctx.exception).lower())

    def test_nonexistent_file_raises(self):
        from scripts.wave import parse_plan_frontmatter, WaveError
        with self.assertRaises(WaveError):
            parse_plan_frontmatter(FIXTURES / "does-not-exist.md")


class UnknownKeyTest(unittest.TestCase):
    def test_unknown_wave_key_is_rejected_by_name(self):
        import tempfile
        from scripts.wave import parse_plan_frontmatter, WaveError
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "plan.md"
            p.write_text("---\nwave:\n  worktree: ../wt\n  branch: feat/x\n  mpc: inherit\n---\n# P\n")
            with self.assertRaises(WaveError) as ctx:
                parse_plan_frontmatter(p)
            self.assertIn("mpc", str(ctx.exception))
            self.assertIn("mcp", str(ctx.exception))   # the known list is printed

    def test_mcp_is_a_known_key(self):
        import tempfile
        from scripts.wave import parse_plan_frontmatter
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "plan.md"
            p.write_text("---\nwave:\n  worktree: ../wt\n  branch: feat/x\n  mcp: inherit\n---\n# P\n")
            self.assertEqual(parse_plan_frontmatter(p)["mcp"], "inherit")
