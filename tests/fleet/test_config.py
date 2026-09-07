"""Ontology / config resolution.

The point of the ontology is that `bstack fleet` declares what the client skill
this generalizes hardcoded. Four layers, and the order between them is the
contract: CLI flag > env `BSTACK_FLEET_<KEY>` > `~/.bstack/config.yaml`
`fleet_<key>` > built-in default.
"""
import os
import unittest
from pathlib import Path

from scripts import fleet
from tests.fleet.helpers import sandbox


def _write_config(text: str) -> Path:
    """Write the file `bin/bstack-config` reads, at the location it reads it
    from (`$BSTACK_STATE_DIR/config.yaml`)."""
    p = fleet.config_file()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


class ConfigPrecedenceTest(unittest.TestCase):
    def test_default_when_nothing_is_set(self):
        with sandbox():
            self.assertEqual(fleet.resolve("base"), "main")
            self.assertEqual(fleet.resolve("peer_contract"), "autonomous")
            self.assertEqual(fleet.resolve("mcp"), "strict")
            self.assertEqual(fleet.resolve("ticket_pattern"), r"[A-Za-z]+-\d+")
            self.assertIsNone(fleet.resolve("allowed_tools"))

    def test_config_file_beats_default(self):
        with sandbox():
            _write_config("auto_upgrade: true\nfleet_base: develop\n")
            self.assertEqual(fleet.resolve("base"), "develop")

    def test_env_beats_config_file(self):
        with sandbox():
            _write_config("fleet_base: develop\n")
            os.environ["BSTACK_FLEET_BASE"] = "release"
            self.assertEqual(fleet.resolve("base"), "release")

    def test_flag_beats_env(self):
        with sandbox():
            _write_config("fleet_base: develop\n")
            os.environ["BSTACK_FLEET_BASE"] = "release"
            self.assertEqual(fleet.resolve("base", "trunk"), "trunk")

    def test_full_ladder_on_one_key(self):
        """Peel the layers off one at a time and watch the answer fall
        through: flag, env, file, default."""
        with sandbox():
            _write_config("fleet_peer_contract: from-file\n")
            os.environ["BSTACK_FLEET_PEER_CONTRACT"] = "from-env"
            self.assertEqual(fleet.resolve("peer_contract", "from-flag"), "from-flag")
            self.assertEqual(fleet.resolve("peer_contract"), "from-env")
            os.environ.pop("BSTACK_FLEET_PEER_CONTRACT")
            self.assertEqual(fleet.resolve("peer_contract"), "from-file")
            fleet.config_file().unlink()
            self.assertEqual(fleet.resolve("peer_contract"), "autonomous")

    def test_unknown_key_is_rejected(self):
        with sandbox():
            with self.assertRaises(fleet.FleetError) as ctx:
                fleet.resolve("colour")
            self.assertIn("unknown config key", str(ctx.exception))


class ConfigFileReaderTest(unittest.TestCase):
    def test_comments_blanks_and_inline_comments(self):
        with sandbox():
            _write_config(
                "# a comment line\n"
                "\n"
                "fleet_base: develop   # trailing comment\n"
                'fleet_peer_contract: "quoted"\n'
                "not a key value line\n")
            cfg = fleet.read_config_file()
            self.assertEqual(cfg["fleet_base"], "develop")
            self.assertEqual(cfg["fleet_peer_contract"], "quoted")
            self.assertNotIn("# a comment line", cfg)

    def test_missing_config_file_is_not_an_error(self):
        with sandbox():
            self.assertEqual(fleet.read_config_file(), {})

    def test_config_file_location_follows_bstack_state_dir(self):
        with sandbox() as td:
            self.assertEqual(fleet.config_file(),
                             Path(td / "bstack-state" / "config.yaml"))


class StateDirTest(unittest.TestCase):
    def test_env_state_dir(self):
        with sandbox() as td:
            self.assertEqual(fleet.state_root(), Path(td / "fleet-state"))

    def test_flag_beats_env_state_dir(self):
        with sandbox() as td:
            self.assertEqual(fleet.state_root(str(td / "elsewhere")),
                             Path(td / "elsewhere"))

    def test_default_state_dir_is_under_the_bstack_cache(self):
        with sandbox():
            os.environ.pop("BSTACK_FLEET_STATE_DIR")
            self.assertEqual(fleet.state_root(),
                             Path.home() / ".cache" / "bstack" / "fleet")


class ClaudeBinaryTest(unittest.TestCase):
    def test_fleet_env_wins(self):
        with sandbox():
            os.environ["BSTACK_FLEET_CLAUDE_BIN"] = "/tmp/fleet-claude"
            os.environ["BSTACK_WAVE_CLAUDE_BIN"] = "/tmp/wave-claude"
            self.assertEqual(fleet._claude_binary(), "/tmp/fleet-claude")

    def test_falls_back_to_the_wave_stub(self):
        """A machine already stubbed for wave's suite must not spawn a real
        session just because the fleet variable is unset."""
        with sandbox():
            os.environ["BSTACK_WAVE_CLAUDE_BIN"] = "/tmp/wave-claude"
            self.assertEqual(fleet._claude_binary(), "/tmp/wave-claude")

    def test_default_is_plain_claude(self):
        with sandbox():
            self.assertEqual(fleet._claude_binary(), "claude")


if __name__ == "__main__":
    unittest.main()
