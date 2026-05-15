import os
import subprocess
import tempfile
import unittest
from pathlib import Path


def _init_repo(td: Path) -> Path:
    repo = td / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "t"], check=True)
    (repo / "README").write_text("hi\n")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "init"], check=True)
    return repo


def _put_plan(repo: Path, slug: str) -> Path:
    p = repo / f"plan-{slug}.md"
    p.write_text(
        f"---\nwave:\n  worktree: ../wt-{slug}\n  branch: feat/{slug}\n"
        f"  slug: {slug}\n---\n\n# Plan\n",
        encoding="utf-8",
    )
    # Commit the plan so the source repo stays clean for validator.
    subprocess.run(["git", "-C", str(repo), "add", str(p)], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", f"add plan {slug}"], check=True)
    return p


class DispatchTest(unittest.TestCase):
    def test_dry_run_creates_nothing(self):
        from scripts.wave import main
        with tempfile.TemporaryDirectory() as td:
            os.environ["BSTACK_WAVE_CACHE_DIR"] = td + "/cache"
            repo = _init_repo(Path(td))
            pa = _put_plan(repo, "a")
            pb = _put_plan(repo, "b")
            with self.assertRaises(SystemExit) as ctx:
                main(["dispatch", "--dry-run", str(pa), str(pb)])
            self.assertEqual(ctx.exception.code, 0)
            self.assertFalse(Path(td + "/cache").exists())
            self.assertFalse((Path(td) / "wt-a").exists())

    def test_dispatch_with_stub_creates_manifest_and_worktrees(self):
        from scripts.wave import main
        with tempfile.TemporaryDirectory() as td:
            os.environ["BSTACK_WAVE_CACHE_DIR"] = td + "/cache"
            stub = Path(td) / "fake-claude.sh"
            stub.write_text(
                "#!/bin/sh\n"
                "if [ \"$1\" = --bg ]; then flag=1; else flag=0; fi\n"
                "echo \"CALLED bg=$flag\" >> " + td + "/fake-calls.log\n"
                "exit 0\n"
            )
            stub.chmod(0o755)
            os.environ["BSTACK_WAVE_CLAUDE_BIN"] = str(stub)
            repo = _init_repo(Path(td))
            pa = _put_plan(repo, "a")
            pb = _put_plan(repo, "b")
            with self.assertRaises(SystemExit) as ctx:
                main(["dispatch", "--name", "test-wave", str(pa), str(pb)])
            self.assertEqual(ctx.exception.code, 0)
            cache = Path(td) / "cache"
            self.assertTrue(cache.exists())
            wave_dirs = [d for d in cache.iterdir() if d.name.startswith("wave_")]
            self.assertEqual(len(wave_dirs), 1)
            self.assertTrue((wave_dirs[0] / "manifest.json").exists())
            self.assertTrue((Path(td) / "wt-a").exists())
            self.assertTrue((Path(td) / "wt-b").exists())
            # Give the (instantly-exiting) stubs a moment to write the log.
            import time
            time.sleep(0.5)
            calls = Path(td + "/fake-calls.log").read_text().strip().splitlines()
            self.assertEqual(len(calls), 2)
            self.assertIn("bg=1", calls[0])
            self.assertIn("bg=1", calls[1])

    def test_validation_failure_aborts_pre_worktree(self):
        from scripts.wave import main
        with tempfile.TemporaryDirectory() as td:
            os.environ["BSTACK_WAVE_CACHE_DIR"] = td + "/cache"
            os.environ["BSTACK_WAVE_CLAUDE_BIN"] = "/bin/true"
            repo = _init_repo(Path(td))
            pa = _put_plan(repo, "a")
            # Write a second plan file with the same branch as plan-a -> duplicate branch
            pb = repo / "plan-a-copy.md"
            pb.write_text(
                "---\nwave:\n  worktree: ../wt-a-copy\n  branch: feat/a\n"
                "  slug: a-copy\n---\n\n# Plan copy\n",
                encoding="utf-8",
            )
            subprocess.run(["git", "-C", str(repo), "add", str(pb)], check=True)
            subprocess.run(["git", "-C", str(repo), "commit", "-qm", "add plan-a-copy"], check=True)
            with self.assertRaises(SystemExit) as ctx:
                main(["dispatch", str(pa), str(pb)])
            self.assertEqual(ctx.exception.code, 1)
            self.assertFalse((Path(td) / "wt-a").exists())
            self.assertFalse(Path(td + "/cache").exists())
