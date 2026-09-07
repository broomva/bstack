import json
import tempfile
import unittest
from pathlib import Path

FIX = Path(__file__).parent / "fixtures" / "claude-agents-2.1.258.json"


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


class StatusLivenessTest(unittest.TestCase):
    """The LIVE column and the suggestions, joined against the real listing
    shape. Without `agents` every row must be `unknown`, never `live`."""

    def _wave_with_sessions(self, td, plans):
        from scripts.wave import write_manifest, Manifest, PlanEntry
        import os
        os.environ["BSTACK_WAVE_CACHE_DIR"] = td
        wd = Path(td) / "wave_live"
        write_manifest(wd, Manifest(
            wave_id="wave_live", name="t", created_at="t", repo_root="/",
            plans=[PlanEntry(slug=slug, plan_path=f"/p/{slug}", worktree=wt, branch=f"feat/{slug}",
                             base="main", linear=None, agent_pid=None, launched_at=None,
                             session_id=sid, session_name=name)
                   for slug, wt, sid, name in plans]))
        for slug, *_ in plans:
            (wd / f"{slug}.status.jsonl").write_text(json.dumps({"ts": "t", "event": "started"}) + "\n")
        return wd

    def test_live_column_reflects_the_listing(self):
        from scripts.wave import render_status_table
        from scripts import peer
        agents = json.loads(FIX.read_text())
        live = next(a for a in agents if a.get("status") == "busy" and a.get("state") == "working")
        waiting = next(a for a in agents if a.get("status") == "waiting")
        done = next(a for a in agents if a.get("state") == "done" and a.get("pid"))
        with tempfile.TemporaryDirectory() as td:
            wd = self._wave_with_sessions(td, [
                ("p-live", "/w/live", live.get("id") or live["sessionId"][:8], live["name"]),
                ("p-wait", "/w/wait", waiting.get("id") or waiting["sessionId"][:8], waiting["name"]),
                ("p-done", "/w/done", done.get("id") or done["sessionId"][:8], done["name"]),
                ("p-gone", "/w/gone", "ffffffff", "no-such-peer"),
            ])
            out = render_status_table(wd, agents=agents)
            rows = {ln.split()[0]: ln for ln in out.splitlines() if ln.strip().startswith("p-")}
            self.assertTrue(rows["p-live"].rstrip().endswith(peer.LIVE), rows["p-live"])
            self.assertTrue(rows["p-wait"].rstrip().endswith(peer.WAITING), rows["p-wait"])
            self.assertTrue(rows["p-done"].rstrip().endswith(peer.DONE), rows["p-done"])
            self.assertTrue(rows["p-gone"].rstrip().endswith(peer.GONE), rows["p-gone"])
            self.assertIn(f"is waiting ({waiting['waitingFor']})", out)
            self.assertIn("finished its turn", out)
            self.assertIn("is gone", out)
            self.assertIn("claude attach", out)

    def test_without_agents_every_row_is_unknown_and_says_why(self):
        from scripts.wave import render_status_table
        with tempfile.TemporaryDirectory() as td:
            wd = self._wave_with_sessions(td, [("p-x", "/w/x", "0f29e602", "wt-x")])
            out = render_status_table(wd, agents=None)
            self.assertIn("unknown", out)
            self.assertNotIn(" live", out)
            self.assertIn("liveness unavailable", out)
