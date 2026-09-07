"""`write_state` is atomic — the manifest a reader lands on is never torn.

`up` rewrites `fleet.json` after every spawn; `status`, `list`, `down` and a
peer's own read all land on the same file, unsynchronised. A truncate-then-write
would hand a concurrent reader an empty or half-written file. The write goes to
a sibling tmp in the same directory and is `os.replace`d in.
"""
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from scripts import fleet
from tests.fleet.helpers import sandbox


def _big_state(fleet_id: str, n: int = 300) -> fleet.FleetState:
    """A state large enough that a truncate-then-write leaves a wide torn
    window for a concurrent reader to fall into."""
    peers = [fleet.PeerState(
        name=f"wt-bro-1-peer{i:03d}", slug=f"peer{i}", ticket="BRO-1",
        worktree="/w/wt", brief_path=f"/w/wt/briefs/peer{i:03d}.md",
        session_id=f"sid{i:05d}", spawned=True,
        owns=[f"pkg/mod{i}/**", f"docs/mod{i}/**", f"tests/mod{i}/**"])
        for i in range(n)]
    return fleet.FleetState(fleet_id=fleet_id, created_at="2026-01-01T00:00:00Z",
                            base_worktree="/w/wt", peers=peers)


class AtomicWriteTest(unittest.TestCase):
    def test_concurrent_readers_never_see_a_torn_file(self):
        with sandbox():
            fd = Path(os.environ["BSTACK_FLEET_STATE_DIR"]) / "fleet_torn"
            state = _big_state("fleet_torn")
            fleet.write_state(fd, state)            # seed a complete file
            target = fd / "fleet.json"

            errors: list = []
            stop = threading.Event()

            def reader():
                while not stop.is_set():
                    try:
                        data = json.loads(target.read_text(encoding="utf-8"))
                    except FileNotFoundError:
                        continue
                    except Exception as exc:               # torn / partial JSON
                        errors.append(("torn", repr(exc)))
                        return
                    if len(data.get("peers", [])) != 300:
                        errors.append(("short", len(data.get("peers", []))))
                        return

            readers = [threading.Thread(target=reader) for _ in range(6)]
            for r in readers:
                r.start()
            for _ in range(400):
                fleet.write_state(fd, state)
            stop.set()
            for r in readers:
                r.join()
            self.assertEqual(errors, [], f"torn reads observed: {errors[:3]}")

    def test_tmp_is_written_inside_the_state_dir(self):
        """The replace source must sit in the state dir, not
        `tempfile.gettempdir()` — a cross-device tmp both breaks the atomic
        rename and (the mutant) hides that the write was ever staged there."""
        with sandbox():
            fd = Path(os.environ["BSTACK_FLEET_STATE_DIR"]) / "fleet_tmp"
            state = _big_state("fleet_tmp", n=2)
            with mock.patch("os.replace", wraps=os.replace) as m:
                fleet.write_state(fd, state)
            self.assertTrue(m.called, "write_state did not go through os.replace")
            src = Path(m.call_args.args[0])
            dst = Path(m.call_args.args[1])
            self.assertEqual(src.parent, fd)
            self.assertNotEqual(src.parent, Path(tempfile.gettempdir()))
            self.assertEqual(dst, fd / "fleet.json")


if __name__ == "__main__":
    unittest.main()
