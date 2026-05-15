"""Smoke test — proves the wave module is importable and has expected entrypoints."""
import unittest


class WaveModuleSmokeTest(unittest.TestCase):
    def test_module_importable(self):
        from scripts import wave  # noqa: F401

    def test_main_entrypoint_exists(self):
        from scripts import wave
        self.assertTrue(callable(wave.main))

    def test_subcommands_registered(self):
        from scripts import wave
        # main() with no args should exit non-zero (no subcommand) but not crash
        with self.assertRaises(SystemExit):
            wave.main([])
