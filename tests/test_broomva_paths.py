"""Tests for the path registry (BRO-3216): references/paths.toml, the generator
scripts/gen_paths.py, and the two readers it writes, scripts/broomva_paths.py
and scripts/lib/broomva-paths.sh. stdlib unittest; git is the only tool used.

Every rule the readers state has a case that turns red when the rule breaks:
the migration rule on disk and in a git tree, the repo-root rules (no cwd
fallback, never $HOME), the scope precedence, the generated owner list, and
shell and Python agreeing on every case.
"""

from __future__ import annotations

import ast
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))
import broomva_paths as bp  # noqa: E402
import gen_paths  # noqa: E402

SH_LIB = REPO / "scripts" / "lib" / "broomva-paths.sh"
BASH = "/bin/bash" if Path("/bin/bash").exists() else shutil.which("bash")


def git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True,
                   env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"})


def make_repo(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    git(root, "init", "-q", "-b", "main")
    return root


def sh(script: str, env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run([BASH, "-c", f'. "{SH_LIB}"; {script}'], capture_output=True, text=True,
                          env={**os.environ, **(env or {})})


class Tmp(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="broomva-paths-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)


class Generator(unittest.TestCase):
    def test_committed_readers_are_current(self) -> None:
        self.assertEqual(gen_paths.main(["--check"]), 0, "run python3 scripts/gen_paths.py")

    def test_readers_carry_the_registry_hash(self) -> None:
        want = hashlib.sha256((REPO / "references" / "paths.toml").read_bytes()).hexdigest()
        self.assertEqual(bp.REGISTRY_SHA256, want)
        self.assertIn(f'BROOMVA_REGISTRY_SHA256="{want}"', SH_LIB.read_text())

    def test_python_reader_parses_as_python_3_9(self) -> None:
        # Hooks run under the Mac's system python3, which is 3.9.
        ast.parse((REPO / "scripts" / "broomva_paths.py").read_text(), feature_version=(3, 9))

    def _render(self, toml: str) -> None:
        gen_paths.render(toml.encode())

    BASE = """version = 1
[roots.repo]
dir = ".broomva"
legacy = ".control"
[roots.machine]
env = "BROOMVA_HOME"
default = "~/.broomva"
[roots.workspace]
env = ["BROOMVA_WORKSPACE", "MAESTRO_WORKSPACE", "BSTACK_WORKSPACE"]
default = "~/broomva"
[machine]
config = { path = "config" }
"""

    def test_a_valid_minimal_registry_renders(self) -> None:
        self._render(self.BASE + '[repo]\npolicy = { path = "policy.yaml", owner = true }\n')

    def test_invalid_registries_are_refused(self) -> None:
        bad = {
            "parent path": '[repo]\nx = { path = "../x" }\n',
            "absolute path": '[repo]\nx = { path = "/etc/x" }\n',
            "space in path": '[repo]\nx = { path = "a b" }\n',
            "shared path": '[repo]\nx = { path = "p" }\ny = { path = "p" }\n',
            "unknown key": '[repo]\nx = { path = "p", owner = true, gate = 1 }\n',
            "bad name": '[repo]\nX = { path = "p" }\n',
            "string legacy on repo": '[repo]\nx = { path = "p", legacy = "y" }\n',
            "empty repo table": "",
        }
        for why, extra in bad.items():
            with self.subTest(why), self.assertRaises(gen_paths.RegistryError):
                self._render(self.BASE + extra)
        with self.assertRaises(gen_paths.RegistryError):
            self._render(self.BASE.replace('legacy = ".control"', 'legacy = ".ctl"') + '[repo]\nx = { path = "p" }\n')
        with self.assertRaises(gen_paths.RegistryError):
            self._render(self.BASE + '[repo]\nx = { path = "p" }\n'
                         + '[machine.work]\npath = "work"\nlegacy = "maestro"\n')

    def test_owner_paths_are_every_owner_entry_under_both_spellings(self) -> None:
        want = []
        for name, (path, legacy, owner) in bp.REPO.items():
            if owner:
                want.append(f".broomva/{path}")
                if legacy:
                    want.append(f".control/{path}")
        self.assertEqual(list(bp.OWNER_PATHS), want)
        for must in (".broomva/policy.yaml", ".control/policy.yaml", ".broomva/preauth.yaml",
                     ".control/preauth.yaml", ".broomva/allowed_signers", ".control/allowed_signers"):
            self.assertIn(must, bp.OWNER_PATHS)
        self.assertNotIn(".control/hooks", bp.OWNER_PATHS, "hooks/ was never under .control/")
        self.assertEqual(set(sh("echo $BROOMVA_OWNER_PATHS").stdout.split()), set(bp.OWNER_PATHS))

    def test_every_workspace_control_entry_has_a_name(self) -> None:
        # The workspace's .control/ as of 2026-10-20: a new file there needs a registry name.
        listed = {p for p, legacy, _ in bp.REPO.values() if legacy}
        for entry in ("arcs.yaml", "asks", "audit", "bands", "hillclimb", "i2p-profile.example.json",
                      "initiatives", "leverage-metrics.jsonl", "leverage-setpoints.yaml",
                      "leverage-ship-state.json", "leverage-state.json", "policy.yaml", "preauth.yaml",
                      "rcs-parameters.toml", "spec-contract-grandfathered.txt"):
            self.assertIn(entry, listed)


class DiskRule(Tmp):
    """The migration rule on disk, for repo and machine names."""

    def test_new_location_wins_when_it_exists(self) -> None:
        (self.tmp / ".broomva").mkdir()
        (self.tmp / ".control").mkdir()
        (self.tmp / ".broomva" / "policy.yaml").write_text("")
        (self.tmp / ".control" / "policy.yaml").write_text("")
        self.assertEqual(bp.repo_path("policy", self.tmp), self.tmp / ".broomva" / "policy.yaml")

    def test_legacy_location_when_only_it_exists(self) -> None:
        (self.tmp / ".control").mkdir()
        (self.tmp / ".control" / "asks").mkdir()
        self.assertEqual(bp.repo_path("asks", self.tmp), self.tmp / ".control" / "asks")

    def test_an_unmoved_repo_keeps_new_files_in_its_legacy_root(self) -> None:
        # Neither copy exists yet, but .control/ does: a writer must not split the ledger.
        (self.tmp / ".control").mkdir()
        self.assertEqual(bp.repo_path("preauth", self.tmp), self.tmp / ".control" / "preauth.yaml")

    def test_a_moved_or_new_repo_uses_the_new_root(self) -> None:
        self.assertEqual(bp.repo_path("preauth", self.tmp), self.tmp / ".broomva" / "preauth.yaml")

    def test_an_entry_without_legacy_never_resolves_under_control(self) -> None:
        (self.tmp / ".control").mkdir()
        (self.tmp / ".control" / "loops").mkdir()
        self.assertEqual(bp.repo_path("loops", self.tmp), self.tmp / ".broomva" / "loops")

    def test_scope_prefixes_and_unknown_names(self) -> None:
        self.assertEqual(bp.repo_path("repo.asks", self.tmp), bp.repo_path("asks", self.tmp))
        with self.assertRaises(bp.UnknownName):
            bp.repo_path("nope", self.tmp)
        with self.assertRaises(bp.UnknownName):
            bp.machine_path("policy")
        with self.assertRaises(bp.UnknownName):
            bp.resolve("asks")

    def test_shadowed_names_are_reported(self) -> None:
        for d in (".broomva", ".control"):
            (self.tmp / d / "asks").mkdir(parents=True)
        (self.tmp / ".control" / "policy.yaml").write_text("")
        self.assertEqual(bp.shadowed(self.tmp), ["asks"])

    def test_machine_rule(self) -> None:
        home = self.tmp / "home"
        legacy = self.tmp / "maestro"
        with mock.patch.dict(os.environ, {"BROOMVA_HOME": str(home)}), \
                mock.patch.dict(bp.MACHINE, {"work": ("work", str(legacy))}):
            self.assertEqual(bp.machine_path("work"), home / "work", "neither: new")
            legacy.mkdir()
            self.assertEqual(bp.machine_path("machine.work"), legacy, "only legacy: legacy")
            (home / "work").mkdir(parents=True)
            self.assertEqual(bp.machine_path("work"), home / "work", "new exists: new")
            self.assertEqual(bp.machine_path("config"), home / "config", "no legacy: new")

    def test_roots_and_their_precedence(self) -> None:
        clean = {k: "" for k in ("BROOMVA_HOME", "BROOMVA_WORKSPACE", "MAESTRO_WORKSPACE", "BSTACK_WORKSPACE")}
        with mock.patch.dict(os.environ, {**clean, "HOME": str(self.tmp)}):
            self.assertEqual(bp.machine_root(), self.tmp / ".broomva")
            self.assertEqual(bp.workspace_root(), self.tmp / "broomva")
            for key, others in (("BSTACK_WORKSPACE", ()), ("MAESTRO_WORKSPACE", ("BSTACK_WORKSPACE",)),
                                ("BROOMVA_WORKSPACE", ("MAESTRO_WORKSPACE", "BSTACK_WORKSPACE"))):
                env = {key: str(self.tmp / key), **{o: str(self.tmp / o) for o in others}}
                with mock.patch.dict(os.environ, env):
                    self.assertEqual(bp.workspace_root(), self.tmp / key)
            with mock.patch.dict(os.environ, {"BROOMVA_WORKSPACE": str(self.tmp / "ws")}):
                (self.tmp / "ws" / ".control").mkdir(parents=True)
                self.assertEqual(bp.workspace_path("asks"), self.tmp / "ws" / ".control" / "asks")


class RepoRoot(Tmp):
    def test_the_git_top_level_of_the_target(self) -> None:
        repo = make_repo(self.tmp / "r")
        (repo / "a" / "b").mkdir(parents=True)
        self.assertEqual(bp.repo_root(repo / "a" / "b"), repo)
        self.assertEqual(bp.repo_root(repo / "a" / "new" / "deeper" / "file.yaml"), repo,
                         "a file about to be written, in folders that do not exist yet")

    def test_a_nested_repo_is_its_own_root(self) -> None:
        outer = make_repo(self.tmp / "outer")
        inner = make_repo(outer / "inner")
        self.assertEqual(bp.repo_root(inner / "x"), inner)

    def test_outside_a_repo_there_is_no_root(self) -> None:
        self.assertIsNone(bp.repo_root(self.tmp))

    def test_no_target_means_claude_project_dir_never_the_cwd(self) -> None:
        repo = make_repo(self.tmp / "r")
        here = os.getcwd()
        self.addCleanup(os.chdir, here)
        os.chdir(repo)
        with mock.patch.dict(os.environ, {"CLAUDE_PROJECT_DIR": ""}):
            self.assertIsNone(bp.repo_root(), "the cwd is a repo, and must still not be used")
        with mock.patch.dict(os.environ, {"CLAUDE_PROJECT_DIR": str(repo)}):
            self.assertEqual(bp.repo_root(), repo)

    def test_home_is_never_a_repo_root(self) -> None:
        home = make_repo(self.tmp / "home")
        with mock.patch.dict(os.environ, {"HOME": str(home)}):
            self.assertIsNone(bp.repo_root(home / "sub"))
            self.assertEqual(sh(f'broomva_repo_root "{home}/sub"', {"HOME": str(home)}).returncode, 1)

    def test_the_cli_refuses_a_repo_name_without_a_repo(self) -> None:
        with mock.patch.dict(os.environ, {"CLAUDE_PROJECT_DIR": ""}):
            self.assertEqual(bp.main(["resolve", "repo.policy"]), 2)
        self.assertEqual(bp.main(["resolve", "repo.nope", "--root", str(REPO)]), 2)


class TreeRule(Tmp):
    """The migration rule inside a git tree: new path, else legacy, else None."""

    def setUp(self) -> None:
        super().setUp()
        self.repo = make_repo(self.tmp / "r")
        (self.repo / ".control" / "asks").mkdir(parents=True)
        (self.repo / ".control" / "asks" / "a.yaml").write_text("asks: []\n")
        (self.repo / ".broomva" / "loops").mkdir(parents=True)
        (self.repo / ".broomva" / "loops" / "l.yaml").write_text("x: 1\n")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-qm", "before")

    def test_before_and_after_the_move(self) -> None:
        self.assertEqual(bp.tree_path("asks", self.repo, "HEAD"), ".control/asks")
        self.assertEqual(bp.tree_path("loops", self.repo, "HEAD"), ".broomva/loops")
        self.assertIsNone(bp.tree_path("policy", self.repo, "HEAD"))
        git(self.repo, "mv", ".control/asks", ".broomva/asks")
        git(self.repo, "commit", "-qm", "move")
        self.assertEqual(bp.tree_path("asks", self.repo, "HEAD"), ".broomva/asks")
        self.assertEqual(bp.tree_path("asks", self.repo, "HEAD~1"), ".control/asks", "the old ref still reads")

    def test_a_tree_holding_both_spellings_reads_the_new_one(self) -> None:
        (self.repo / ".broomva" / "asks").mkdir(parents=True)
        (self.repo / ".broomva" / "asks" / "a.yaml").write_text("asks: []\n")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-qm", "both")
        self.assertEqual(bp.tree_path("asks", self.repo, "HEAD"), ".broomva/asks")
        self.assertEqual(sh(f'broomva_tree_path asks "{self.repo}" HEAD').stdout.strip(), ".broomva/asks")

    def test_the_working_tree_does_not_leak_into_a_ref(self) -> None:
        # The round-8 trap: a move only on disk must not look moved in the ref.
        shutil.move(str(self.repo / ".control" / "asks"), str(self.repo / ".broomva" / "asks"))
        self.assertEqual(bp.tree_path("asks", self.repo, "HEAD"), ".control/asks")

    def test_an_option_is_not_a_revision(self) -> None:
        with self.assertRaises(ValueError):
            bp.tree_path("asks", self.repo, "--output=/tmp/x")


class ShellMatchesPython(Tmp):
    """The bash 3.2 reader gives the Python reader's answer on every case."""

    def _states(self):
        # (label, setup) pairs covering every branch of the disk rule.
        def none(r: Path) -> None: pass
        def legacy_root(r: Path) -> None: (r / ".control").mkdir()
        def legacy_entry(r: Path) -> None:
            (r / ".control" / "asks").mkdir(parents=True)
            (r / ".control" / "policy.yaml").write_text("")
        def both(r: Path) -> None:
            legacy_entry(r)
            (r / ".broomva" / "asks").mkdir(parents=True)
            (r / ".broomva" / "policy.yaml").write_text("")
        return [("none", none), ("legacy root", legacy_root), ("legacy entry", legacy_entry), ("both", both)]

    def test_repo_paths_and_rels(self) -> None:
        names = ["policy", "asks", "loops", "hooks", "audit", "arcs"]
        for label, setup in self._states():
            root = self.tmp / label.replace(" ", "-")
            root.mkdir()
            setup(root)
            got = sh(" ".join(f'broomva_repo_path {n} "{root}";' for n in names)).stdout.split("\n")[:-1]
            want = [str(bp.repo_path(n, root)) for n in names]
            self.assertEqual(got, want, label)
        for n in names:
            self.assertEqual(sh(f"broomva_repo_rels {n}").stdout.split(), list(bp.repo_rels(n)), n)
        self.assertEqual(sh(f'broomva_repo_path nope "{self.tmp}"').returncode, 2)

    def test_machine_and_workspace_paths(self) -> None:
        env = {"BROOMVA_HOME": str(self.tmp / "h"), "BROOMVA_WORKSPACE": str(self.tmp / "ws"), "HOME": str(self.tmp)}
        (self.tmp / "maestro").mkdir()
        (self.tmp / "ws" / ".control").mkdir(parents=True)
        with mock.patch.dict(os.environ, env):
            for n in bp.MACHINE:
                self.assertEqual(sh(f"broomva_machine_path {n}", env).stdout.strip(), str(bp.machine_path(n)), n)
            self.assertEqual(sh("broomva_workspace_path asks", env).stdout.strip(), str(bp.workspace_path("asks")))
            self.assertEqual(sh("broomva_machine_root; broomva_workspace_root", env).stdout.split(),
                             [str(bp.machine_root()), str(bp.workspace_root())])

    def test_tree_paths_and_repo_roots(self) -> None:
        repo = make_repo(self.tmp / "r")
        (repo / ".control" / "asks").mkdir(parents=True)
        (repo / ".control" / "asks" / "a.yaml").write_text("x\n")
        git(repo, "add", ".")
        git(repo, "commit", "-qm", "c")
        for n in ("asks", "loops", "policy"):
            out = sh(f'broomva_tree_path {n} "{repo}" HEAD')
            want = bp.tree_path(n, repo, "HEAD")
            self.assertEqual(out.stdout.strip() or None, want, n)
            self.assertEqual(out.returncode, 0 if want else 1, n)
        self.assertEqual(sh(f'broomva_tree_path asks "{repo}" --all').returncode, 2)
        self.assertEqual(sh(f'broomva_repo_root "{repo}/x/y.yaml"').stdout.strip(), str(bp.repo_root(repo / "x" / "y.yaml")))
        self.assertEqual(sh(f'broomva_repo_root "{self.tmp}"').returncode, 1)
        out = sh("broomva_repo_root", {"CLAUDE_PROJECT_DIR": ""})
        self.assertEqual(out.returncode, 1, "no target and no CLAUDE_PROJECT_DIR: no root")


if __name__ == "__main__":
    unittest.main()
