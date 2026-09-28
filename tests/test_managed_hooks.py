"""Tests for scripts/managed_hooks.py (BRO-2542).

Every test lays fixture files (tests/fixtures/managed-hooks/) into a temp HOME, a
temp project and a temp managed-settings directory. The operator's real
~/.claude and /Library or /etc managed settings are never read: HOME is patched,
CLAUDE_CONFIG_DIR is cleared, and --managed-settings always points into the
sandbox. Each rule is tested where it fires and where it does not.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES = REPO_ROOT / "tests" / "fixtures" / "managed-hooks"
sys.path.insert(0, str(REPO_ROOT / "scripts"))
import managed_hooks  # noqa: E402

PLUGIN = "governance@org-market"
TWIN = "governance@community"


class Sandbox(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.td = Path(self._td.name)
        self.home = self.td / "home"
        self.project = self.td / "project"
        self.mdir = self.td / "managed"
        for d in (self.home / ".claude", self.project / ".claude", self.mdir):
            d.mkdir(parents=True)
        self.managed = self.mdir / "managed-settings.json"
        self.managed.write_text("{}")
        env = {k: v for k, v in os.environ.items() if k != "CLAUDE_CONFIG_DIR"}
        env["HOME"] = str(self.home)
        self._env = mock.patch.dict(os.environ, env, clear=True)
        self._env.start()

    def tearDown(self):
        self._env.stop()
        self._td.cleanup()

    # -- fixture placement ---------------------------------------------------
    def place(self, fixture: str, dest: Path) -> Path:
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(FIXTURES / fixture, dest)
        return dest

    def user(self, fixture="user-settings.json", **extra):
        return self._layer(self.home / ".claude" / "settings.json", fixture, extra)

    def project_settings(self, fixture="project-settings.json", **extra):
        return self._layer(self.project / ".claude" / "settings.json", fixture, extra)

    def local(self, fixture="local-settings.json", **extra):
        return self._layer(self.project / ".claude" / "settings.local.json", fixture, extra)

    def managed_settings(self, fixture="managed-settings.json", **extra):
        return self._layer(self.managed, fixture, extra)

    def _layer(self, dest: Path, fixture: str | None, extra: dict) -> Path:
        data = json.loads((FIXTURES / fixture).read_text()) if fixture else {}
        data.update(extra)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(json.dumps(data))
        return dest

    def plugin_hooks(self, name="plugin-hooks.json") -> Path:
        return self.place("plugin-hooks.json", self.td / "plugins" / name)

    # -- running -------------------------------------------------------------
    def run_cli(self, *args: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = managed_hooks.main(["--managed-settings", str(self.managed),
                                     "--project-dir", str(self.project), *args])
        return rc, out.getvalue(), err.getvalue()

    def report(self, *args: str) -> dict:
        rc, out, err = self.run_cli("--json", *args)
        self.assertIn(rc, (0, 1), err)
        return json.loads(out)

    @staticmethod
    def hook(r: dict, needle: str) -> dict:
        hits = [h for h in r["hooks"] if needle in h["command"]]
        assert len(hits) == 1, f"{needle!r} matched {len(hits)} hooks"
        return hits[0]


class TestSurfaces(Sandbox):
    def test_user_hook_runs_today_blocked_under_managed_only(self):
        self.user()
        h = self.hook(self.report(), "greeting.sh")
        self.assertEqual(h["surface"], "user")
        self.assertTrue(h["runs_today"])
        self.assertFalse(h["runs_under_managed_only"])
        self.assertIn("blocks user hooks", h["reason"]["managed_only"])

    def test_project_hook_blocked_under_managed_only(self):
        self.project_settings()
        h = self.hook(self.report(), "conversation-bridge")
        self.assertEqual(h["surface"], "project")
        self.assertTrue(h["runs_today"])
        self.assertFalse(h["runs_under_managed_only"])

    def test_local_hook_blocked_under_managed_only(self):
        self.local()
        h = self.hook(self.report(), "format-on-save")
        self.assertEqual(h["surface"], "local")
        self.assertTrue(h["runs_today"])
        self.assertFalse(h["runs_under_managed_only"])

    def test_managed_hook_runs_in_both_columns(self):
        self.managed_settings()
        h = self.hook(self.report(), "org-audit")
        self.assertEqual(h["surface"], "managed")
        self.assertTrue(h["runs_today"])
        self.assertTrue(h["runs_under_managed_only"])

    def test_actual_allow_managed_hooks_only_blocks_today(self):
        self.managed_settings(allowManagedHooksOnly=True)
        self.user()
        r = self.report()
        self.assertEqual(r["managed_only_mode"], "actual")
        self.assertFalse(self.hook(r, "greeting.sh")["runs_today"])
        self.assertTrue(self.hook(r, "org-audit")["runs_today"])

    def test_claude_config_dir_relocates_user_settings(self):
        alt = self.td / "alt-config"
        self.place("user-settings.json", alt / "settings.json")
        with mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": str(alt)}):
            r = self.report()
        self.assertEqual(self.hook(r, "greeting.sh")["source"], str(alt / "settings.json"))


class TestPlugins(Sandbox):
    def test_force_enabled_exact_id_runs(self):
        self.managed_settings()
        r = self.report("--plugin-hooks", f"{PLUGIN}={self.plugin_hooks()}")
        h = self.hook(r, "test-lock-hook")
        self.assertEqual(h["plugin_id"], PLUGIN)
        self.assertTrue(h["runs_under_managed_only"])
        self.assertIn("force-enabled", h["reason"]["managed_only"])

    def test_same_name_other_marketplace_stays_blocked(self):
        self.managed_settings()
        self.user(fixture=None, enabledPlugins={TWIN: True})
        r = self.report("--plugin-hooks", f"{TWIN}={self.plugin_hooks()}")
        h = self.hook(r, "test-lock-hook")
        self.assertTrue(h["runs_today"])
        self.assertFalse(h["runs_under_managed_only"])
        self.assertIn(PLUGIN, h["reason"]["managed_only"])
        self.assertIn("full plugin@marketplace ID must match", h["reason"]["managed_only"])

    def test_command_sourced_plugin_blocked_unless_explicit_false(self):
        self.managed_settings()
        args = ("--plugin-hooks", f"{PLUGIN}={self.plugin_hooks()}",
                "--plugin-source", f"{PLUGIN}=command")
        h = self.hook(self.report(*args), "test-lock-hook")
        self.assertTrue(h["runs_today"], "command sources only follow the managed-only key")
        self.assertFalse(h["runs_under_managed_only"])
        self.assertIn("command-sourced", h["reason"]["managed_only"])

        self.managed_settings(disableCommandPluginSources=False)
        self.assertTrue(self.hook(self.report(*args), "test-lock-hook")["runs_under_managed_only"])

        self.managed_settings(disableCommandPluginSources=True)
        h = self.hook(self.report(*args), "test-lock-hook")
        self.assertFalse(h["runs_today"])
        self.assertFalse(h["runs_under_managed_only"])

    def test_non_command_source_is_unaffected(self):
        self.managed_settings()
        r = self.report("--plugin-hooks", f"{PLUGIN}={self.plugin_hooks()}",
                        "--plugin-source", f"{PLUGIN}=github")
        self.assertTrue(self.hook(r, "test-lock-hook")["runs_under_managed_only"])

    def test_enabled_plugins_precedence(self):
        # project false beats user true; managed false beats everything.
        self.user(fixture=None, enabledPlugins={TWIN: True})
        self.project_settings(fixture=None, enabledPlugins={TWIN: False})
        args = ("--plugin-hooks", f"{TWIN}={self.plugin_hooks()}")
        h = self.hook(self.report(*args), "test-lock-hook")
        self.assertFalse(h["runs_today"])
        self.assertIn("project", h["reason"]["today"])
        self.project_settings(fixture=None, enabledPlugins={TWIN: True})
        self.managed_settings(fixture=None, enabledPlugins={TWIN: False})
        h = self.hook(self.report(*args), "test-lock-hook")
        self.assertFalse(h["runs_today"])
        self.assertIn("managed", h["reason"]["today"])

    def test_unresolved_plugin_reported_never_guessed(self):
        self.managed_settings()
        self.user(fixture=None, enabledPlugins={"other@somewhere": True, "off@somewhere": False})
        r = self.report()
        ids = {u["plugin_id"]: u for u in r["unresolved"]}
        self.assertEqual(set(ids), {PLUGIN, "other@somewhere"})
        self.assertTrue(ids[PLUGIN]["force_enabled"])
        self.assertEqual(ids["other@somewhere"]["enabled_in"], "user")
        self.assertFalse(any(h["surface"] == "plugin" for h in r["hooks"]))
        _, out, _ = self.run_cli()
        self.assertIn("UNRESOLVED other@somewhere", out)
        # Resolving it removes it from the list.
        r = self.report("--plugin-hooks", f"{PLUGIN}={self.plugin_hooks()}")
        self.assertEqual([u["plugin_id"] for u in r["unresolved"]], ["other@somewhere"])


class TestDisableAllHooks(Sandbox):
    def _all_surfaces(self):
        self.user()
        self.project_settings()
        self.local()
        return ("--plugin-hooks", f"{PLUGIN}={self.plugin_hooks()}")

    def test_in_managed_settings_disables_everything(self):
        args = self._all_surfaces()
        self.managed_settings(disableAllHooks=True)
        r = self.report(*args)
        self.assertTrue(r["hooks"])
        for h in r["hooks"]:
            self.assertFalse(h["runs_today"], h)
            self.assertFalse(h["runs_under_managed_only"], h)
            self.assertIn("including managed ones", h["reason"]["today"])
        self.assertFalse(r["columns"]["today"]["goal_available"])

    def test_in_user_settings_spares_managed_and_force_enabled(self):
        args = self._all_surfaces()
        self.managed_settings()
        self.user(fixture="user-settings.json", disableAllHooks=True,
                  enabledPlugins={"other@somewhere": True})
        other = self.plugin_hooks("other.json")
        r = self.report(*args, "--plugin-hooks", f"other@somewhere={other}")
        today = {(h["surface"], h["plugin_id"] or "", h["command"]): h["runs_today"] for h in r["hooks"]}
        self.assertTrue(self.hook(r, "org-audit")["runs_today"])
        for (surface, pid, _), runs in today.items():
            if surface == "managed" or pid == PLUGIN:
                self.assertTrue(runs, (surface, pid))
            else:
                self.assertFalse(runs, (surface, pid))
        self.assertEqual(r["non_managed_disableAllHooks_from"], "user")
        self.assertFalse(r["columns"]["today"]["goal_available"])

    def test_project_false_overrides_user_true(self):
        self.user(disableAllHooks=True)
        self.project_settings(disableAllHooks=False)
        r = self.report()
        self.assertTrue(self.hook(r, "greeting.sh")["runs_today"])
        self.assertIsNone(r["non_managed_disableAllHooks_from"])
        self.assertTrue(r["columns"]["today"]["goal_available"])


class TestGoalAndDropins(Sandbox):
    def test_goal_available_per_column(self):
        self.user()
        r = self.report()
        self.assertEqual(r["managed_only_mode"], "simulated")
        self.assertTrue(r["columns"]["today"]["goal_available"])
        self.assertFalse(r["columns"]["managed_only"]["goal_available"])
        self.managed_settings(fixture=None, allowManagedHooksOnly=True)
        r = self.report()
        self.assertFalse(r["columns"]["today"]["goal_available"])

    def test_dropins_merge_in_sorted_order(self):
        self.managed_settings(disableCommandPluginSources=True)
        d = self.mdir / "managed-settings.d"
        d.mkdir()
        (d / "20-late.json").write_text(json.dumps({
            "disableCommandPluginSources": False,
            "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "/opt/org/late.sh"}]}]},
        }))
        (d / "10-early.json").write_text(json.dumps({
            "allowManagedHooksOnly": True,
            "disableCommandPluginSources": True,
            "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "/opt/org/early.sh"}]}]},
        }))
        (d / ".hidden.json").write_text(json.dumps({"disableAllHooks": True}))
        (d / "notes.txt").write_text("not json at all {")
        r = self.report()
        self.assertEqual([Path(f).name for f in r["managed"]["files"]],
                         ["managed-settings.json", "10-early.json", "20-late.json"])
        self.assertIs(r["managed"]["disableCommandPluginSources"], False, "later file wins")
        self.assertEqual(r["managed_only_mode"], "actual")
        self.assertIsNot(r["managed"]["disableAllHooks"], True, "hidden files are skipped")
        for needle in ("org-audit", "early.sh", "late.sh"):
            self.assertTrue(self.hook(r, needle)["runs_today"], needle)

    def test_dropins_alone_count_as_managed_settings(self):
        self.managed.unlink()
        d = self.mdir / "managed-settings.d"
        d.mkdir()
        (d / "10-only.json").write_text(json.dumps({"allowManagedHooksOnly": True}))
        r = self.report()
        self.assertEqual(r["managed_only_mode"], "actual")


class TestCriticalAndErrors(Sandbox):
    def test_critical_blocked_lines_and_fail_flag(self):
        self.user()
        self.project_settings()
        rc, out, _ = self.run_cli()
        self.assertEqual(rc, 0, "advisory without --fail-on-critical")
        self.assertIn("CRITICAL BLOCKED user PreToolUse", out)
        self.assertIn("control-gate-hook.sh", out)
        self.assertIn("CRITICAL BLOCKED project Stop", out)
        self.assertNotIn("greeting.sh —", out.split("CRITICAL", 1)[1])
        rc, _, _ = self.run_cli("--fail-on-critical")
        self.assertEqual(rc, 1)
        rc, out, err = self.run_cli("--fail-on-critical", "--json")
        self.assertEqual(rc, 1)
        self.assertEqual(len(json.loads(out)["critical_blocked"]), 3)
        self.assertIn("CRITICAL BLOCKED", err, "JSON mode keeps stdout clean, reports on stderr")

    def test_critical_hook_that_survives_is_not_flagged(self):
        self.managed_settings(fixture=None, hooks={"PreToolUse": [{"matcher": "Bash", "hooks": [
            {"type": "command", "command": "/opt/org/control-gate-hook.sh"}]}]})
        self.user(fixture=None)
        r = self.report("--fail-on-critical")
        self.assertTrue(self.hook(r, "control-gate")["critical"])
        self.assertEqual(r["critical_blocked"], [])
        self.assertEqual(self.run_cli("--fail-on-critical")[0], 0)

    def test_custom_critical_regex_replaces_defaults(self):
        self.user()
        r = self.report("--critical", r"greeting\.sh")
        self.assertEqual([c["command"] for c in r["critical_blocked"]],
                         ['bash "$HOME/.claude/hooks/greeting.sh"'])

    def test_invalid_json_exits_two_and_names_the_file(self):
        bad = self.place("invalid-json.txt", self.project / ".claude" / "settings.json")
        rc, out, err = self.run_cli()
        self.assertEqual(rc, 2)
        self.assertEqual(out, "")
        self.assertIn(str(bad), err)
        self.assertIn("invalid JSON", err)

    def test_invalid_dropin_and_plugin_file_exit_two(self):
        d = self.mdir / "managed-settings.d"
        d.mkdir()
        bad = self.place("invalid-json.txt", d / "30-broken.json")
        rc, _, err = self.run_cli()
        self.assertEqual(rc, 2)
        self.assertIn(str(bad), err)
        bad.unlink()
        broken = self.place("invalid-json.txt", self.td / "plugins" / "hooks.json")
        rc, _, err = self.run_cli("--plugin-hooks", f"{PLUGIN}={broken}")
        self.assertEqual(rc, 2)
        self.assertIn(str(broken), err)

    def test_explicit_missing_managed_path_exits_two(self):
        rc, _, err = self.run_cli("--managed-settings", str(self.td / "nope" / "managed-settings.json"))
        self.assertEqual(rc, 2)
        self.assertIn("not found", err)

    def test_wrong_shape_hooks_exits_two(self):
        self.user(fixture=None, hooks=["not", "an", "object"])
        rc, _, err = self.run_cli()
        self.assertEqual(rc, 2)
        self.assertIn("'hooks' must be an object", err)

    def test_json_path_writes_file_and_keeps_human_stdout(self):
        self.user()
        dest = self.td / "report.json"
        rc, out, err = self.run_cli("--json", str(dest), "--fail-on-critical")
        self.assertEqual(rc, 1, err)
        self.assertIn("CRITICAL BLOCKED user", out)
        self.assertEqual(len(json.loads(dest.read_text())["critical_blocked"]), 1)

    def test_shim_runs_as_a_subprocess(self):
        self.user()
        p = subprocess.run(
            ["bash", str(REPO_ROOT / "bin" / "bstack-managed-hooks"),
             "--managed-settings", str(self.managed), "--project-dir", str(self.project),
             "--fail-on-critical"],
            capture_output=True, text=True, stdin=subprocess.DEVNULL,
        )
        self.assertEqual(p.returncode, 1, p.stderr)
        self.assertIn("CRITICAL BLOCKED user", p.stdout)


if __name__ == "__main__":
    unittest.main()
