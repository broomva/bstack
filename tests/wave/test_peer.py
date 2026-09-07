"""The peer-session spawn contract (scripts/peer.py). Every assertion here is
a clause of Fanout (P5) that a mutation must break: drop a flag, loosen the
name grammar, or skip the ANSI strip and a test goes red."""
import json
import os
import unittest
from pathlib import Path

from scripts import peer

ANSI_BG = ("\x1b[1mbackgrounded\x1b[0m \x1b[2m·\x1b[0m \x1b[36m0f29e602\x1b[0m\n"
           "  claude agents             list sessions\n"
           "  claude attach 0f29e602    open in this terminal\n")


class NameGrammarTest(unittest.TestCase):
    def test_canonical_shape(self):
        self.assertEqual(peer.compose_name("/x/wt/cusk", "STI-2669", "fleet-aware"),
                         "cusk-sti-2669-fleet-aware")

    def test_ticketless_arc_omits_the_ticket_part(self):
        self.assertEqual(peer.compose_name("/x/cusk", None, "flaky-test"),
                         "cusk-flaky-test")

    def test_lowercase_hyphens_only(self):
        name = peer.compose_name("/x/My_Worktree", "BRO/12", "Fix Slash/Under_score")
        self.assertRegex(name, r"^[a-z0-9]+(-[a-z0-9]+)*$")
        self.assertEqual(name, "my-worktree-bro-12-fix-slash-under-score")

    def test_length_cap_and_no_trailing_hyphen(self):
        name = peer.compose_name("/x/w", None, "a-" * 80)
        self.assertLessEqual(len(name), peer.NAME_MAX)
        self.assertFalse(name.endswith("-"))

    def test_empty_parts_raise(self):
        with self.assertRaises(peer.PeerError):
            peer.compose_name("", None, "!!!")


class SpawnArgvTest(unittest.TestCase):
    def _argv(self, **kw):
        return peer.build_spawn_argv("wt-bro-1-x", "do the thing", **kw)

    def test_bg_name_first(self):
        argv = self._argv()
        self.assertEqual(argv[:4], ["claude", "--bg", "--name", "wt-bro-1-x"])

    def test_strict_mcp_flag_present_by_default(self):
        self.assertIn(peer.STRICT_MCP_FLAG, self._argv())

    def test_inbound_accept_settings_present(self):
        argv = self._argv()
        i = argv.index("--settings")
        self.assertEqual(argv[i + 1], '{"crossSessionInbound":"accept"}')

    def test_prompt_is_last_and_positional(self):
        self.assertEqual(self._argv()[-1], "do the thing")
        self.assertNotIn("-p", self._argv())

    def test_inherit_mode_drops_strict_only(self):
        argv = self._argv(mcp="inherit")
        self.assertNotIn(peer.STRICT_MCP_FLAG, argv)
        self.assertIn("--settings", argv)
        self.assertIn("--name", argv)

    def test_bad_mcp_mode_rejected(self):
        with self.assertRaises(peer.PeerError):
            self._argv(mcp="yolo")

    def test_optional_role_model_tools(self):
        argv = self._argv(agent="reviewer", model="opus", allowed_tools="Read,Grep")
        for flag, val in (("--agent", "reviewer"), ("--model", "opus"),
                          ("--allowedTools", "Read,Grep")):
            self.assertEqual(argv[argv.index(flag) + 1], val)
        self.assertEqual(argv[-1], "do the thing")

    def test_no_prompt_means_no_positional(self):
        argv = peer.build_spawn_argv("n", None)
        self.assertEqual(argv[-2:], ["--settings", peer.INBOUND_ACCEPT_SETTINGS])

    def test_unnamed_peer_rejected(self):
        with self.assertRaises(peer.PeerError):
            peer.build_spawn_argv("", "x")


class McpModeTest(unittest.TestCase):
    def setUp(self):
        self._saved = os.environ.pop("BSTACK_PEER_MCP", None)

    def tearDown(self):
        if self._saved is not None:
            os.environ["BSTACK_PEER_MCP"] = self._saved
        else:
            os.environ.pop("BSTACK_PEER_MCP", None)

    def test_default_is_strict(self):
        self.assertEqual(peer.mcp_mode(None), "strict")

    def test_env_overrides_default(self):
        os.environ["BSTACK_PEER_MCP"] = "inherit"
        self.assertEqual(peer.mcp_mode(None), "inherit")

    def test_explicit_beats_env(self):
        os.environ["BSTACK_PEER_MCP"] = "inherit"
        self.assertEqual(peer.mcp_mode("strict"), "strict")

    def test_invalid_rejected(self):
        with self.assertRaises(peer.PeerError):
            peer.mcp_mode("permissive")


