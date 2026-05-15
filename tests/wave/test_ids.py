import re
import time
import unittest
from pathlib import Path


class WaveIdTest(unittest.TestCase):
    def test_format_is_wave_unix_rand4(self):
        from scripts.wave import mint_wave_id
        wid = mint_wave_id()
        self.assertRegex(wid, r"^wave_\d{10,}_[a-z0-9]{4}$")

    def test_total_ordering_by_unix_prefix(self):
        from scripts.wave import mint_wave_id
        a = mint_wave_id()
        time.sleep(1)
        b = mint_wave_id()
        unix_a = int(a.split("_")[1])
        unix_b = int(b.split("_")[1])
        self.assertLess(unix_a, unix_b)

    def test_uniqueness_in_burst(self):
        # Burst sized for ~0.3% birthday collision in 65k slot (token_hex(2))
        # within one unix-second. 100 would flake at ~7.6%.
        from scripts.wave import mint_wave_id
        ids = {mint_wave_id() for _ in range(20)}
        self.assertEqual(len(ids), 20)


class SlugDerivationTest(unittest.TestCase):
    def test_strips_date_prefix(self):
        from scripts.wave import derive_slug
        p = Path("2026-05-07-spec-e-sub-b-inference-backend.md")
        self.assertEqual(derive_slug(p), "spec-e-sub-b-inference-backend")

    def test_strips_md_suffix(self):
        from scripts.wave import derive_slug
        p = Path("/abs/path/foo-bar.md")
        self.assertEqual(derive_slug(p), "foo-bar")

    def test_no_date_prefix_kept(self):
        from scripts.wave import derive_slug
        p = Path("simple-plan.md")
        self.assertEqual(derive_slug(p), "simple-plan")
