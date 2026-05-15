import json
import shutil
import tempfile
import unittest
from pathlib import Path


class StatusJsonlTest(unittest.TestCase):
    def setUp(self):
        # Guard against stale wave_report_test from prior aborted runs (macOS
        # os.rename refuses to overwrite a non-empty directory, unlike Linux).
        stale = Path(tempfile.gettempdir()) / "wave_report_test"
        if stale.exists():
            shutil.rmtree(stale, ignore_errors=True)

    def _setup_wave_dir(self, td: str) -> Path:
        from scripts.wave import write_manifest, Manifest, PlanEntry
        wd = Path(td)
        m = Manifest(
            wave_id="wave_test", name=None, created_at="t", repo_root="/",
            plans=[PlanEntry(
                slug="slug-a", plan_path="/p", worktree="/w", branch="b",
                base="main", linear=None, agent_pid=None, launched_at=None,
            )],
        )
        write_manifest(wd, m)
        return wd

    def test_append_started_event(self):
        from scripts.wave import append_status_event
        with tempfile.TemporaryDirectory() as td:
            wd = self._setup_wave_dir(td)
            append_status_event(wd, "slug-a", "started", {})
            jl = wd / "slug-a.status.jsonl"
            self.assertTrue(jl.exists())
            line = jl.read_text().strip()
            obj = json.loads(line)
            self.assertEqual(obj["event"], "started")
            self.assertIn("ts", obj)

    def test_append_with_extras(self):
        from scripts.wave import append_status_event
        with tempfile.TemporaryDirectory() as td:
            wd = self._setup_wave_dir(td)
            append_status_event(wd, "slug-a", "pr_opened", {"pr": "https://x/1"})
            obj = json.loads((wd / "slug-a.status.jsonl").read_text().strip())
            self.assertEqual(obj["pr"], "https://x/1")

    def test_append_unknown_slug_raises(self):
        from scripts.wave import append_status_event, WaveError
        with tempfile.TemporaryDirectory() as td:
            wd = self._setup_wave_dir(td)
            with self.assertRaises(WaveError):
                append_status_event(wd, "slug-unknown", "started", {})

    def test_multiple_appends_one_per_line(self):
        from scripts.wave import append_status_event
        with tempfile.TemporaryDirectory() as td:
            wd = self._setup_wave_dir(td)
            append_status_event(wd, "slug-a", "started", {})
            append_status_event(wd, "slug-a", "branch_pushed", {"branch": "b", "head": "abc"})
            lines = (wd / "slug-a.status.jsonl").read_text().strip().split("\n")
            self.assertEqual(len(lines), 2)
            self.assertEqual(json.loads(lines[1])["event"], "branch_pushed")

    def test_report_subcommand_routes_to_append(self):
        from scripts.wave import main
        with tempfile.TemporaryDirectory() as td:
            wd = self._setup_wave_dir(td)
            import os
            old_cache = os.environ.get("BSTACK_WAVE_CACHE_DIR")
            os.environ["BSTACK_WAVE_CACHE_DIR"] = str(wd.parent)
            try:
                wd2 = wd.parent / "wave_report_test"
                wd.rename(wd2)
                with self.assertRaises(SystemExit) as ctx:
                    main(["report", "--wave", "wave_report_test", "--plan",
                          "slug-a", "--event", "started"])
                self.assertEqual(ctx.exception.code, 0)
                self.assertTrue((wd2 / "slug-a.status.jsonl").exists())
            finally:
                if old_cache is None:
                    os.environ.pop("BSTACK_WAVE_CACHE_DIR", None)
                else:
                    os.environ["BSTACK_WAVE_CACHE_DIR"] = old_cache
