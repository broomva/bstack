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
import re
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

    def test_every_name_bstack_reads_has_an_entry(self) -> None:
        # Every `.control/<name>` bstack's own scripts, hooks and templates name must be
        # registered, so a new reader cannot add an unregistered path unnoticed.
        listed = {p for p, legacy, _ in bp.REPO.values() if legacy} | set(bp.REPO_STATE.values())
        pattern = re.compile(r"\.control/([A-Za-z0-9_.-]+)")
        seen = {}
        # Not reader names: a backup migrate.sh writes beside policy.yaml (it moves with it),
        # and the `leverage-` prefix bstack-hook-guard.sh globs over (leverage-*.json).
        not_names = {"policy.yaml.bak", "leverage-"}
        for f in [*(REPO / "scripts").rglob("*"), *(REPO / "hooks").rglob("*"), *(REPO / "assets").rglob("*")]:
            if not f.is_file() or "__pycache__" in f.parts or f.suffix in (".pyc", ".png", ".jpg", ".svg") \
                    or "paths-templates" in f.parts or f.name in ("broomva_paths.py", "broomva-paths.sh"):
                continue
            for name in pattern.findall(f.read_text(errors="ignore")):
                name = name.rstrip(".")
                if name and name not in not_names:
                    seen.setdefault(name, f.relative_to(REPO))
        missing = {n: str(f) for n, f in seen.items() if n not in listed}
        self.assertEqual(missing, {}, "register these in references/paths.toml")
        self.assertGreater(len(seen), 5, "the scan found the readers")

    def test_check_fails_a_stale_or_missing_reader(self) -> None:
        tmp = Path(tempfile.mkdtemp(prefix="gen-check-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        stale_py, stale_sh = tmp / "p.py", tmp / "p.sh"
        stale_py.write_text(gen_paths.PY_OUT.read_text().replace("'policy.yaml'", "'../../etc/passwd'"))
        stale_sh.write_text(gen_paths.SH_OUT.read_text())
        with mock.patch.object(gen_paths, "PY_OUT", stale_py), mock.patch.object(gen_paths, "SH_OUT", stale_sh):
            self.assertEqual(gen_paths.main(["--check"]), 1, "a hand edit that kept the hash line")
        with mock.patch.object(gen_paths, "PY_OUT", tmp / "absent.py"):
            self.assertEqual(gen_paths.main(["--check"]), 1, "a missing reader")

    def test_more_invalid_registries_are_refused(self) -> None:
        base = self.BASE + '[repo]\nx = { path = "p" }\n'
        bad = {
            "boolean version": base.replace("version = 1", "version = true"),
            "mistyped table": base + '[machin]\ny = { path = "q" }\n',
            "stray table": base + '[workspace]\ny = { path = "q" }\n',
            "dot path": self.BASE + '[repo]\nx = { path = "." }\n',
            "dot part": self.BASE + '[repo]\nx = { path = "./asks" }\n',
            "machine unknown key": base.replace('config = { path = "config" }', 'config = { path = "config", owner = true }'),
            "state shares a repo name": base + '[repo_state]\nx = { path = "s" }\n',
            "state shares a repo path": base + '[repo_state]\ns = { path = "p" }\n',
            "missing root": base.replace('[roots.workspace]\nenv = ["BROOMVA_WORKSPACE", "MAESTRO_WORKSPACE", "BSTACK_WORKSPACE"]\ndefault = "~/broomva"\n', ""),
        }
        for why, toml in bad.items():
            with self.subTest(why), self.assertRaises(gen_paths.RegistryError):
                gen_paths.render(toml.encode())

    def test_names_that_are_shell_words_stay_words(self) -> None:
        names = ("esac", "in", "do", "done", "fi", "then")
        toml = self.BASE + "[repo]\n" + "".join(f'{n} = {{ path = "{n}.yaml" }}\n' for n in names)
        _, shell = gen_paths.render(toml.encode())
        lib = Path(tempfile.mkdtemp(prefix="gen-words-")) / "lib.sh"
        self.addCleanup(shutil.rmtree, lib.parent, True)
        lib.write_text(shell)
        out = subprocess.run([BASH, "-c", f'. "{lib}" && for n in {" ".join(names)}; do broomva_repo_rels "$n"; done'],
                             capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(out.stdout.split()[:2], [".broomva/esac.yaml", ".control/esac.yaml"])


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
        names = ["policy", "asks", "loops", "hooks", "audit", "leverage_state", "arcs"]
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


class Hardening(Tmp):
    """Round-1 review (Stratum C): each finding, as a case that fails if it returns."""

    def _repo_with(self, *files: str) -> Path:
        repo = make_repo(self.tmp / "r")
        for f in files:
            (repo / f).parent.mkdir(parents=True, exist_ok=True)
            (repo / f).write_text("x\n")
        git(repo, "add", ".")
        git(repo, "commit", "-qm", "c")
        return repo

    def test_the_generator_refuses_code_in_a_legacy_location(self) -> None:
        for legacy in ("/x'$(id)'", "/x$(id)", "/x`id`", "/x;id", '/x"y', "/x\\ny", "~/a b", "relative/x", "~/../etc", "/", "~/./x"):
            entry = '[machine.w]\npath = "w"\nlegacy = ' + '"' + legacy.replace('"', '\\"') + '"\n'
            with self.subTest(legacy), self.assertRaises(gen_paths.RegistryError):
                gen_paths.render((Generator.BASE + '[repo]\nx = { path = "p" }\n' + entry).encode())

    def test_an_unreadable_tree_is_not_an_absent_one(self) -> None:
        repo = self._repo_with(".control/allowed_signers")
        cases = {"unfetched ref": (repo, "origin/main"), "not a repo": (self.tmp, "HEAD")}
        for why, (root, rev) in cases.items():
            with self.subTest(why):
                with self.assertRaises(bp.TreeReadError):
                    bp.tree_path("allowed_signers", root, rev)
                self.assertEqual(sh(f'broomva_tree_path allowed_signers "{root}" {rev}').returncode, 3)
                self.assertEqual(bp.main(["tree", "allowed_signers", "--root", str(root), "--rev", rev]), 3)
        with mock.patch.dict(os.environ, {"PATH": str(self.tmp / "no-git-here")}):
            with self.assertRaises(bp.TreeReadError):
                bp.tree_path("allowed_signers", repo, "HEAD")
        self.assertEqual(sh(f'PATH=/nonexistent broomva_tree_path allowed_signers "{repo}" HEAD').returncode, 3)
        self.assertIsNone(bp.tree_path("policy", repo, "HEAD"), "readable and absent stays None")
        self.assertEqual(sh(f'broomva_tree_path policy "{repo}" HEAD').returncode, 1)
        self.assertEqual(bp.main(["tree", "asks", "--root", str(repo), "--rev", ""]), 2)
        self.assertEqual(sh(f'broomva_tree_path nope "{repo}" HEAD').returncode, 2, "unknown name: usage, as in Python")

    def test_an_empty_target_or_root_is_never_the_cwd(self) -> None:
        repo = self._repo_with(".control/asks/a.yaml")
        here = os.getcwd()
        self.addCleanup(os.chdir, here)
        os.chdir(repo)
        with mock.patch.dict(os.environ, {"CLAUDE_PROJECT_DIR": ""}):
            self.assertIsNone(bp.repo_root(""))
            for call in (lambda: bp.repo_path("policy", ""), lambda: bp.tree_path("asks", ""), lambda: bp.shadowed("")):
                with self.assertRaises(ValueError):
                    call()
            env = {"CLAUDE_PROJECT_DIR": ""}
            self.assertEqual(subprocess.run([BASH, "-c", f'. "{SH_LIB}"; broomva_repo_root ""'], cwd=repo,
                                            env={**os.environ, **env}, capture_output=True).returncode, 1)
            for cmd in ('broomva_repo_path policy ""', 'broomva_repo_path audit ""', 'broomva_tree_path asks ""',
                        'broomva_tree_path audit ""'):
                self.assertEqual(subprocess.run([BASH, "-c", f'. "{SH_LIB}"; {cmd}'], cwd=repo,
                                                capture_output=True).returncode, 2, cmd)

    def test_git_dir_does_not_redirect_the_root(self) -> None:
        repo = make_repo(self.tmp / "r")
        other = make_repo(self.tmp / "other")
        env = {"GIT_DIR": str(other / ".git"), "GIT_WORK_TREE": str(other)}
        with mock.patch.dict(os.environ, env):
            self.assertEqual(bp.repo_root(repo / "x.yaml"), repo)
        self.assertEqual(sh(f'broomva_repo_root "{repo}/x.yaml"', env).stdout.strip(), str(repo))

    def test_tilde_and_an_unset_home_expand_alike(self) -> None:
        env = {"BROOMVA_HOME": "~/mh", "BROOMVA_WORKSPACE": "~/ws", "HOME": str(self.tmp)}
        with mock.patch.dict(os.environ, env):
            want = [str(bp.machine_root()), str(bp.workspace_root())]
        self.assertEqual(want, [str(self.tmp / "mh"), str(self.tmp / "ws")])
        self.assertEqual(sh("broomva_machine_root; broomva_workspace_root", env).stdout.split(), want)
        clean = {k: v for k, v in os.environ.items() if k not in ("HOME", "BROOMVA_HOME")}
        py = subprocess.run([sys.executable, "-c", f"import sys; sys.path.insert(0, {str(REPO / 'scripts')!r}); "
                             "import broomva_paths as b; print(b.machine_root())"], env=clean, capture_output=True, text=True)
        shell = subprocess.run([BASH, "-c", f'set -u; . "{SH_LIB}"; broomva_machine_root'], env=clean, capture_output=True, text=True)
        self.assertEqual(shell.returncode, 0, shell.stderr)
        self.assertEqual(shell.stdout.strip(), py.stdout.strip())

    def test_the_shell_reader_keeps_to_itself(self) -> None:
        repo = self._repo_with(".control/asks/a.yaml")
        out = sh(f'e=mine; p=mine; rel=mine; _br_e=mine; IFS=/; got=$(broomva_tree_path asks "{repo}" HEAD); rc=$?; '
                 f'IFS=" "; broomva_repo_path policy "{repo}" >/dev/null; echo "$got $rc $e $p $rel $_br_e"')
        self.assertEqual(out.stdout.split(), [".control/asks", "0", "mine", "mine", "mine", "mine"])
        strict = sh(f'set -euo pipefail; broomva_repo_path asks "{repo}"; broomva_tree_path policy "{repo}" HEAD || echo absent-ok')
        self.assertEqual(strict.returncode, 0, strict.stderr)
        self.assertIn("absent-ok", strict.stdout)

    def test_scope_prefixes_agree(self) -> None:
        (self.tmp / ".control").mkdir()
        self.assertEqual(sh(f'broomva_repo_path workspace.policy "{self.tmp}"').stdout.strip(),
                         str(bp.repo_path("workspace.policy", self.tmp)))
        with self.assertRaises(bp.UnknownName):
            bp.resolve("repo.workspace.policy", self.tmp)
        self.assertEqual(sh(f'broomva_repo_path repo.workspace.policy "{self.tmp}"').returncode, 2)
        self.assertTrue(any(n.startswith("workspace.") for n in bp.all_names()))

    def test_a_symlinked_root_folder_and_an_unreadable_root_are_errors(self) -> None:
        (self.tmp / ".control" / "asks").mkdir(parents=True)
        (self.tmp / ".broomva").symlink_to(self.tmp / ".control")  # even a "compatibility" alias
        with self.assertRaises(bp.LayoutError):
            bp.shadowed(self.tmp)
        self.assertEqual(bp.main(["shadowed", "--root", str(self.tmp)]), 3)
        other = self.tmp / "o"
        (other / ".control" / "asks").mkdir(parents=True)
        (other / ".broomva").mkdir()
        (other / ".broomva").chmod(0)
        self.addCleanup((other / ".broomva").chmod, 0o755)
        self.assertEqual(bp.main(["shadowed", "--root", str(other)]), 3)


class Round1More(Tmp):
    """Round-1 review (Strata A and B): the rest of the findings, as failing cases."""

    def test_the_readers_never_search_upward(self) -> None:
        outer = self.tmp / "outer"
        (outer / ".broomva").mkdir(parents=True)
        (outer / ".control").mkdir()
        inner = outer / "inner" / "deep"
        inner.mkdir(parents=True)
        self.assertIsNone(bp.repo_root(inner))
        self.assertEqual(sh(f'broomva_repo_root "{inner}"').returncode, 1)

    def test_every_workspace_env_combination_agrees(self) -> None:
        keys = ("BROOMVA_WORKSPACE", "MAESTRO_WORKSPACE", "BSTACK_WORKSPACE")
        for mask in range(8):
            env = {"HOME": str(self.tmp), **{k: (str(self.tmp / k) if mask & (1 << i) else "") for i, k in enumerate(keys)}}
            with self.subTest(mask), mock.patch.dict(os.environ, env):
                self.assertEqual(sh("broomva_workspace_root", env).stdout.strip(), str(bp.workspace_root()))

    def test_relative_env_roots_are_refused(self) -> None:
        for key, call, shell in (("BROOMVA_HOME", bp.machine_root, "broomva_machine_root"),
                                 ("BROOMVA_WORKSPACE", bp.workspace_root, "broomva_workspace_root")):
            with self.subTest(key), mock.patch.dict(os.environ, {key: "rel/x"}):
                with self.assertRaises(ValueError):
                    call()
                self.assertEqual(sh(shell, {key: "rel/x"}).returncode, 2)
        with mock.patch.dict(os.environ, {"BROOMVA_HOME": "rel/x"}):
            self.assertEqual(bp.main(["resolve", "machine.config"]), 2)

    def test_a_moved_repo_with_a_leftover_control_writes_new(self) -> None:
        (self.tmp / ".broomva").mkdir()
        (self.tmp / ".broomva" / "policy.yaml").write_text("")
        (self.tmp / ".control" / "audit").mkdir(parents=True)  # untracked, survives a pull
        self.assertTrue(bp.moved(self.tmp))
        for name, want in (("preauth", self.tmp / ".broomva" / "preauth.yaml"), ("commands", self.tmp / ".broomva" / "commands.yaml")):
            self.assertEqual(bp.repo_path(name, self.tmp), want)
            self.assertEqual(sh(f'broomva_repo_path {name} "{self.tmp}"').stdout.strip(), str(want))
        self.assertEqual(sh(f'broomva_moved "{self.tmp}"').returncode, 0)
        unmoved = self.tmp / "u"
        (unmoved / ".broomva" / "loops").mkdir(parents=True)  # loops never had a legacy home
        (unmoved / ".control").mkdir()
        self.assertFalse(bp.moved(unmoved))
        self.assertEqual(sh(f'broomva_moved "{unmoved}"').returncode, 1)

    def test_a_repo_at_a_symlinked_home_is_never_a_root(self) -> None:
        real = make_repo(self.tmp / "real-home")
        link = self.tmp / "home-link"
        link.symlink_to(real)
        with mock.patch.dict(os.environ, {"HOME": str(link)}):
            self.assertIsNone(bp.repo_root(real / "x"))
        self.assertEqual(sh(f'broomva_repo_root "{real}/x"', {"HOME": str(link)}).returncode, 1)

    def test_a_trailing_slash_on_the_root_changes_nothing(self) -> None:
        (self.tmp / ".control").mkdir()
        self.assertEqual(sh(f'broomva_repo_path asks "{self.tmp}/"').stdout.strip(), str(bp.repo_path("asks", f"{self.tmp}/")))

    def test_tree_reads_from_a_subfolder_see_the_whole_tree(self) -> None:
        repo = make_repo(self.tmp / "r")
        (repo / ".control" / "asks").mkdir(parents=True)
        (repo / ".control" / "asks" / "a.yaml").write_text("x\n")
        (repo / "sub").mkdir()
        (repo / "sub" / "f").write_text("x\n")
        git(repo, "add", ".")
        git(repo, "commit", "-qm", "c")
        self.assertEqual(bp.tree_path("asks", repo / "sub", "HEAD"), ".control/asks")
        self.assertEqual(sh(f'broomva_tree_path asks "{repo}/sub" HEAD').stdout.strip(), ".control/asks")

    def test_a_tree_that_holds_both_spellings_is_reported(self) -> None:
        repo = make_repo(self.tmp / "r")
        for d in (".control", ".broomva"):
            (repo / d / "asks").mkdir(parents=True)
            (repo / d / "asks" / "a.yaml").write_text("x\n")
        (repo / ".control" / "policy.yaml").write_text("x\n")
        git(repo, "add", ".")
        git(repo, "commit", "-qm", "both")
        self.assertEqual(bp.shadowed(repo, "HEAD"), ["asks"])
        self.assertEqual(bp.main(["shadowed", "--root", str(repo), "--rev", "HEAD"]), 1)
        self.assertEqual(bp.main(["shadowed", "--root", str(repo), "--rev", "no-such-ref"]), 3)

    def test_runtime_state_moves_to_the_machine_root(self) -> None:
        repo = self.tmp / "myrepo"
        (repo / ".control" / "audit").mkdir(parents=True)
        home = self.tmp / "mh"
        env = {"BROOMVA_HOME": str(home)}
        with mock.patch.dict(os.environ, env):
            self.assertEqual(bp.repo_path("audit", repo), repo / ".control" / "audit", "unmoved: legacy")
            self.assertEqual(bp.repo_path("repo.leverage_state", repo), repo / ".control" / "leverage-state.json",
                             "unmoved, no file yet: legacy (a writer keeps one place)")
            self.assertEqual(bp.state_dir(repo).parent, home / "state" / "repos")
            (bp.state_dir(repo) / "audit").mkdir(parents=True)
            self.assertEqual(bp.repo_path("audit", repo), bp.state_dir(repo) / "audit", "moved state: new")
            self.assertIn("audit", bp.shadowed(repo))
            self.assertEqual(bp.repo_rels("audit"), (".control/audit",))
            for name in ("audit", "leverage_state"):
                self.assertEqual(sh(f'broomva_repo_path {name} "{repo}"', env).stdout.strip(), str(bp.repo_path(name, repo)))
            self.assertEqual(sh("broomva_repo_rels audit").stdout.split(), [".control/audit"])
            gone = self.tmp / "gone"
            gone.mkdir()
            self.assertEqual(bp.repo_path("leverage_state", gone), bp.state_dir(gone) / "leverage-state.json")
        self.assertIn("repo.audit", bp.all_names())


class Round2(Tmp):
    """Round-2 review (Strata A, B, C): each finding, as a case that fails if it returns."""

    def test_two_repos_with_one_name_keep_separate_state(self) -> None:
        a, b = self.tmp / "x" / "checkout", self.tmp / "y" / "checkout"
        a.mkdir(parents=True)
        b.mkdir(parents=True)
        env = {"BROOMVA_HOME": str(self.tmp / "mh")}
        with mock.patch.dict(os.environ, env):
            da, db = bp.state_dir(a), bp.state_dir(b)
            self.assertNotEqual(da, db)
            self.assertTrue(da.name.startswith("checkout-") and len(da.name) == len("checkout-") + 12)
            for root, want in ((a, da), (b, db), (Path(str(a) + "/."), da)):
                self.assertEqual(sh(f'broomva_state_dir "{root}"', env).stdout.strip(), str(want), str(root))
            self.assertEqual(bp.state_dir(Path(str(a) + "/.")), da, "a trailing /. is the same repo")
            link = self.tmp / "link-to-a"
            link.symlink_to(a)
            self.assertEqual(bp.state_dir(link), da, "the same repo through a symlink: the real path decides")
            self.assertEqual(sh(f'broomva_state_dir "{link}"', env).stdout.strip(), str(da))
            self.assertTrue(bp.repo_key("/").startswith("_root-"))
            # Round 3: a relative root under CDPATH, a root named like an option, a missing root.
            elsewhere = self.tmp / "cd-elsewhere"
            (elsewhere / "checkout").mkdir(parents=True)
            out = subprocess.run([BASH, "-c", f'. "{SH_LIB}"; CDPATH="{elsewhere}"; export CDPATH; broomva_repo_key checkout'],
                                 cwd=a.parent, capture_output=True, text=True, env={**os.environ, **env})
            here = os.getcwd()
            os.chdir(a.parent)
            try:
                self.assertEqual(out.stdout.strip(), bp.repo_key("checkout"), "CDPATH must not pick the folder")
            finally:
                os.chdir(here)
            dash = self.tmp / "-r"
            dash.mkdir()
            out = subprocess.run([BASH, "-c", f'. "{SH_LIB}"; broomva_repo_key -r'], cwd=self.tmp, capture_output=True, text=True)
            os.chdir(self.tmp)
            try:
                self.assertEqual(out.stdout.strip(), bp.repo_key("-r"))
            finally:
                os.chdir(here)
            # Round 4 (C): a workspace not cloned yet keys as it will once it exists.
            absent = self.tmp / "later" / "ws"
            before = bp.repo_key(absent)
            self.assertEqual(sh(f'broomva_repo_key "{absent}"').stdout.strip(), before)
            absent.mkdir(parents=True)
            self.assertEqual(bp.repo_key(absent), before)
            self.assertEqual(sh(f'broomva_repo_key "{absent}"').stdout.strip(), before)
            for bad in (f"{a}/..", f"{a}/../checkout"):
                with self.assertRaises(ValueError):
                    bp.repo_key(bad)
                self.assertEqual(sh(f'broomva_repo_key "{bad}"').returncode, 2)
            self.assertEqual(sh('broomva_repo_key /').stdout.strip(), bp.repo_key("/"))

    def test_tilde_rules_are_one_rule(self) -> None:
        for value in ("~root", "~nobody/x"):
            with self.subTest(value), mock.patch.dict(os.environ, {"BROOMVA_HOME": value}):
                with self.assertRaises(ValueError):
                    bp.machine_root()
                self.assertEqual(sh("broomva_machine_root", {"BROOMVA_HOME": value}).returncode, 2)
        env = {"HOME": "", "BROOMVA_HOME": "~/reg"}
        with mock.patch.dict(os.environ, env):
            want = str(bp.machine_root())
        self.assertFalse(want.startswith("/reg"), "an empty HOME is the account's home, not /")
        self.assertEqual(sh("broomva_machine_root", env).stdout.strip(), want)
        env = {"BROOMVA_HOME": f"{self.tmp}/h///", "BROOMVA_WORKSPACE": f"{self.tmp}/ws/"}
        with mock.patch.dict(os.environ, env):
            want = [str(bp.machine_root()), str(bp.workspace_root()), str(bp.machine_path("config"))]
        self.assertEqual(sh("broomva_machine_root; broomva_workspace_root; broomva_machine_path config", env).stdout.split(), want)
        self.assertEqual(want[0], f"{self.tmp}/h")

    def test_the_workspace_root_is_never_home(self) -> None:
        for value in ("~", str(self.tmp)):
            env = {"HOME": str(self.tmp), "BROOMVA_WORKSPACE": value}
            with self.subTest(value), mock.patch.dict(os.environ, env):
                with self.assertRaises(ValueError):
                    bp.workspace_root()
                self.assertEqual(sh("broomva_workspace_root", env).returncode, 2)
                self.assertEqual(sh("broomva_workspace_path policy", env).returncode, 2)

    def test_containment_not_provenance(self) -> None:
        # Round 3: a symlinked .broomva/ (or entry) would send reads and writes outside the
        # repo: refused, in both readers. A plain copy is not something a reader can judge
        # (`cp -r ../evil .broomva`): it reads, and doctor's shadow gap names it.
        evil = self.tmp / "evil"
        evil.mkdir()
        (evil / "policy.yaml").write_text("agent: true\n")
        (evil / "asks").mkdir()
        linked = self.tmp / "linked"
        (linked / ".control").mkdir(parents=True)
        (linked / ".control" / "policy.yaml").write_text("owner: true\n")
        (linked / ".broomva").symlink_to(evil)
        for name in ("policy", "decisions", "asks"):  # owner, a ledger with no file yet (C's prediction), any
            with self.subTest(name):
                with self.assertRaises(bp.LayoutError):
                    bp.repo_path(name, linked)
                self.assertEqual(sh(f'broomva_repo_path {name} "{linked}"').returncode, 3)
        with self.assertRaises(bp.LayoutError):
            bp.moved(linked)
        self.assertEqual(sh(f'broomva_moved "{linked}"').returncode, 3)
        leaf = self.tmp / "leaf"
        (leaf / ".broomva").mkdir(parents=True)
        (leaf / ".broomva" / "asks").symlink_to(evil / "asks")
        with self.assertRaises(bp.LayoutError):
            bp.repo_path("asks", leaf)
        self.assertEqual(sh(f'broomva_repo_path asks "{leaf}"').returncode, 3)
        copied = self.tmp / "copied"
        (copied / ".control").mkdir(parents=True)
        (copied / ".control" / "policy.yaml").write_text("owner: true\n")
        shutil.copytree(evil, copied / ".broomva")
        self.assertEqual(bp.repo_path("policy", copied), copied / ".broomva" / "policy.yaml")
        self.assertIn("policy", bp.shadowed(copied), "doctor's shadow gap names it")
        self.assertNotIn("UnsafePath", dir(bp))

    def test_a_committed_symlink_is_refused_in_a_tree(self) -> None:
        repo = make_repo(self.tmp / "r")
        (repo / ".control").mkdir()
        (repo / ".control" / "policy.yaml").symlink_to("/etc/hosts")
        git(repo, "add", ".")
        git(repo, "commit", "-qm", "link")
        with self.assertRaises(bp.LayoutError):
            bp.tree_path("policy", repo, "HEAD")
        self.assertEqual(sh(f'broomva_tree_path policy "{repo}" HEAD').returncode, 3)

    def test_a_control_file_and_a_symlinked_folder_find_the_same_root(self) -> None:
        repo = make_repo(self.tmp / "r")
        (repo / ".control").write_text("not a folder\n")
        self.assertEqual(bp.repo_root(repo / ".control" / "policy.yaml"), repo)
        self.assertEqual(sh(f'broomva_repo_root "{repo}/.control/policy.yaml"').stdout.strip(), str(repo))
        outside = self.tmp / "outside"
        outside.mkdir()
        (repo / "link").symlink_to(outside)
        self.assertEqual(bp.repo_root(repo / "link" / "x.yaml"), repo, "a symlinked folder does not take git out")
        self.assertEqual(sh(f'broomva_repo_root "{repo}/link/x.yaml"').stdout.strip(), str(repo))

    def test_an_explicit_empty_revision_is_a_usage_error_in_both(self) -> None:
        repo = make_repo(self.tmp / "r")
        (repo / "f").write_text("x\n")
        git(repo, "add", ".")
        git(repo, "commit", "-qm", "c")
        self.assertEqual(sh(f'broomva_tree_path asks "{repo}" ""').returncode, 2)
        self.assertEqual(sh(f'broomva_tree_path asks "{repo}"').returncode, 1, "an absent REV is HEAD")

    def test_an_unreadable_new_root_is_unreadable_in_both(self) -> None:
        root = self.tmp / "u"
        (root / ".control" / "asks").mkdir(parents=True)
        (root / ".broomva").mkdir()
        (root / ".broomva").chmod(0)
        self.addCleanup((root / ".broomva").chmod, 0o755)
        with self.assertRaises(OSError):
            bp.repo_path("preauth", root)
        self.assertEqual(sh(f'broomva_repo_path preauth "{root}"').returncode, 3)
        self.assertEqual(sh(f'broomva_moved "{root}"').returncode, 3)
        lone = self.tmp / "lone"  # no .control: only the unreadable-folder check can tell
        (lone / ".broomva").mkdir(parents=True)
        (lone / ".broomva").chmod(0)
        self.addCleanup((lone / ".broomva").chmod, 0o755)
        with self.assertRaises(OSError):
            bp.repo_path("asks", lone)
        self.assertEqual(sh(f'broomva_repo_path asks "{lone}"').returncode, 3)

    def test_no_git_is_an_error_not_a_missing_repo(self) -> None:
        repo = make_repo(self.tmp / "r")
        with mock.patch.dict(os.environ, {"PATH": str(self.tmp / "nowhere")}):
            with self.assertRaises(bp.TreeReadError):
                bp.repo_root(repo / "x")
            self.assertEqual(bp.main(["resolve", "repo.policy", "--root", str(repo)]), 3)
        self.assertEqual(sh(f'PATH=/nonexistent broomva_repo_root "{repo}/x"').returncode, 3)
        # Without the external dirname the walk up must still end (it hung, round 3).
        out = subprocess.run([BASH, "-c", f'. "{SH_LIB}"; command() {{ return 0; }}; PATH=/nonexistent; '
                              f'broomva_repo_root "{repo}/a/b/c/x"'], capture_output=True, text=True, timeout=20)
        self.assertIn(out.returncode, (1, 3))
        for path, want in (("/a/b", "/a"), ("/a", "/"), ("/", "/"), ("a", "."), ("a/b/", "a"), ("/a//", "/")):
            self.assertEqual(sh(f'_broomva_dirname "{path}"').stdout.strip(), want, path)

    def test_the_generator_refuses_nesting_case_duplicates_and_bad_toml(self) -> None:
        base = Generator.BASE + '[repo]\nx = { path = "a" }\n'
        bad = {
            "repo nests repo": base + 'y = { path = "a/b" }\n',
            "state nests in repo": base + '[repo_state]\ns = { path = "a/b" }\n',
            "case duplicate": base + 'y = { path = "A" }\n',
            "machine case duplicate": base.replace('config = { path = "config" }', 'config = { path = "config" }\nConfig2 = { path = "Config" }'),
            "invalid toml": base + "y = {\n",
        }
        for why, toml in bad.items():
            with self.subTest(why), self.assertRaises(gen_paths.RegistryError):
                gen_paths.render(toml.encode())
        gen_paths.render((Generator.BASE + '[repo]\nx = { path = "a" }\n').replace(
            'config = { path = "config" }', 'ledger = { path = "ledger" }\nticks = { path = "ledger/t.jsonl" }').encode())

    def test_a_partial_move_is_named(self) -> None:
        root = self.tmp / "p"
        (root / ".broomva").mkdir(parents=True)
        (root / ".broomva" / "policy.yaml").write_text("")
        (root / ".control" / "asks").mkdir(parents=True)
        (root / ".control" / "audit").mkdir()  # runtime state: not a split
        self.assertEqual(bp.partially_moved(root), ["asks"])
        self.assertEqual(bp.main(["split", "--root", str(root)]), 1)
        unmoved = self.tmp / "q"
        (unmoved / ".control" / "asks").mkdir(parents=True)
        self.assertEqual(bp.partially_moved(unmoved), [])
        alias = self.tmp / "alias"
        (alias / ".control" / "asks").mkdir(parents=True)
        (alias / ".broomva").symlink_to(alias / ".control")
        with self.assertRaises(bp.LayoutError):
            bp.partially_moved(alias)
        self.assertEqual(bp.main(["split", "--root", str(alias)]), 3)

    def test_check_refuses_a_root_that_does_not_exist(self) -> None:
        self.assertEqual(bp.main(["check", "--root", str(self.tmp / "absent")]), 3)

    def test_the_machine_root_stray_list_covers_every_repo_name(self) -> None:
        tops = {p.split("/")[0] for p, _, _ in bp.REPO.values()}
        machine_tops = {p.split("/")[0].lower() for p, _ in bp.MACHINE.values()}
        self.assertEqual(set(bp.REPO_NAMES_NEVER_IN_MACHINE_ROOT), {t for t in tops if t.lower() not in machine_tops})
        self.assertIn("allowed_signers", bp.REPO_NAMES_NEVER_IN_MACHINE_ROOT)


class Round3(Tmp):
    """Round-3 review (Strata A, B, C): the rest of the findings, as failing cases."""

    def _bash5(self) -> str:
        for candidate in ("/opt/homebrew/bin/bash", "/usr/local/bin/bash", "/usr/bin/bash", "/bin/bash"):
            if Path(candidate).exists():
                out = subprocess.run([candidate, "-c", "echo ${BASH_VERSINFO[0]}"], capture_output=True, text=True)
                if out.stdout.strip() >= "4":
                    return candidate
        return BASH  # bash 3.2 only: the case still runs, on 3.2

    def test_an_empty_home_resolves_on_every_bash(self) -> None:
        env = {**os.environ, "HOME": "", "BROOMVA_HOME": "~/reg"}
        with mock.patch.dict(os.environ, {"HOME": "", "BROOMVA_HOME": "~/reg"}):
            want = [str(bp.machine_root())]
        with mock.patch.dict(os.environ, {"HOME": ""}):
            os.environ.pop("BROOMVA_HOME", None)
            want_default = str(bp.machine_root())
        for shell in {BASH, self._bash5()}:
            with self.subTest(shell):
                out = subprocess.run([shell, "-c", f'. "{SH_LIB}"; broomva_machine_root'], env=env, capture_output=True, text=True)
                self.assertEqual(out.stdout.split(), want, out.stderr)
                env2 = {k: v for k, v in env.items() if k != "BROOMVA_HOME"}
                out = subprocess.run([shell, "-c", f'. "{SH_LIB}"; broomva_machine_root'], env=env2, capture_output=True, text=True)
                self.assertEqual(out.stdout.strip(), want_default)

    def test_a_relative_home_is_refused(self) -> None:
        with mock.patch.dict(os.environ, {"HOME": "rel", "BROOMVA_HOME": ""}):
            with self.assertRaises(ValueError):
                bp.machine_root()
        self.assertEqual(sh("broomva_machine_root", {"HOME": "rel", "BROOMVA_HOME": ""}).returncode, 2)

    def test_home_spelled_alike_counts_even_before_it_exists(self) -> None:
        home = self.tmp / "no-such-home"
        for ws in (str(home), str(home) + "/", str(home) + "/."):
            env = {"HOME": str(home), "BROOMVA_WORKSPACE": ws}
            with self.subTest(ws), mock.patch.dict(os.environ, env):
                with self.assertRaises(ValueError):
                    bp.workspace_root()
                self.assertEqual(sh("broomva_workspace_root", env).returncode, 2)

    def test_trailing_slashes_on_home_itself(self) -> None:
        env = {"HOME": f"{self.tmp}///", "BROOMVA_HOME": "", "BROOMVA_WORKSPACE": "", "MAESTRO_WORKSPACE": "", "BSTACK_WORKSPACE": ""}
        with mock.patch.dict(os.environ, env):
            want = [str(bp.machine_root()), str(bp.workspace_root())]
        self.assertEqual(sh("broomva_machine_root; broomva_workspace_root", env).stdout.split(), want)

    def test_a_table_of_the_wrong_kind_is_a_registry_error(self) -> None:
        base = Generator.BASE + '[repo]\nx = { path = "p" }\n'
        for bad in (base.replace("[machine]\nconfig", "machine = []\n[unused]\nconfig"), "repo = []\n" + Generator.BASE):
            with self.subTest(bad[:20]), self.assertRaises(gen_paths.RegistryError):
                gen_paths.render(bad.encode())

    def test_an_unreadable_legacy_root_is_unreadable_in_both(self) -> None:
        root = self.tmp / "u"
        (root / ".control").mkdir(parents=True)
        (root / ".control").chmod(0)
        self.addCleanup((root / ".control").chmod, 0o755)
        with self.assertRaises(OSError):
            bp.repo_path("asks", root)
        self.assertEqual(sh(f'broomva_repo_path asks "{root}"').returncode, 3)

    def test_no_hash_tool_is_unreadable_not_usage(self) -> None:
        (self.tmp / "r").mkdir()
        out = subprocess.run([BASH, "-c", f'. "{SH_LIB}"; command() {{ return 1; }}; broomva_repo_path audit "{self.tmp}/r"'],
                             capture_output=True, text=True)
        self.assertEqual(out.returncode, 3)


class Round4(Tmp):
    """Round-4 review (Stratum A): each finding, as a case that fails if it returns."""

    def test_a_hard_linked_owner_file_is_refused(self) -> None:
        repo = self.tmp / "r"
        (repo / ".broomva").mkdir(parents=True)
        (repo / ".broomva" / "policy.yaml").write_text("owner\n")
        os.link(repo / ".broomva" / "policy.yaml", repo / "not-governed.yaml")
        with self.assertRaises(bp.LayoutError):
            bp.repo_path("policy", repo)
        self.assertEqual(sh(f'broomva_repo_path policy "{repo}"').returncode, 3)
        (repo / ".broomva" / "arcs.yaml").write_text("x\n")
        os.link(repo / ".broomva" / "arcs.yaml", repo / "arcs-alias.yaml")
        self.assertEqual(bp.repo_path("arcs", repo), repo / ".broomva" / "arcs.yaml", "only owner files")
        self.assertEqual(sh(f'broomva_repo_path arcs "{repo}"').returncode, 0)
        (repo / ".broomva" / "hooks").mkdir()
        self.assertEqual(bp.repo_path("hooks", repo), repo / ".broomva" / "hooks", "a folder has links by nature")
        self.assertEqual(sh(f'broomva_repo_path hooks "{repo}"').returncode, 0)

    def test_a_committed_symlinked_root_is_refused_not_absent(self) -> None:
        repo = make_repo(self.tmp / "r")
        (self.tmp / "elsewhere").mkdir()
        (self.tmp / "elsewhere" / "policy.yaml").write_text("x\n")
        (repo / ".broomva").symlink_to(self.tmp / "elsewhere")
        (repo / "f").write_text("x\n")
        git(repo, "add", ".")
        git(repo, "commit", "-qm", "link root")
        for call in (lambda: bp.tree_path("policy", repo, "HEAD"), lambda: bp.shadowed(repo, "HEAD")):
            with self.assertRaises(bp.LayoutError):
                call()
        self.assertEqual(sh(f'broomva_tree_path policy "{repo}" HEAD').returncode, 3)

    def test_a_single_nested_repo_path_is_refused(self) -> None:
        toml = Generator.BASE + '[repo]\npolicy = { path = "governance/policy.yaml", owner = true }\n'
        with self.assertRaises(gen_paths.RegistryError):
            gen_paths.render(toml.encode())
        with self.assertRaises(gen_paths.RegistryError):
            gen_paths.render((Generator.BASE + '[repo]\nx = { path = "p" }\n[repo_state]\ns = { path = "a/b" }\n').encode())

    def test_dotdot_aliases_of_home_and_roots_agree(self) -> None:
        home = self.tmp / "home"
        env = {"HOME": str(home), "BROOMVA_WORKSPACE": str(self.tmp / "other" / ".." / "home")}
        with mock.patch.dict(os.environ, env):
            with self.assertRaises(ValueError):
                bp.workspace_root()
        self.assertEqual(sh("broomva_workspace_root", env).returncode, 2)
        env = {"HOME": str(home), "BROOMVA_HOME": f"{self.tmp}/a/../mh//x/.", "BROOMVA_WORKSPACE": f"{self.tmp}/w/./s/.."}
        with mock.patch.dict(os.environ, env):
            want = [str(bp.machine_root()), str(bp.workspace_root())]
        self.assertEqual(want, [f"{self.tmp}/mh/x", f"{self.tmp}/w"])
        self.assertEqual(sh("broomva_machine_root; broomva_workspace_root", env).stdout.split(), want)
        for path, want in (("/a/../b", "/b"), ("/..", "/"), ("/a//b/./c/", "/a/b/c"), ("/", "/")):
            self.assertEqual(sh(f'_broomva_normpath "{path}"').stdout.strip(), want, path)
        self.assertEqual(sh('_broomva_normpath rel').returncode, 2)

    def test_a_relative_home_is_a_usage_error_not_no_repo(self) -> None:
        repo = make_repo(self.tmp / "r")
        self.assertEqual(sh(f'broomva_repo_root "{repo}"', {"HOME": "rel"}).returncode, 2)
        with mock.patch.dict(os.environ, {"HOME": "rel"}):
            self.assertEqual(bp.main(["resolve", "repo.asks", "--root", str(repo)]), 2)

    def test_moved_refuses_a_symlinked_entry(self) -> None:
        repo = self.tmp / "r"
        (repo / ".broomva").mkdir(parents=True)
        (self.tmp / "evil").write_text("x")
        (repo / ".broomva" / "policy.yaml").symlink_to(self.tmp / "evil")
        with self.assertRaises(bp.LayoutError):
            bp.moved(repo)
        self.assertEqual(sh(f'broomva_moved "{repo}"').returncode, 3)


class Round4B(Tmp):
    """Round-4 review (Stratum B): each finding, as a case that fails if it returns."""

    def _moved_repo(self) -> Path:
        repo = self.tmp / "r"
        (repo / ".broomva" / "asks").mkdir(parents=True)  # moved
        return repo

    def test_a_dangling_symlink_is_never_returned(self) -> None:
        repo = self._moved_repo()
        (repo / ".broomva" / "decisions.jsonl").symlink_to(self.tmp / "evil" / "decisions.jsonl")
        (repo / ".broomva" / "preauth.yaml").symlink_to(self.tmp / "evil" / "preauth.yaml")
        for name in ("decisions", "preauth"):
            with self.subTest(name):
                with self.assertRaises(bp.LayoutError):
                    bp.repo_path(name, repo)
                self.assertEqual(sh(f'broomva_repo_path {name} "{repo}"').returncode, 3)
        env = {"BROOMVA_HOME": str(self.tmp / "mh")}
        with mock.patch.dict(os.environ, env):
            state = bp.state_dir(repo)
            state.mkdir(parents=True)
            (state / "leverage-state.json").symlink_to(self.tmp / "evil" / "s.json")
            with self.assertRaises(bp.LayoutError):
                bp.repo_path("leverage_state", repo)
        self.assertEqual(sh(f'broomva_repo_path leverage_state "{repo}"', env).returncode, 3)
        self.assertFalse((self.tmp / "evil").exists(), "nothing was written outside the repo")

    def test_a_symlink_under_an_unsearchable_root_is_an_error_on_every_python(self) -> None:
        # B's located prediction: 3.12+ Path.is_symlink() answers False when lstat is denied.
        repo = self.tmp / "r"
        (repo / ".broomva").mkdir(parents=True)
        (repo / ".broomva" / "decisions.jsonl").symlink_to(self.tmp / "evil")
        (repo / ".broomva").chmod(0)
        self.addCleanup((repo / ".broomva").chmod, 0o755)
        with self.assertRaises(OSError):
            bp.repo_path("decisions", repo)
        self.assertEqual(sh(f'broomva_repo_path decisions "{repo}"').returncode, 3)

    def test_a_legacy_entry_symlink_is_refused_in_both(self) -> None:
        repo = self.tmp / "r"
        (repo / ".control").mkdir(parents=True)
        (repo / ".control" / "arcs.yaml").symlink_to(self.tmp / "evil")
        with self.assertRaises(bp.LayoutError):
            bp.repo_path("arcs", repo)
        self.assertEqual(sh(f'broomva_repo_path arcs "{repo}"').returncode, 3)

    def test_a_root_folder_that_is_a_file_is_refused_in_both(self) -> None:
        repo = self.tmp / "r"
        repo.mkdir()
        (repo / ".broomva").write_text("not a folder\n")
        with self.assertRaises(bp.LayoutError):
            bp.repo_path("asks", repo)
        self.assertEqual(sh(f'broomva_repo_path asks "{repo}"').returncode, 3)

    def test_an_unreadable_legacy_root_is_an_error_even_when_the_name_is_new(self) -> None:
        repo = self._moved_repo()
        (repo / ".control").mkdir()
        (repo / ".control").chmod(0)
        self.addCleanup((repo / ".control").chmod, 0o755)
        with self.assertRaises(OSError):
            bp.repo_path("asks", repo)
        self.assertEqual(sh(f'broomva_repo_path asks "{repo}"').returncode, 3)

    def test_the_account_name_guard_keeps_eval_inert(self) -> None:
        marker = self.tmp / "pwned"
        out = subprocess.run([BASH, "-c", f'. "{SH_LIB}"; id() {{ printf "%s\\n" "x;touch {marker}"; }}; HOME=; broomva_machine_root'],
                             capture_output=True, text=True)
        self.assertEqual(out.returncode, 2)
        self.assertFalse(marker.exists(), "the account name never reached eval")

    def test_a_home_lookup_that_fails_is_a_usage_error(self) -> None:
        with mock.patch.dict(os.environ, {"HOME": "", "BROOMVA_HOME": ""}), \
                mock.patch.object(bp.pwd, "getpwuid", side_effect=KeyError("no entry")):
            with self.assertRaises(ValueError):
                bp.machine_root()

    def test_a_bad_home_is_a_usage_error_for_legacy_machine_paths_too(self) -> None:
        self.assertEqual(sh("broomva_machine_path work", {"HOME": "rel", "BROOMVA_HOME": str(self.tmp / "mh")}).returncode, 2)
        with mock.patch.dict(os.environ, {"HOME": "rel", "BROOMVA_HOME": str(self.tmp / "mh")}):
            with self.assertRaises(ValueError):
                bp.machine_path("work")


class Round4C(Tmp):
    """Round-4 review (Stratum C): each finding, as a case that fails if it returns."""

    def test_workspace_state_names_resolve_before_the_workspace_exists(self) -> None:
        # C's located prediction: workspace.audit on a fresh machine must resolve, as workspace.asks does.
        env = {"HOME": str(self.tmp), "BROOMVA_WORKSPACE": str(self.tmp / "not-cloned"), "BROOMVA_HOME": str(self.tmp / "mh")}
        with mock.patch.dict(os.environ, env):
            for name in ("workspace.audit", "workspace.leverage_state", "workspace.asks"):
                with self.subTest(name):
                    self.assertEqual(bp.main(["resolve", name]), 0)
            want = str(bp.workspace_path("audit"))
        self.assertEqual(sh("broomva_workspace_path audit", env).stdout.strip(), want)

    def test_a_root_with_dotdot_is_refused(self) -> None:
        repo = self.tmp / "r"
        (repo / ".control").mkdir(parents=True)
        (self.tmp / "lnk").symlink_to(self.tmp / "elsewhere")
        bad = f"{self.tmp}/lnk/.."
        for call in (lambda: bp.repo_path("policy", bad), lambda: bp.moved(bad), lambda: bp.tree_path("asks", bad)):
            with self.assertRaises(ValueError):
                call()
        for cmd in (f'broomva_repo_path policy "{bad}"', f'broomva_moved "{bad}"', f'broomva_tree_path asks "{bad}" HEAD'):
            self.assertEqual(sh(cmd).returncode, 2, cmd)

    def test_numeric_or_dash_account_names_never_reach_eval(self) -> None:
        for name in ("0", "1", "-", "+1"):
            out = subprocess.run([BASH, "-c", f'. "{SH_LIB}"; id() {{ printf "%s\\n" "{name}"; }}; HOME=; broomva_machine_root'],
                                 capture_output=True, text=True, cwd=self.tmp)
            with self.subTest(name):
                self.assertEqual(out.returncode, 2, out.stdout)

    def test_a_tree_holds_only_folders_and_files_at_registry_paths(self) -> None:
        repo = make_repo(self.tmp / "r")
        (repo / ".control").mkdir()
        (repo / ".control" / "x").write_text("x\n")
        git(repo, "add", ".")
        git(repo, "commit", "-qm", "c")
        sha = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
        git(repo, "update-index", "--add", "--cacheinfo", f"160000,{sha},.control/asks")
        git(repo, "commit", "-qm", "gitlink")
        with self.assertRaises(bp.LayoutError):
            bp.tree_path("asks", repo, "HEAD")
        self.assertEqual(sh(f'broomva_tree_path asks "{repo}" HEAD').returncode, 3)


if __name__ == "__main__":
    unittest.main()
