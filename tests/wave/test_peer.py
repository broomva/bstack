"""The peer-session spawn contract (scripts/peer.py). Every assertion here is
a clause of Fanout (P5) that a mutation must break: drop a flag, loosen the
name grammar, or skip the ANSI strip and a test goes red."""
import os
import unittest

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
    AGENTS = [
        {"id": "aaaaaa01", "name": "wt-bro-1-live", "state": "working", "pid": 4242},
        {"id": "aaaaaa02", "name": "wt-bro-2-idle", "state": "blocked",
         "needs": "send a prompt to start", "pid": 4243},
        {"id": "aaaaaa03", "name": "wt-bro-3-dead", "state": "blocked"},
        {"id": "aaaaaa04", "name": "wt-bro-4-done", "state": "done"},
    ]

    def test_pid_means_live(self):
        self.assertEqual(peer.liveness(self.AGENTS, session_id="aaaaaa01", name=None)[0],
                         peer.LIVE)

    def test_needs_means_idle_start_even_with_pid(self):
        self.assertEqual(peer.liveness(self.AGENTS, session_id="aaaaaa02", name=None)[0],
                         peer.IDLE_START)

    def test_blocked_without_pid_is_gone_not_blocked(self):
        self.assertEqual(peer.liveness(self.AGENTS, session_id="aaaaaa03", name=None)[0],
                         peer.GONE)

    def test_done_state(self):
        self.assertEqual(peer.liveness(self.AGENTS, session_id="aaaaaa04", name=None)[0],
                         peer.DONE)

    def test_unlisted_is_gone(self):
        self.assertEqual(peer.liveness(self.AGENTS, session_id="ffffffff", name="nope")[0],
                         peer.GONE)

    def test_join_falls_back_to_name(self):
        cls, entry = peer.liveness(self.AGENTS, session_id=None, name="wt-bro-1-live")
        self.assertEqual(cls, peer.LIVE)
        self.assertEqual(entry["id"], "aaaaaa01")

    def test_unreadable_listing_is_unknown_not_clean(self):
        self.assertEqual(peer.liveness(None, session_id="aaaaaa01", name=None)[0],
                         peer.UNKNOWN)

    def test_nothing_to_join_on_is_unknown(self):
        self.assertEqual(peer.liveness(self.AGENTS, session_id=None, name=None)[0],
                         peer.UNKNOWN)


if __name__ == "__main__":
    unittest.main()
