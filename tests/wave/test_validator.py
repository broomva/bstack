import subprocess
import tempfile
import unittest
from pathlib import Path


def _init_repo(td: Path, base="main") -> Path:
    repo = td / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", base, str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "t"], check=True)
    (repo / "README").write_text("hi\n")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "init"], check=True)
    return repo


def _put_plan(repo: Path, slug: str, worktree_rel: str, branch: str) -> Path:
    plans_dir = repo / "plans"
    plans_dir.mkdir(exist_ok=True)
    p = plans_dir / f"plan-{slug}.md"
    p.write_text(
        f"---\nwave:\n  worktree: {worktree_rel}\n  branch: {branch}\n"
        f"  slug: {slug}\n---\n\n# Plan\n",
        encoding="utf-8",
    )
    return p


class ValidatorTest(unittest.TestCase):
    def test_clean_repo_two_plans_passes(self):
        from scripts.wave import validate_plans
        with tempfile.TemporaryDirectory() as td:
            repo = _init_repo(Path(td))
            pa = _put_plan(repo, "a", "../wt-a", "feat/a")
            pb = _put_plan(repo, "b", "../wt-b", "feat/b")
            entries = validate_plans([pa, pb])
            self.assertEqual(len(entries), 2)
            self.assertEqual(entries[0]["repo_root"], str(repo.resolve()))

    def test_duplicate_branch_rejected(self):
        from scripts.wave import validate_plans, WaveError
        with tempfile.TemporaryDirectory() as td:
            repo = _init_repo(Path(td))
            pa = _put_plan(repo, "a", "../wt-a", "feat/dup")
            pb = _put_plan(repo, "b", "../wt-b", "feat/dup")
            with self.assertRaises(WaveError) as ctx:
                validate_plans([pa, pb])
            self.assertIn("branch", str(ctx.exception).lower())

    def test_duplicate_worktree_rejected(self):
        from scripts.wave import validate_plans, WaveError
        with tempfile.TemporaryDirectory() as td:
            repo = _init_repo(Path(td))
            pa = _put_plan(repo, "a", "../wt-same", "feat/a")
            pb = _put_plan(repo, "b", "../wt-same", "feat/b")
            with self.assertRaises(WaveError):
                validate_plans([pa, pb])

    def test_dirty_repo_rejected(self):
        from scripts.wave import validate_plans, WaveError
        with tempfile.TemporaryDirectory() as td:
            repo = _init_repo(Path(td))
            (repo / "dirty.txt").write_text("uncommitted\n")
            pa = _put_plan(repo, "a", "../wt-a", "feat/a")
            with self.assertRaises(WaveError) as ctx:
                validate_plans([pa])
            self.assertIn("dirty", str(ctx.exception).lower())