class SessionIdCaptureTest(unittest.TestCase):
    def test_ansi_coloured_output_yields_id(self):
        self.assertEqual(peer.parse_session_id(ANSI_BG), "0f29e602")

    def test_plain_output_yields_id(self):
        self.assertEqual(peer.parse_session_id("backgrounded · 2d59ee14\n"), "2d59ee14")

    def test_no_id_yields_none(self):
        self.assertIsNone(peer.parse_session_id("Login expired. Please run /login"))
        self.assertIsNone(peer.parse_session_id(""))

    def test_spawn_result_summary_reports_error_line(self):
        r = peer.SpawnResult(name="n", session_id=None, returncode=1,
                             output="boom\nLogin expired", ok=False)
        self.assertTrue(r.summary.startswith("ERROR: "))
        self.assertIn("Login expired", r.summary)


class LivenessTest(unittest.TestCase):
    """Bound to the artifact: the fixture is a scrubbed capture of
    `claude agents --json --all` on Claude Code 2.1.258 (32 entries, 11 distinct
    kind/state/status shapes). An earlier draft keyed on an invented `needs`
    field; the first assertion here is that no such field exists."""
    FIX = Path(__file__).parent / "fixtures" / "claude-agents-2.1.258.json"

    @classmethod
    def setUpClass(cls):
        cls.agents = json.loads(cls.FIX.read_text())
        cls.by_name = {a["name"]: a for a in cls.agents}

    def _cls(self, name):
        return peer.liveness(self.agents, session_id=None, name=name)[0]

    def test_fixture_is_the_real_schema_not_an_invented_one(self):
        keys = {k for a in self.agents for k in a}
        self.assertNotIn("needs", keys)
        self.assertTrue({"kind", "sessionId", "name", "cwd"} <= keys)
        self.assertTrue(any(a.get("status") == "waiting" for a in self.agents))
        self.assertTrue(any(a.get("state") == "done" and a.get("pid") for a in self.agents))
        self.assertTrue(any(a.get("state") in ("failed", "stopped") for a in self.agents))

    def test_busy_working_with_pid_is_live(self):
        a = next(a for a in self.agents if a.get("status") == "busy" and a.get("state") == "working")
        self.assertEqual(peer.classify(a), peer.LIVE)

    def test_waiting_status_is_waiting_with_reason(self):
        a = next(a for a in self.agents if a.get("status") == "waiting")
        self.assertEqual(peer.classify(a), peer.WAITING)
        self.assertEqual(peer.waiting_for(a), a["waitingFor"])
        self.assertEqual(peer.IDLE_START, peer.WAITING)   # older name still resolves

    def test_done_state_is_done_even_with_a_live_pid(self):
        a = next(a for a in self.agents if a.get("state") == "done" and a.get("pid"))
        self.assertEqual(peer.classify(a), peer.DONE)

    def test_failed_and_stopped_are_gone(self):
        for st in ("failed", "stopped"):
            a = next(a for a in self.agents if a.get("state") == st)
            self.assertEqual(peer.classify(a), peer.GONE, st)

    def test_done_without_pid_is_done_not_gone(self):
        """A finished turn whose process has exited is still `done`: the state
        is terminal and known, which is more than `gone` says."""
        a = next(a for a in self.agents if a.get("state") == "done" and not a.get("pid"))
        self.assertEqual(peer.classify(a), peer.DONE)

    def test_terminal_state_beats_a_lingering_pid(self):
        """Synthetic: `stopped`/`failed` with a pid that has not been reaped yet
        is still gone — the state is terminal whatever the process table says."""
        for st in ("stopped", "failed"):
            a = {"id": "0badf00d", "sessionId": "0badf00d-0000", "kind": "background",
                 "name": "x", "cwd": "/w/x", "state": st, "pid": 4242, "status": "idle"}
            self.assertEqual(peer.classify(a), peer.GONE, st)

    def test_blocked_without_pid_is_gone_not_blocked(self):
        a = {"id": "deadbeef", "sessionId": "deadbeef-0000", "kind": "background",
             "name": "x", "cwd": "/w/x", "state": "blocked"}
        self.assertEqual(peer.classify(a), peer.GONE)

    def test_background_blocked_with_pid_is_waiting_not_live(self):
        """The shape every wave/fleet peer actually reaches when it needs the
        operator: a --bg session sets state=blocked, never status=waiting."""
        a = next(a for a in self.agents
                 if a.get("kind") == "background" and a.get("state") == "blocked" and a.get("pid"))
        self.assertEqual(peer.classify(a), peer.WAITING)

    def test_background_peers_never_carry_status_waiting(self):
        """Guards the classifier's premise: if a future build starts setting
        status=waiting on --bg entries this test flags that the WAITING branch
        was reachable only via state=blocked when it was written."""
        bg_waiting = [a for a in self.agents
                      if a.get("kind") == "background" and a.get("status") == "waiting"]
        self.assertEqual(bg_waiting, [])

    def test_waiting_reason_is_empty_for_background(self):
        a = next(a for a in self.agents
                 if a.get("kind") == "background" and a.get("state") == "blocked" and a.get("pid"))
        self.assertEqual(peer.waiting_for(a), "")   # bg entries carry no waitingFor

    def test_unlisted_is_gone(self):
        self.assertEqual(peer.liveness(self.agents, session_id="ffffffff", name="nope")[0], peer.GONE)

    def test_join_by_short_id_hits_background_entry(self):
        a = next(a for a in self.agents if a.get("id"))
        cls, entry = peer.liveness(self.agents, session_id=a["id"], name=None)
        self.assertIs(entry, a)

    def test_join_by_session_id_prefix_hits_interactive_entry(self):
        a = next(a for a in self.agents if a.get("kind") == "interactive")
        self.assertNotIn("id", a)
        cls, entry = peer.liveness(self.agents, session_id=a["sessionId"][:8], name=None)
        self.assertIs(entry, a)

    def test_recorded_id_absent_is_gone_not_adopted_by_name(self):
        """A re-dispatch reuses the deterministic name; an old wave whose peer's
        id has left the listing must read gone, not adopt the new peer."""
        live = next(a for a in self.agents if a.get("id") and a.get("pid"))
        cls, entry = peer.liveness(self.agents, session_id="deadbe00", name=live["name"])
        self.assertIsNone(entry)
        self.assertEqual(cls, peer.GONE)

    def test_cwd_join_ignores_an_interactive_session_in_the_worktree(self):
        """The operator opening a `claude` in a peer's worktree must not be
        adopted as the (legacy, id-less) peer and reported live."""
        import collections
        counts = collections.Counter(a["cwd"] for a in self.agents)
        inter = next(a for a in self.agents
                     if a.get("kind") == "interactive" and counts[a["cwd"]] == 1)
        cls, entry = peer.liveness(self.agents, session_id=None, name=None, cwd=inter["cwd"])
        self.assertIsNone(entry)

    def test_join_falls_back_to_name(self):
        a = next(a for a in self.agents if a.get("kind") == "interactive" and a.get("pid"))
        cls, entry = peer.liveness(self.agents, session_id=None, name=a["name"])
        self.assertIs(entry, a)
        self.assertIn(cls, (peer.LIVE, peer.WAITING))

    def test_join_by_cwd_when_unique(self):
        import collections
        counts = collections.Counter(a["cwd"] for a in self.agents)
        cwd = next(c for c, n in counts.items() if n == 1)
        cls, entry = peer.liveness(self.agents, session_id=None, name=None, cwd=cwd)
        self.assertEqual(entry["cwd"], cwd)

    def test_join_by_cwd_refuses_ambiguity(self):
        import collections
        counts = collections.Counter(a["cwd"] for a in self.agents)
        cwd = next(c for c, n in counts.items() if n > 1)
        cls, entry = peer.liveness(self.agents, session_id="zzzzzzzz", name="zz", cwd=cwd)
        self.assertIsNone(entry)
        self.assertEqual(cls, peer.GONE)

    def test_unreadable_listing_is_unknown_not_clean(self):
        self.assertEqual(peer.liveness(None, session_id="aaaaaa01", name=None)[0], peer.UNKNOWN)

    def test_nothing_to_join_on_is_unknown(self):
        self.assertEqual(peer.liveness(self.agents, session_id=None, name=None)[0], peer.UNKNOWN)


