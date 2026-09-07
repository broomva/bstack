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
    _ENV = ("BSTACK_WAVE_CACHE_DIR", "BSTACK_WAVE_CLAUDE_BIN", "BSTACK_PEER_MCP")

    def setUp(self):
        self._saved = {k: os.environ.get(k) for k in self._ENV}
        for k in self._ENV:
            os.environ.pop(k, None)

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

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
        """The stub records the exact argv it was called with and answers like
        the real binary (ANSI-coloured `backgrounded · <id>`). The assertions
        are the P5 spawn contract: named, unattended-safe, prompt last, id
        recorded in the manifest."""
        import json
        from scripts.wave import main
        with tempfile.TemporaryDirectory() as td:
            os.environ["BSTACK_WAVE_CACHE_DIR"] = td + "/cache"
            stub = Path(td) / "fake-claude.sh"
            stub.write_text(
                "#!/bin/sh\n"
                "if [ \"$1\" = agents ]; then echo '[]'; exit 0; fi\n"
                "n=$(ls " + td + "/argv-*.log 2>/dev/null | wc -l | tr -d ' ')\n"
                "printf '%s\\0' \"$@\" > " + td + "/argv-$n.log\n"
                "printf '\\033[1mbackgrounded\\033[0m · \\033[36mabc12%s\\033[0m\\n' \"$n\"\n"
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
            wave_dirs = [d for d in cache.iterdir() if d.name.startswith("wave_")]
            self.assertEqual(len(wave_dirs), 1)
            self.assertTrue((Path(td) / "wt-a").exists())
            self.assertTrue((Path(td) / "wt-b").exists())

            logs = sorted(Path(td).glob("argv-*.log"))
            self.assertEqual(len(logs), 2)
            for log, slug in zip(logs, ("a", "b")):
                argv = log.read_text().split("\0")[:-1]   # NUL-separated: the prompt is multi-line
                self.assertEqual(argv[0], "--bg")
                self.assertEqual(argv[1], "--name")
                self.assertEqual(argv[2], f"wt-{slug}-{slug}")           # <worktree>-<slug>, no ticket
                self.assertIn("--strict-mcp-config", argv)
                self.assertIn("--settings", argv)
                self.assertIn('{"crossSessionInbound":"accept"}', argv)
                self.assertTrue(argv[-1].startswith("Session: wt-"), argv[-1])   # prompt is last
                self.assertIn(f"Plan-slug: {slug}", argv[-1])

            manifest = json.loads((wave_dirs[0] / "manifest.json").read_text())
            by_slug = {p["slug"]: p for p in manifest["plans"]}
            self.assertEqual(by_slug["a"]["session_name"], "wt-a-a")
            self.assertEqual(by_slug["a"]["session_id"], "abc120")
            self.assertEqual(by_slug["b"]["session_id"], "abc121")
            self.assertIsNone(by_slug["a"]["agent_pid"])

    def test_dispatch_reports_a_spawn_that_returned_no_id(self):
        from scripts.wave import main, read_manifest, wave_dir
        with tempfile.TemporaryDirectory() as td:
            os.environ["BSTACK_WAVE_CACHE_DIR"] = td + "/cache"
            stub = Path(td) / "fake-claude.sh"
            stub.write_text("#!/bin/sh\nif [ \"$1\" = agents ]; then echo '[]'; exit 0; fi\n"
                            "echo 'Login expired · Please run /login' >&2\nexit 1\n")
            stub.chmod(0o755)
            os.environ["BSTACK_WAVE_CLAUDE_BIN"] = str(stub)
            repo = _init_repo(Path(td))
            pa = _put_plan(repo, "a")
            with self.assertRaises(SystemExit) as ctx:
                main(["dispatch", str(pa)])
            self.assertEqual(ctx.exception.code, 1)          # a failed spawn is not a launched wave
            cache = Path(td) / "cache"
            wd = [d for d in cache.iterdir() if d.name.startswith("wave_")][0]
            m = read_manifest(wd)
            self.assertIsNone(m.plans[0].session_id)         # recorded as unknown, never invented
            self.assertEqual(m.plans[0].session_name, "wt-a-a")

    def test_dry_run_prints_the_spawn_argv(self):
        import contextlib, io
        from scripts.wave import main
        with tempfile.TemporaryDirectory() as td:
            os.environ["BSTACK_WAVE_CACHE_DIR"] = td + "/cache"
            repo = _init_repo(Path(td))
            pa = _put_plan(repo, "a")
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf), self.assertRaises(SystemExit):
                main(["dispatch", "--dry-run", str(pa)])
            out = buf.getvalue()
            self.assertIn("--name wt-a-a", out)
            self.assertIn("--strict-mcp-config", out)
            self.assertIn("crossSessionInbound", out)

    def test_duplicate_session_name_rejected_before_worktree(self):
        """Two plans whose (worktree basename, ticket, slug) compose the same
        name must fail validation: two peers under one name are unaddressable."""
        from scripts.wave import main
        with tempfile.TemporaryDirectory() as td:
            os.environ["BSTACK_WAVE_CACHE_DIR"] = td + "/cache"
            os.environ["BSTACK_WAVE_CLAUDE_BIN"] = "/bin/true"
            repo = _init_repo(Path(td))
            pa = _put_plan(repo, "a")
            # Different worktree dir + different branch, but the basename
            # `wt-a` and slug `a` compose the same `wt-a-a`.
            pb = repo / "plan-a-twin.md"
            pb.write_text(
                "---\nwave:\n  worktree: ../other/wt-a\n  branch: feat/a-twin\n"
                "  slug: a\n---\n\n# Twin\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(repo), "add", str(pb)], check=True)
            subprocess.run(["git", "-C", str(repo), "commit", "-qm", "twin"], check=True)
            import contextlib, io
            err = io.StringIO()
            with contextlib.redirect_stderr(err), self.assertRaises(SystemExit) as ctx:
                main(["dispatch", str(pa), str(pb)])
            self.assertEqual(ctx.exception.code, 1)
            self.assertIn("duplicate session name", err.getvalue())
            self.assertIn("wt-a-a", err.getvalue())
            self.assertFalse((Path(td) / "wt-a").exists())
            self.assertFalse(Path(td + "/cache").exists())

    def test_bad_mcp_value_is_a_clean_validation_error(self):
        """`mcp: yolo` in the frontmatter must surface as `error: ...` with exit 1,
        not as a PeerError traceback escaping main."""
        from scripts.wave import main
        with tempfile.TemporaryDirectory() as td:
            os.environ["BSTACK_WAVE_CACHE_DIR"] = td + "/cache"
            os.environ["BSTACK_WAVE_CLAUDE_BIN"] = "/bin/true"
            repo = _init_repo(Path(td))
            pa = repo / "plan-a.md"
            pa.write_text("---\nwave:\n  worktree: ../wt-a\n  branch: feat/a\n"
                          "  slug: a\n  mcp: yolo\n---\n\n# Plan\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(repo), "add", str(pa)], check=True)
            subprocess.run(["git", "-C", str(repo), "commit", "-qm", "plan"], check=True)
            import contextlib, io
            err = io.StringIO()
            with contextlib.redirect_stderr(err), self.assertRaises(SystemExit) as ctx:
                main(["dispatch", str(pa)])
            self.assertEqual(ctx.exception.code, 1)
            self.assertIn("error:", err.getvalue())
            self.assertIn("mcp mode", err.getvalue())
            self.assertFalse((Path(td) / "wt-a").exists())

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
