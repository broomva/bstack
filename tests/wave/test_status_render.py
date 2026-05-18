import json
import tempfile
import unittest
from pathlib import Path


def _make_wave(td: str, slugs_and_events: dict):
    from scripts.wave import write_manifest, Manifest, PlanEntry
    import os
    os.environ["BSTACK_WAVE_CACHE_DIR"] = td
    wid = "wave_render_test"
    wd = Path(td) / wid
    m = Manifest(
        wave_id=wid, name="test", created_at="2026-05-13T00:00:00Z",
        repo_root="/", plans=[
            PlanEntry(slug=s, plan_path=f"/p/{s}", worktree=f"/w/{s}",
                      branch=f"feat/{s}", base="main", linear=f"BRO-{i}",
                      agent_pid=None, launched_at=None)
            for i, s in enumerate(slugs_and_events.keys())
        ],
    )
    write_manifest(wd, m)
    for slug, events in slugs_and_events.items():
        jl = wd / f"{slug}.status.jsonl"
        with jl.open("w") as fh:
            for ev in events:
                fh.write(json.dumps(ev) + "\n")
    return wid, wd


class StatusReaderTest(unittest.TestCase):
    def test_latest_event_per_slug(self):
        from scripts.wave import read_wave_state
        with tempfile.TemporaryDirectory() as td:
            wid, wd = _make_wave(td, {
                "a": [{"ts": "2026-05-13T00:01:00Z", "event": "started"},
                      {"ts": "2026-05-13T00:02:00Z", "event": "pr_opened",
                       "pr": "https://x/1"}],
                "b": [{"ts": "2026-05-13T00:01:00Z", "event": "started"}],
            })
            state = read_wave_state(wd)
            self.assertEqual(state["a"]["event"], "pr_opened")
            self.assertEqual(state["a"]["pr"], "https://x/1")
            self.assertEqual(state["b"]["event"], "started")

    def test_no_jsonl_yet(self):
        from scripts.wave import read_wave_state, write_manifest, Manifest, PlanEntry
        with tempfile.TemporaryDirectory() as td:
            wd = Path(td) / "wave_no_events"
            write_manifest(wd, Manifest(
                wave_id="wave_no_events", name=None, created_at="t",
                repo_root="/", plans=[PlanEntry(
                    slug="a", plan_path="/p", worktree="/w", branch="b",
                    base="main", linear=None, agent_pid=None, launched_at=None)]))
            state = read_wave_state(wd)
            self.assertEqual(state["a"]["event"], "pending")


class StatusRenderTest(unittest.TestCase):
    def test_table_includes_slug_branch_last_event(self):
        from scripts.wave import render_status_table
        with tempfile.TemporaryDirectory() as td:
            wid, wd = _make_wave(td, {
                "spec-e-sub-b": [{"ts": "2026-05-13T00:02:00Z", "event": "pr_opened",
                                   "pr": "https://x/1218"}],
            })
            out = render_status_table(wd)
            self.assertIn("spec-e-sub-b", out)
            self.assertIn("feat/spec-e-sub-b", out)
            self.assertIn("pr_opened", out)
            self.assertIn("#1218", out)

    def test_suggestion_p9_after_pr_opened(self):
        from scripts.wave import render_status_table
        with tempfile.TemporaryDirectory() as td:
            wid, wd = _make_wave(td, {
                "a": [{"ts": "2026-05-13T00:02:00Z", "event": "pr_opened",
                       "pr": "https://github.com/o/r/pull/1218"}],
            })
            out = render_status_table(wd)
            self.assertIn("p9 watch", out)

    def test_suggestion_all_merged(self):
        from scripts.wave import render_status_table
        with tempfile.TemporaryDirectory() as td:
            wid, wd = _make_wave(td, {
                "a": [{"ts": "2026-05-13T00:02:00Z", "event": "pr_merged",
                       "merge_sha": "abc"}],
            })
            out = render_status_table(wd)
            self.assertIn("janitor", out.lower())
            self.assertIn("bookkeeping", out.lower())