class AnsiAndTimeoutTest(unittest.TestCase):
    def test_osc_hyperlink_and_title_are_stripped(self):
        osc8 = "\x1b]8;;https://x\x07backgrounded\x1b]8;;\x07 · \x1b]0;title\x1b\\0f29e602\n"
        self.assertEqual(peer.parse_session_id(osc8), "0f29e602")
        self.assertNotIn("\x1b", peer.strip_ansi(osc8))

    def test_id_must_be_on_the_same_line(self):
        self.assertIsNone(peer.parse_session_id("backgrounded\n\n0000000000 table rule\n"))
        self.assertIsNone(peer.parse_session_id("backgrounded at 1788758028\n"))

    def test_timeout_keeps_an_id_the_launcher_already_printed(self):
        import tempfile, os as _os
        with tempfile.TemporaryDirectory() as td:
            stub = Path(td) / "slow.sh"
            stub.write_text("#!/bin/sh\nprintf 'backgrounded · 0f29e602\\n'\nsleep 5\n")
            stub.chmod(0o755)
            r = peer.spawn([str(stub), "--bg", "--name", "n"], timeout=1)
            self.assertFalse(r.ok)
            self.assertEqual(r.session_id, "0f29e602")
            self.assertIn("timeout", r.output)
            self.assertIn("0f29e602", r.output)

    def test_spawn_timeout_env(self):
        import os as _os
        saved = _os.environ.pop("BSTACK_PEER_SPAWN_TIMEOUT", None)
        try:
            self.assertEqual(peer.spawn_timeout(60.0), 60.0)
            _os.environ["BSTACK_PEER_SPAWN_TIMEOUT"] = "7"
            self.assertEqual(peer.spawn_timeout(60.0), 7.0)
            _os.environ["BSTACK_PEER_SPAWN_TIMEOUT"] = "nope"
            self.assertEqual(peer.spawn_timeout(60.0), 60.0)
        finally:
            _os.environ.pop("BSTACK_PEER_SPAWN_TIMEOUT", None)
            if saved is not None:
                _os.environ["BSTACK_PEER_SPAWN_TIMEOUT"] = saved


if __name__ == "__main__":
    unittest.main()
