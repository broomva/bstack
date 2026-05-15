import os
import subprocess
import tempfile
import time
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


class EndToEndTest(unittest.TestCase):
    def test_dispatch_through_pr_merged(self):
        wave_py = Path(__file__).resolve().parents[2] / "scripts" / "wave.py"
        stub = Path(__file__).parent / "fake_claude.sh"
        with tempfile.TemporaryDirectory() as td:
            cache = Path(td) / "cache"
            env = {
                **os.environ,
                "BSTACK_WAVE_CACHE_DIR": str(cache),
                "BSTACK_WAVE_CLAUDE_BIN": str(stub),
                "BSTACK_WAVE_PY": f"python3 {wave_py}",
            }
            repo = _init_repo(Path(td))
            for slug in ("a", "b"):
                p = repo / f"plan-{slug}.md"
                p.write_text(
                    f"---\nwave:\n  worktree: ../wt-{slug}\n  branch: feat/{slug}\n"
                    f"  slug: {slug}\n---\n\n# Plan\n", encoding="utf-8")
                subprocess.run(["git", "-C", str(repo), "add", str(p)], check=True)
                subprocess.run(["git", "-C", str(repo), "commit", "-qm", f"add plan {slug}"],
                               check=True)
            r = subprocess.run(
                ["python3", str(wave_py), "dispatch",
                 str(repo / "plan-a.md"), str(repo / "plan-b.md")],
                check=True, capture_output=True, text=True, env=env,
            )
            # Wait for the stub to finish writing its lifecycle JSONL entries.
            # The stub fires 4 python3 invocations per plan (×2 plans = 8 total),
            # each spawning a fresh interpreter; 2 s is sufficient on CI.
            time.sleep(2)
            wave_dirs = [d for d in cache.iterdir() if d.name.startswith("wave_")]
            self.assertEqual(len(wave_dirs), 1)
            wd = wave_dirs[0]
            for slug in ("a", "b"):
                jl = wd / f"{slug}.status.jsonl"
                self.assertTrue(jl.exists(), f"missing {jl}")
                lines = jl.read_text().strip().splitlines()
                self.assertEqual(len(lines), 4)
                last = lines[-1]
                self.assertIn("pr_merged", last)
            # Status output mentions all-merged suggestion.
            sr = subprocess.run(
                ["python3", str(wave_py), "status", wd.name],
                check=True, capture_output=True, text=True, env=env,
            )
            self.assertIn("janitor", sr.stdout.lower())
