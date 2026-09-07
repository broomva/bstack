import json
import tempfile
import unittest
from pathlib import Path


class ManifestTest(unittest.TestCase):
    def test_write_read_roundtrip(self):
        from scripts.wave import write_manifest, read_manifest, Manifest, PlanEntry
        with tempfile.TemporaryDirectory() as td:
            wd = Path(td)
            m = Manifest(
                wave_id="wave_1700000000_ab12",
                name="test-wave",
                created_at="2026-05-13T15:30:00Z",
                repo_root="/abs/repo",
                plans=[
                    PlanEntry(
                        slug="spec-e-sub-b",
                        plan_path="/abs/plan.md",
                        worktree="/abs/wt",
                        branch="feat/x",
                        base="main",
                        linear="BRO-1023",
                        agent_pid=12345,
                        launched_at="2026-05-13T15:30:02Z",
                    ),
                ],
            )
            write_manifest(wd, m)
            self.assertTrue((wd / "manifest.json").exists())
            m2 = read_manifest(wd)
            self.assertEqual(m2.wave_id, m.wave_id)
            self.assertEqual(len(m2.plans), 1)
            self.assertEqual(m2.plans[0].slug, "spec-e-sub-b")

    def test_unknown_schema_version_raises(self):
        from scripts.wave import read_manifest, WaveError
        with tempfile.TemporaryDirectory() as td:
            wd = Path(td)
            (wd / "manifest.json").write_text(json.dumps({
                "schema_version": 999,
                "wave_id": "wave_x",
                "name": None,
                "created_at": "2026-05-13T15:30:00Z",
                "repo_root": "/",
                "plans": [],
            }))
            with self.assertRaises(WaveError) as ctx:
                read_manifest(wd)
            self.assertIn("schema_version", str(ctx.exception))

    def test_missing_manifest_raises(self):
        from scripts.wave import read_manifest, WaveError
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(WaveError):
                read_manifest(Path(td))

    def test_schema_version_written(self):
        from scripts.wave import write_manifest, Manifest
        with tempfile.TemporaryDirectory() as td:
            wd = Path(td)
            m = Manifest(wave_id="wave_x", name=None, created_at="t",
                         repo_root="/", plans=[])
            write_manifest(wd, m)
            raw = json.loads((wd / "manifest.json").read_text())
            self.assertEqual(raw["schema_version"], 1)


class ManifestAtomicityTest(unittest.TestCase):
    def test_readers_never_see_a_partial_manifest(self):
        """Peers read manifest.json while dispatch rewrites it after every
        spawn. A truncate-then-write hands readers half a file; an atomic
        replace never does. 200 plans × 150 rewrites against a hot reader."""
        import threading
        from scripts.wave import write_manifest, read_manifest, Manifest, PlanEntry, WaveError
        with tempfile.TemporaryDirectory() as td:
            wd = Path(td)
            plans = [PlanEntry(slug=f"s{i}", plan_path="/p", worktree="/w", branch=f"b{i}",
                               base="main", linear=None, agent_pid=None, launched_at=None)
                     for i in range(200)]
            m = Manifest(wave_id="wave_atomic", name=None, created_at="t", repo_root="/", plans=plans)
            write_manifest(wd, m)
            stop = threading.Event(); failures = []; reads = [0]
            def reader():
                while not stop.is_set():
                    try:
                        read_manifest(wd); reads[0] += 1
                    except WaveError as exc:
                        failures.append(str(exc))
            th = [threading.Thread(target=reader) for _ in range(3)]
            for t in th: t.start()
            for i in range(150):
                plans[i % 200].session_id = f"id{i:06d}"
                write_manifest(wd, m)
            stop.set()
            for t in th: t.join()
            self.assertGreater(reads[0], 0)
            self.assertEqual(failures, [], failures[:3])
            self.assertEqual([p.name for p in wd.iterdir()], ["manifest.json"])
