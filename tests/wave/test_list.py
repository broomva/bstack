import os
import tempfile
import unittest
from pathlib import Path


class ListCommandTest(unittest.TestCase):
    def test_no_waves(self):
        from scripts.wave import list_waves
        with tempfile.TemporaryDirectory() as td:
            os.environ["BSTACK_WAVE_CACHE_DIR"] = td
            self.assertEqual(list_waves(), [])

    def test_lists_waves(self):
        from scripts.wave import list_waves, write_manifest, Manifest, PlanEntry
        with tempfile.TemporaryDirectory() as td:
            os.environ["BSTACK_WAVE_CACHE_DIR"] = td
            for wid in ("wave_a", "wave_b"):
                wd = Path(td) / wid
                write_manifest(wd, Manifest(
                    wave_id=wid, name=None, created_at="t", repo_root="/",
                    plans=[PlanEntry(slug="s", plan_path="/p", worktree="/w",
                                     branch="b", base="main", linear=None,
                                     agent_pid=None, launched_at=None)],
                ))
            waves = list_waves()
            self.assertEqual({w["wave_id"] for w in waves}, {"wave_a", "wave_b"})

    def test_summary_counts_states(self):
        from scripts.wave import list_waves, write_manifest, Manifest, PlanEntry, \
            append_status_event
        with tempfile.TemporaryDirectory() as td:
            os.environ["BSTACK_WAVE_CACHE_DIR"] = td
            wd = Path(td) / "wave_c"
            write_manifest(wd, Manifest(
                wave_id="wave_c", name=None, created_at="t", repo_root="/",
                plans=[
                    PlanEntry(slug="a", plan_path="/p", worktree="/w", branch="b",
                              base="main", linear=None, agent_pid=None, launched_at=None),
                    PlanEntry(slug="b", plan_path="/p", worktree="/w", branch="b2",
                              base="main", linear=None, agent_pid=None, launched_at=None),
                ],
            ))
            append_status_event(wd, "a", "pr_merged", {"merge_sha": "x"})
            append_status_event(wd, "b", "pr_opened", {"pr": "https://x/1"})
            summary = list_waves()[0]["summary"]
            self.assertEqual(summary["merged"], 1)
            self.assertEqual(summary["open_pr"], 1)
