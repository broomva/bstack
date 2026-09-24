"""Tests for scripts/bands.py (BRO-2542) — stdlib unittest, no network.

Every rule the detector states is a rule some test can turn red. Each Western
Electric rule has a firing case AND a near-miss (the case one step short of
firing), because a rule tested only where it fires cannot tell a correct
threshold from a loose one.

Series convention: BASE = [9, 11] * 10 gives mean 10 and sample std
sqrt(20/19) = 1.026, so 1σ ≈ 11.03, 2σ ≈ 12.05, 3σ ≈ 13.08.
"""

from __future__ import annotations

import contextlib
import copy
import datetime as dt
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))
import bands  # noqa: E402

FIX = REPO / "tests" / "fixtures" / "bands"
TEMPLATE = REPO / "references" / "templates" / "bands.example.yaml"
BASE = [9, 11] * 10
START = dt.date(2026, 9, 1)

CFG = {
    "metric": "ci_test_failure_rate",
    "description": "share of completed CI runs that failed, per day",
    "baseline": "rolling_30d",
    "min_baseline_points": 10,
    "rules": "western_electric",
    "direction": "high",
    "tiers": {
        "1sigma": {"action": "log"},
        "2sigma": {"action": "diagnose", "tools": "Read,Grep,Glob"},
        "3sigma": {"action": "propose", "routes": ["pull_request", "runbook:rollback-deploy"]},
    },
    "intent": {"affected": "CI pipeline and the test suites it runs"},
}

# Evaluation windows (8 points each) that fire exactly one rule on the high side.
R1_ONLY = [10, 9.5, 10, 10.5, 9.5, 10, 9.5, 14]
R2_ONLY = [10, 9.5, 10, 10.5, 9.5, 12.5, 10, 12.5]
R3_ONLY = [10, 9.5, 10, 11.5, 11.5, 10, 11.5, 11.5]
R4_ONLY = [10.5] * 8
QUIET = [10, 10.5, 9.5, 10, 10.5, 9.5, 10, 10.5]


def cfg(**over) -> dict:
    c = copy.deepcopy(CFG)
    c.update(over)
    return c


def daily(values, start=START, step=1) -> list[dict]:
    return [{"t": (start + dt.timedelta(days=i * step)).isoformat(), "v": v}
            for i, v in enumerate(values)]


def run(values, config=None, **over) -> dict:
    """Evaluate BASE + `values` (or an explicit full series) under a config."""
    c = config or cfg(**over)
    pts = bands.parse_series(json.dumps(daily(values)), "test")
    return bands.evaluate(c, pts, "bands.yaml")


def call(argv, stdin: str | None = None) -> tuple[int, str, str]:
    """Run main() in-process, capturing stdout/stderr and argparse exits."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        with mock.patch.object(sys, "stdin", io.StringIO(stdin or "")):
            try:
                rc = bands.main(argv)
            except SystemExit as e:
                rc = e.code
    return rc, out.getvalue(), err.getvalue()


def dump_yaml(path: Path, data) -> Path:
    import yaml
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return path


def clean_env() -> dict:
    """A filtered environment for subprocesses: nothing from the operator leaks."""
    keep = ("PATH", "HOME", "LANG", "LC_ALL", "TMPDIR", "SYSTEMROOT")
    env = {k: os.environ[k] for k in keep if k in os.environ}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


class TempDirCase(unittest.TestCase):
    """A temp dir that is also the cwd, so a red run cannot write into the repo:
    a cwd-relative path built from an unexpected message lands here instead."""

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name).resolve()
        self._cwd = os.getcwd()
        os.chdir(self.tmp)

    def tearDown(self):
        os.chdir(self._cwd)
        self._td.cleanup()

    def written(self, out: str) -> Path:
        """The path `intent` printed, proven to be a file inside the temp dir."""
        p = Path(out.strip())
        self.assertTrue(p.is_absolute() and self.tmp in p.resolve().parents,
                        f"intent did not print a path inside the temp dir: {out!r}")
        self.assertTrue(p.is_file(), f"no file at {p}")
        return p

    def series_file(self, values, name="s.json") -> Path:
        p = self.tmp / name
        p.write_text(json.dumps(daily(values)), encoding="utf-8")
        return p

    def config_file(self, data=None, name="bands.yaml") -> Path:
        return dump_yaml(self.tmp / name, data if data is not None else cfg())


# --- Western Electric rules -------------------------------------------------------

class WesternElectricTest(unittest.TestCase):
    def assertFires(self, values, rules, tier, **over):
        r = run(BASE + values, **over)
        self.assertEqual(r["rules_fired"], rules)
        self.assertEqual(r["tier"], tier)
        return r

    def test_no_breach_is_none(self):
        r = self.assertFires(QUIET, [], "none")
        self.assertEqual(r["action"], "none")

    def test_r1_fires_alone(self):
        r = self.assertFires(R1_ONLY, ["R1"], "3sigma")
        self.assertEqual(r["action"], "propose")

    def test_r1_is_strict_at_exactly_3_sigma(self):
        # Baseline [8, 10, 12]: mean 10, sample std exactly 2, so v=16 is z=3.0.
        c = cfg(min_baseline_points=3)
        at = run([8, 10, 12] + [10] * 7 + [16], config=c)
        self.assertEqual(at["evaluated"][-1]["z"], 3.0)
        self.assertEqual(at["rules_fired"], [])
        past = run([8, 10, 12] + [10] * 7 + [16.5], config=c)
        self.assertEqual(past["rules_fired"], ["R1"])

    def test_r2_fires_alone(self):
        self.assertFires(R2_ONLY, ["R2"], "2sigma")

    def test_r2_near_miss_one_of_three(self):
        self.assertFires([10, 9.5, 10, 10.5, 9.5, 12.5, 10, 10.5], [], "none")

    def test_r2_needs_the_same_side(self):
        # +2.44σ and -2.44σ in the last 3: two beyond 2σ, but on opposite sides.
        self.assertFires([10, 9.5, 10, 10.5, 9.5, 12.5, 10, 7.5], [], "none", direction="both")

    def test_r3_fires_alone(self):
        self.assertFires(R3_ONLY, ["R3"], "1sigma")

    def test_r3_near_miss_three_of_five(self):
        self.assertFires([10, 9.5, 10, 11.5, 10, 10, 11.5, 11.5], [], "none")

    def test_r4_fires_alone(self):
        r = self.assertFires(R4_ONLY, ["R4"], "1sigma")
        self.assertEqual(r["action"], "log")

    def test_r4_near_miss_seven_of_eight(self):
        self.assertFires([9.5] + [10.5] * 7, [], "none")

    def test_r4_point_at_the_mean_breaks_the_run(self):
        self.assertFires([10.5] * 7 + [10], [], "none")

    def test_precedence_all_rules_is_3sigma(self):
        self.assertFires([11.5] * 5 + [12.5, 12.5, 14], ["R1", "R2", "R3", "R4"], "3sigma")

    def test_precedence_r2_outranks_r3_and_r4(self):
        self.assertFires([11.5] * 5 + [12.5, 12.5, 12.5], ["R2", "R3", "R4"], "2sigma")

    def test_direction_high_ignores_a_low_breach(self):
        low = [20 - v for v in R2_ONLY]           # mirror R2_ONLY about the mean
        self.assertFires(low, [], "none", direction="high")
        self.assertFires([9.5] * 8, [], "none", direction="high")

    def test_direction_low_fires_on_the_low_side(self):
        low = [20 - v for v in R2_ONLY]
        self.assertFires(low, ["R2"], "2sigma", direction="low")
        self.assertFires([9.5] * 8, ["R4"], "1sigma", direction="low")
        self.assertFires(R2_ONLY, [], "none", direction="low")

    def test_direction_both_fires_either_side(self):
        self.assertFires(R2_ONLY, ["R2"], "2sigma", direction="both")
        self.assertFires([20 - v for v in R1_ONLY], ["R1"], "3sigma", direction="both")


# --- baseline ---------------------------------------------------------------------------

class BaselineTest(unittest.TestCase):
    def test_insufficient_baseline_is_not_none(self):
        # Five baseline points under a floor of 10 — and a last point that would be
        # a screaming 3σ breach. Unmeasured must win over both healthy and breach.
        r = run([9, 11, 9, 11, 9] + [10] * 7 + [50])
        self.assertEqual(r["tier"], bands.INSUFFICIENT)
        self.assertNotEqual(r["tier"], "none")
        self.assertEqual(r["rules_fired"], [])
        self.assertEqual(r["action"], "none")
        self.assertEqual(r["baseline"]["n"], 5)
        self.assertIsNone(r["baseline"]["mean"])
        self.assertTrue(all(p["z"] is None for p in r["evaluated"]))

    def test_series_no_longer_than_the_window_is_insufficient(self):
        r = run([10] * 8)
        self.assertEqual(r["tier"], bands.INSUFFICIENT)
        self.assertEqual(r["baseline"]["n"], 0)
        self.assertIsNone(r["baseline"]["from"])

    def test_sample_std_uses_ddof_1(self):
        r = run([8, 10, 12] + QUIET, config=cfg(min_baseline_points=3))
        self.assertEqual(r["baseline"]["mean"], 10.0)
        self.assertEqual(r["baseline"]["std"], 2.0)     # population std would be 1.633

    def test_std_zero_at_the_mean_is_z_zero(self):
        r = run([10] * 12 + [10] * 8)
        self.assertEqual(r["baseline"]["std"], 0.0)
        self.assertEqual([p["z"] for p in r["evaluated"]], [0.0] * 8)
        self.assertEqual(r["tier"], "none")

    def test_std_zero_any_departure_is_infinite(self):
        r = run([10] * 12 + [10] * 7 + [10.001])
        self.assertEqual(r["evaluated"][-1]["z"], "+inf")
        self.assertEqual(r["rules_fired"], ["R1"])
        self.assertEqual(r["tier"], "3sigma")
        low = run([10] * 12 + [10] * 7 + [9.999], config=cfg(direction="both"))
        self.assertEqual(low["evaluated"][-1]["z"], "-inf")

    def test_infinite_z_serialises_as_strict_json(self):
        r = run([10] * 12 + [10] * 7 + [11])

        def refuse(const):
            raise AssertionError(f"non-standard JSON constant {const}")
        json.loads(json.dumps(r), parse_constant=refuse)

    def test_rolling_days_excludes_points_older_than_the_window(self):
        # 30 days of an old regime at 100, then BASE (20 days), then R2_ONLY (8 days).
        series = [100] * 30 + BASE + R2_ONLY          # last point: day 57
        r = run(series, config=cfg(baseline="rolling_28d"))
        self.assertEqual(r["baseline"]["n"], 20)
        self.assertEqual(r["baseline"]["mean"], 10.0)
        self.assertEqual(r["baseline"]["from"], (START + dt.timedelta(days=30)).isoformat())
        self.assertEqual(r["tier"], "2sigma")
        # Boundary: rolling_29d reaches exactly one day further, into the old regime.
        r29 = run(series, config=cfg(baseline="rolling_29d"))
        self.assertEqual(r29["baseline"]["n"], 21)
        self.assertEqual(r29["baseline"]["from"], (START + dt.timedelta(days=29)).isoformat())

    def test_rolling_points_counts_points_not_days(self):
        # One point every 2 days: 30 days hold 15 points, 30 points span 60 days.
        pts = bands.parse_series(json.dumps(daily([9, 11] * 20, step=2)), "test")
        by_days = bands.evaluate(cfg(baseline="rolling_30d", min_baseline_points=5), pts)
        by_points = bands.evaluate(cfg(baseline="rolling_30", min_baseline_points=5), pts)
        self.assertEqual(by_days["baseline"]["n"], 15 - 8)
        self.assertEqual(by_points["baseline"]["n"], 30 - 8)

    def test_input_order_does_not_matter(self):
        pts = daily(BASE + R2_ONLY)
        shuffled = pts[1::2] + pts[0::2]
        a = bands.evaluate(cfg(), bands.parse_series(json.dumps(pts)))
        b = bands.evaluate(cfg(), bands.parse_series(json.dumps(shuffled)))
        self.assertEqual(a, b)


# --- series input ---------------------------------------------------------------

class SeriesInputTest(TempDirCase):
    def test_csv_matches_json(self):
        j = bands.parse_series((FIX / "series-2sigma.json").read_text())
        c = bands.parse_series((FIX / "series-2sigma.csv").read_text())
        self.assertEqual(bands.evaluate(cfg(), j), bands.evaluate(cfg(), c))

    def test_series_from_stdin(self):
        csv_text = (FIX / "series-2sigma.csv").read_text()
        rc, out, _ = call(["check", str(FIX / "bands.yaml"), "--series", "-", "--json"],
                          stdin=csv_text)
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(out)["tier"], "2sigma")

    def test_bad_series_exits_2(self):
        bad = {
            "empty": "",
            "csv wrong header": "time,value\n2026-09-01,1\n",
            "csv non-numeric": "t,v\n2026-09-01,abc\n",
            "csv nan": "t,v\n2026-09-01,nan\n",
            "json not array": '{"t": "2026-09-01", "v": 1}',
            "json missing v": '[{"t": "2026-09-01"}]',
            "json bool v": '[{"t": "2026-09-01", "v": true}]',
            "bad t": '[{"t": "yesterday", "v": 1}]',
            "duplicate t": '[{"t": "2026-09-01", "v": 1}, {"t": "2026-09-01", "v": 2}]',
            "json garbage": "[{",
        }
        for label, text in bad.items():
            with self.subTest(label):
                p = self.tmp / "bad.txt"
                p.write_text(text)
                rc, _, err = call(["check", str(FIX / "bands.yaml"), "--series", str(p)])
                self.assertEqual(rc, 2, err)
                self.assertIn("error:", err)

    def test_missing_series_file_exits_2(self):
        rc, _, err = call(["check", str(FIX / "bands.yaml"), "--series",
                           str(self.tmp / "nope.json")])
        self.assertEqual(rc, 2)
        self.assertIn("no such file", err)


# --- check CLI --------------------------------------------------------------------

class CheckCliTest(TempDirCase):
    def test_json_output_shape(self):
        rc, out, _ = call(["check", str(FIX / "bands.yaml"), "--series",
                           str(FIX / "series-2sigma.json"), "--json"])
        self.assertEqual(rc, 0)
        r = json.loads(out)
        for k in ("metric", "tier", "action", "rules_fired", "baseline", "evaluated",
                  "config_path"):
            self.assertIn(k, r)
        self.assertEqual(set(r["baseline"]) >= {"n", "mean", "std", "from", "to"}, True)
        self.assertEqual(len(r["evaluated"]), 8)
        self.assertEqual(set(r["evaluated"][0]), {"t", "v", "z"})
        self.assertEqual((r["tier"], r["action"], r["rules_fired"]),
                         ("2sigma", "diagnose", ["R2"]))
        self.assertEqual(r["baseline"]["from"], "2026-09-01")
        self.assertEqual(r["baseline"]["to"], "2026-09-20")
        self.assertEqual(r["config_path"], str(FIX / "bands.yaml"))

    def test_breach_without_fail_on_exits_0(self):
        rc, out, _ = call(["check", str(FIX / "bands.yaml"), "--series",
                           str(self.series_file(BASE + R1_ONLY))])
        self.assertEqual(rc, 0)
        self.assertIn("tier=3sigma", out)

    def test_fail_on_exit_codes(self):
        s2 = str(self.series_file(BASE + R2_ONLY, "two.json"))
        quiet = str(self.series_file(BASE + QUIET, "quiet.json"))
        cases = [(s2, "1sigma", 3), (s2, "2sigma", 3), (s2, "3sigma", 0),
                 (quiet, "1sigma", 0)]
        for series, fail_on, want in cases:
            with self.subTest(series=Path(series).name, fail_on=fail_on):
                rc, _, _ = call(["check", str(FIX / "bands.yaml"), "--series", series,
                                 "--fail-on", fail_on])
                self.assertEqual(rc, want)

    def test_fail_on_insufficient_baseline_exits_4(self):
        s = str(self.series_file([10] * 12))
        rc, out, err = call(["check", str(FIX / "bands.yaml"), "--series", s,
                             "--fail-on", "3sigma"])
        self.assertEqual(rc, 4)
        self.assertIn("unmeasured", out + err)
        rc, _, _ = call(["check", str(FIX / "bands.yaml"), "--series", s])
        self.assertEqual(rc, 0)


# --- config schema ----------------------------------------------------------------

class ConfigTest(TempDirCase):
    def test_shipped_template_validates(self):
        c = bands.load_config(TEMPLATE)
        self.assertEqual(c["rules"], "western_electric")

    def test_fixture_config_validates(self):
        self.assertEqual(bands.load_config(FIX / "bands.yaml"), CFG)

    def test_unparseable_yaml_exits_2(self):
        p = self.tmp / "bad.yaml"
        p.write_text("metric: [unclosed\n")
        rc, _, err = call(["check", str(p), "--series", str(FIX / "series-2sigma.json")])
        self.assertEqual(rc, 2)
        self.assertIn("unparseable YAML", err)

    def test_schema_errors_exit_2(self):
        def without(key):
            c = cfg()
            del c[key]
            return c

        def tier(name, value):
            c = cfg()
            c["tiers"][name] = value
            return c
        bad = {
            "not a mapping": ["a", "list"],
            "missing metric": without("metric"),
            "missing intent": without("intent"),
            "metric with a path": cfg(metric="../etc/passwd"),
            "unknown key": cfg(treshold=3),
            "baseline shape": cfg(baseline="30d"),
            "baseline zero": cfg(baseline="rolling_0d"),
            "rules other": cfg(rules="nelson"),
            "direction": cfg(direction="up"),
            "min points bool": cfg(min_baseline_points=True),
            "min points 1": cfg(min_baseline_points=1),
            "points window never measurable": cfg(baseline="rolling_12"),
            "missing tier": {**cfg(), "tiers": {k: v for k, v in CFG["tiers"].items()
                                                if k != "3sigma"}},
            "unknown action": tier("2sigma", {"action": "page", "tools": "Read"}),
            "1sigma escalates to diagnose": tier("1sigma", {"action": "diagnose",
                                                            "tools": "Read"}),
            "2sigma escalates to propose": tier("2sigma", {"action": "propose",
                                                           "routes": ["pull_request"]}),
            "propose without routes": tier("3sigma", {"action": "propose"}),
            "bad route": tier("3sigma", {"action": "propose", "routes": ["merge_to_main"]}),
            "tools on a log tier": tier("1sigma", {"action": "log", "tools": "Read"}),
            "diagnose with Write": tier("2sigma", {"action": "diagnose",
                                                   "tools": "Read,Write"}),
            "empty affected": cfg(intent={"affected": " "}),
        }
        for label, data in bad.items():
            with self.subTest(label):
                p = self.config_file(data)
                rc, _, err = call(["check", str(p), "--series",
                                   str(FIX / "series-2sigma.json")])
                self.assertEqual(rc, 2, f"{label}: {err}")
                self.assertIn("invalid bands config", err)

    def test_a_tier_may_do_less_than_the_course_allows(self):
        c = cfg()
        c["tiers"]["3sigma"] = {"action": "diagnose", "tools": "Read"}
        c["tiers"]["2sigma"] = {"action": "log"}
        self.assertEqual(bands.validate_config(c), [])

    def test_missing_pyyaml_exits_2_with_a_clear_message(self):
        with mock.patch.dict(sys.modules, {"yaml": None}):
            rc, _, err = call(["check", str(FIX / "bands.yaml"), "--series",
                               str(FIX / "series-2sigma.json")])
        self.assertEqual(rc, 2)
        self.assertIn("PyYAML is required", err)


# --- intent -------------------------------------------------------------------

class IntentTest(TempDirCase):
    SECTIONS = ["## Problem", "## Evidence", "## Proposed outcome",
                "## Affected users and systems", "## Constraints", "## Open questions"]

    def result_file(self, values, config=None, name="result.json") -> Path:
        r = run(BASE + values, config=config)
        r["config_path"] = "bands.yaml"
        p = self.tmp / name
        p.write_text(json.dumps(r), encoding="utf-8")
        return p

    def intent(self, result: Path, *extra, config=None, out="intent"):
        conf = config or str(FIX / "bands.yaml")
        return call(["intent", conf, "--result", str(result), "--out-dir",
                     str(self.tmp / out), *extra])

    def test_intent_content(self):
        rc, out, _ = self.intent(self.result_file(R2_ONLY))
        self.assertEqual(rc, 0)
        path = self.written(out)
        self.assertEqual(path.name, "2026-09-28-ci_test_failure_rate-2sigma.md")
        text = path.read_text()
        lines = text.splitlines()
        self.assertEqual(lines[0], "# Intent: ci_test_failure_rate breached its 2sigma control band")
        self.assertEqual(lines[2],
                         "Author: bands detector (deterministic, no model). Status: draft.")
        self.assertIn("bands.yaml", lines[3])
        self.assertIn("rules fired: R2", lines[3])
        positions = [text.index(s) for s in self.SECTIONS]
        self.assertEqual(positions, sorted(positions), "sections out of order")
        problem = text.split("## Problem")[1].split("## Evidence")[0]
        for needle in ("12.5", "10 ± 1.02598", "20 points", "2026-09-01", "2026-09-20", "R2"):
            self.assertIn(needle, problem)
        evidence = text.split("## Evidence")[1].split("## Proposed outcome")[0]
        rows = [l for l in evidence.splitlines() if l.startswith("| 2026-")]
        self.assertEqual(len(rows), 8)
        self.assertEqual(rows[-1], "| 2026-09-28 | 12.5 | +2.44 |")
        self.assertIn("within its 1σ band", text)
        self.assertIn("read-only diagnosis only", text)
        self.assertIn("`Read,Grep,Glob`", text)
        self.assertIn("CI pipeline and the test suites it runs",
                      text.split("## Affected users and systems")[1])
        constraints = text.split("## Constraints")[1].split("## Open questions")[0]
        for needle in ("deterministic", "review gate", "grants no production access"):
            self.assertIn(needle, constraints)
        self.assertIn("baseline shift", text.split("## Open questions")[1])

    def test_intent_is_deterministic(self):
        res = self.result_file(R2_ONLY)
        _, a, _ = self.intent(res, out="a")
        _, b, _ = self.intent(res, out="b")
        self.assertEqual(self.written(a).read_bytes(), self.written(b).read_bytes())

    def test_intent_never_overwrites_without_force(self):
        res = self.result_file(R2_ONLY)
        _, out, _ = self.intent(res)
        path = self.written(out)
        path.write_text("hand-edited\n")
        rc, out2, err = self.intent(res)
        self.assertEqual(rc, 0)
        self.assertEqual(out2.strip(), str(path))
        self.assertIn("not overwritten", err)
        self.assertEqual(path.read_text(), "hand-edited\n")
        rc, _, _ = self.intent(res, "--force")
        self.assertEqual(rc, 0)
        self.assertTrue(path.read_text().startswith("# Intent:"))

    def test_log_tier_writes_nothing(self):
        rc, out, _ = self.intent(self.result_file(R4_ONLY))
        self.assertEqual(rc, 0)
        self.assertIn("log only", out)
        self.assertFalse((self.tmp / "intent").exists())

    def test_none_and_insufficient_write_nothing(self):
        for label, values in (("none", QUIET), ("insufficient", None)):
            with self.subTest(label):
                if values is None:
                    r = run([10] * 12)
                    res = self.tmp / "ins.json"
                    res.write_text(json.dumps(r))
                else:
                    res = self.result_file(values)
                rc, out, _ = self.intent(res)
                self.assertEqual(rc, 0)
                self.assertIn("no breach", out)
                self.assertFalse((self.tmp / "intent").exists())

    def test_propose_tier_names_its_routes(self):
        rc, out, _ = self.intent(self.result_file(R1_ONLY))
        self.assertEqual(rc, 0)
        text = self.written(out).read_text()
        self.assertIn("3sigma control band", text)
        self.assertIn("open a pull request into the review gate", text)
        self.assertIn("pre-approved runbook `rollback-deploy`", text)

    def test_zero_variance_adds_an_open_question(self):
        res = self.tmp / "zero.json"
        res.write_text(json.dumps(run([10] * 12 + [10] * 7 + [11])))
        rc, out, _ = self.intent(res)
        self.assertEqual(rc, 0)
        text = self.written(out).read_text()
        self.assertIn("| +inf |", text)
        self.assertIn("zero variance", text)

    def test_explicit_date_and_bad_date(self):
        res = self.result_file(R2_ONLY)
        rc, out, _ = self.intent(res, "--date", "2026-10-01")
        self.assertEqual(rc, 0)
        self.assertTrue(out.strip().endswith("2026-10-01-ci_test_failure_rate-2sigma.md"))
        for bad in ("2026-13-01", "10/01/2026", "2026-10-01/../x"):
            with self.subTest(bad):
                rc, _, err = self.intent(res, "--date", bad)
                self.assertEqual(rc, 2, err)

    def test_result_for_another_metric_exits_2(self):
        res = self.result_file(R2_ONLY, config=cfg(metric="deploy_latency"))
        rc, _, err = self.intent(res)
        self.assertEqual(rc, 2)
        self.assertIn("deploy_latency", err)

    def test_result_inconsistent_with_its_rules_exits_2(self):
        r = json.loads(self.result_file(R3_ONLY).read_text())
        r["tier"], r["action"] = "3sigma", "propose"        # claims more than R3 found
        res = self.tmp / "forged.json"
        res.write_text(json.dumps(r))
        rc, _, err = self.intent(res)
        self.assertEqual(rc, 2)
        self.assertIn("does not match rules_fired", err)
        self.assertFalse((self.tmp / "intent").exists())

    def test_result_from_a_different_config_exits_2(self):
        # Produced where 2sigma diagnoses; rendered against a config where 2sigma logs.
        c = cfg()
        c["tiers"]["2sigma"] = {"action": "log"}
        rc, _, err = self.intent(self.result_file(R2_ONLY),
                                 config=str(self.config_file(c)))
        self.assertEqual(rc, 2)
        self.assertIn("re-run check", err)


    def test_json_reports_created_then_not(self):
        res = self.result_file(R2_ONLY)
        rc, out, _ = self.intent(res, "--json")
        self.assertEqual(rc, 0)
        first = json.loads(out)
        self.assertEqual({k: first[k] for k in ("created", "tier", "action", "dedupe_key")},
                         {"created": True, "tier": "2sigma", "action": "diagnose",
                          "dedupe_key": "ci_test_failure_rate-2sigma"})
        path = self.written(first["path"])
        rc, out, _ = self.intent(res, "--json")
        self.assertEqual(json.loads(out), {**first, "created": False})
        # --force rewrites the file, but it existed: still not "created", so a caller
        # gated on created=true does not re-diagnose a filed intent.
        path.write_text("hand-edited\n")
        rc, out, _ = self.intent(res, "--json", "--force")
        self.assertEqual(json.loads(out)["created"], False)
        self.assertTrue(path.read_text().startswith("# Intent:"))

    def test_json_when_nothing_is_written(self):
        rc, out, _ = self.intent(self.result_file(R4_ONLY), "--json")
        self.assertEqual(json.loads(out), {"path": None, "created": False,
                                           "tier": "1sigma", "action": "log",
                                           "dedupe_key": "ci_test_failure_rate-1sigma"})
        rc, out, _ = self.intent(self.result_file(QUIET, name="q.json"), "--json")
        self.assertEqual(json.loads(out), {"path": None, "created": False,
                                           "tier": "none", "action": "none",
                                           "dedupe_key": "ci_test_failure_rate-none"})
        self.assertFalse((self.tmp / "intent").exists())

    def test_dedupe_key_names_the_incident_not_the_day(self):
        # A sustained breach rolls the file date daily; the key must not roll with it.
        res = self.result_file(R2_ONLY)
        days = [json.loads(self.intent(res, "--json", "--date", d)[1])
                for d in ("2026-10-01", "2026-10-02")]
        self.assertNotEqual(days[0]["path"], days[1]["path"])
        self.assertEqual(days[0]["dedupe_key"], days[1]["dedupe_key"])
        self.assertEqual(days[0]["dedupe_key"], "ci_test_failure_rate-2sigma")
        three = json.loads(self.intent(self.result_file(R1_ONLY, name="r1.json"), "--json")[1])
        self.assertEqual(three["dedupe_key"], "ci_test_failure_rate-3sigma")


# --- series adapter -------------------------------------------------------------

class SeriesAdapterTest(TempDirCase):
    WANT = [
        {"t": "2026-09-01", "v": 1 / 3, "n": 3},   # cancelled + skipped not counted
        {"t": "2026-09-02", "v": 1.0, "n": 2},     # in_progress, neutral, action_required not
        {"t": "2026-09-03", "v": 1.0, "n": 1},     # timed_out counts; cancelled, queued do not
        {"t": "2026-09-04", "v": 0.4, "n": 5},     # startup_failure counts as a failure
        {"t": "2026-09-05", "v": 0.0, "n": 1},     # 23:30-05:00 is 04:30 UTC next day
    ]

    @staticmethod
    def one_day(*conclusions, status="completed"):
        return [{"status": status, "conclusion": c, "createdAt": f"2026-09-01T0{i}:00:00Z"}
                for i, c in enumerate(conclusions)]

    NOW = dt.datetime(2026, 9, 24, 6, 41)       # a fixed clock, after every fixture day

    def rate(self, runs, **kw):
        return bands.ci_failure_rate(runs, now=kw.pop("now", self.NOW), **kw)

    def test_ci_failure_rate_fixture(self):
        runs = json.loads((FIX / "gh-runs.json").read_text())
        self.assertEqual(self.rate(runs), self.WANT)

    def test_timed_out_counts_as_a_failure(self):
        self.assertEqual(self.rate(self.one_day("success", "timed_out")),
                         [{"t": "2026-09-01", "v": 0.5, "n": 2}])

    def test_startup_failure_counts_as_a_failure(self):
        self.assertEqual(self.rate(self.one_day("success", "startup_failure")),
                         [{"t": "2026-09-01", "v": 0.5, "n": 2}])

    def test_non_verdict_conclusions_are_not_counted(self):
        for c in ("cancelled", "skipped", "neutral", "action_required", "stale", "", None):
            with self.subTest(conclusion=c):
                self.assertEqual(self.rate(self.one_day("success", c)),
                                 [{"t": "2026-09-01", "v": 0.0, "n": 1}])
        # A run still going carries no conclusion yet, whatever the field says.
        self.assertEqual(self.rate(
            self.one_day("failure", status="in_progress")), [])

    def test_cli_output_feeds_check(self):
        rc, out, _ = call(["series", "ci-failure-rate", "--runs-json",
                           str(FIX / "gh-runs.json"), "--now", "2026-09-24T06:41:00Z"])
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(out), self.WANT)
        pts = bands.parse_series(out, "adapter")
        self.assertEqual([p["t"] for p in pts], [w["t"] for w in self.WANT])

    @staticmethod
    def month_with_partial_today():
        """25 full days of 20 runs at ~10% failures, then a 6-hour stub of today:
        2 runs, 1 failed. Today is 2026-09-26; the scheduled run fires at 06:41Z."""
        runs = []
        for d in range(25):
            day = dt.date(2026, 9, 1) + dt.timedelta(days=d)
            fails = (1, 2, 3, 2)[d % 4]
            for h in range(20):
                runs.append({"status": "completed",
                             "conclusion": "failure" if h < fails else "success",
                             "createdAt": f"{day.isoformat()}T{h:02d}:00:00Z"})
        runs += [{"status": "completed", "conclusion": c, "createdAt": f"2026-09-26T0{h}:00:00Z"}
                 for h, c in enumerate(("failure", "success"))]
        return runs

    def test_partial_today_is_dropped_by_default(self):
        runs = self.month_with_partial_today()
        now = dt.datetime(2026, 9, 26, 6, 41)
        series = self.rate(runs, now=now)
        self.assertEqual(series[-1]["t"], "2026-09-25")
        self.assertEqual(len(series), 25)
        r = bands.evaluate(cfg(), bands.parse_series(json.dumps(series)))
        self.assertEqual(r["tier"], "none")
        # Kept on request, the stub is exactly the false 3σ breach the default prevents.
        kept = self.rate(runs, now=now, include_today=True)
        self.assertEqual(kept[-1], {"t": "2026-09-26", "v": 0.5, "n": 2})
        r = bands.evaluate(cfg(), bands.parse_series(json.dumps(kept)))
        self.assertEqual(r["tier"], "3sigma")

    def test_partial_today_cli_flags(self):
        p = self.tmp / "runs.json"
        p.write_text(json.dumps(self.month_with_partial_today()))
        base = ["series", "ci-failure-rate", "--runs-json", str(p),
                "--now", "2026-09-26T06:41:00Z"]
        rc, out, _ = call(base)
        self.assertEqual((rc, json.loads(out)[-1]["t"]), (0, "2026-09-25"))
        rc, out, _ = call(base + ["--include-today"])
        self.assertEqual((rc, json.loads(out)[-1]["t"]), (0, "2026-09-26"))
        rc, _, _ = call(["series", "ci-failure-rate", "--runs-json", str(p), "--now", "soon"])
        self.assertEqual(rc, 2)

    def test_default_clock_drops_future_days_and_keeps_past_ones(self):
        runs = [{"status": "completed", "conclusion": "failure", "createdAt": t}
                for t in ("2000-01-01T00:00:00Z", "2999-01-01T00:00:00Z")]
        self.assertEqual([p["t"] for p in bands.ci_failure_rate(runs)], ["2000-01-01"])

    def test_malformed_runs_exit_2(self):
        bad = {
            "not a list": '{"runs": []}',
            "not objects": '["x"]',
            "bad createdAt": '[{"status": "completed", "conclusion": "failure", '
                             '"createdAt": "last tuesday"}]',
            "garbage": "[",
        }
        for label, text in bad.items():
            with self.subTest(label):
                p = self.tmp / "runs.json"
                p.write_text(text)
                rc, _, err = call(["series", "ci-failure-rate", "--runs-json", str(p)])
                self.assertEqual(rc, 2, err)


# --- diagnose-cmd -----------------------------------------------------------------

class DiagnoseCmdTest(TempDirCase):
    ALLOWED = "Read,Grep,Glob"

    def setUp(self):
        super().setUp()
        self.intent_md = self.tmp / "intent.md"
        self.intent_md.write_text("# Intent: x\n")

    def diagnose(self, config_path, *which, intent=None):
        which = which or ("--tier", "2sigma")
        return call(["diagnose-cmd", str(config_path), *which,
                     "--intent", str(intent or self.intent_md)])

    def expected(self, tools, base="Read Grep Glob"):
        return ["claude", "-p", bands.DIAGNOSE_PROMPT.format(intent=str(self.intent_md)),
                "--restricted", "--strict-mcp-config",
                "--tools", base,
                "--allowedTools", tools,
                "--disallowedTools", "Edit Write MultiEdit NotebookEdit",
                "--permission-mode", "dontAsk", "--output-format", "json"]

    @staticmethod
    def granted(out):
        argv = json.loads(out)
        return argv[argv.index("--allowedTools") + 1]

    def test_tools_lists_only_the_granted_base_names(self):
        self.assertEqual(bands.base_tools("Glob, LS,Read,Read Glob"), "Glob LS Read")
        c = cfg()
        c["tiers"]["2sigma"]["tools"] = "Read,Glob,Read"
        rc, out, err = self.diagnose(self.config_file(c))
        self.assertEqual(rc, 0, err)
        argv = json.loads(out)
        self.assertEqual(argv[argv.index("--tools") + 1], "Read Glob")
        self.assertEqual(argv.count("--tools"), 1)

    def test_exact_argv(self):
        rc, out, _ = self.diagnose(FIX / "bands.yaml")
        self.assertEqual(rc, 0)
        argv = json.loads(out)
        self.assertEqual(argv, self.expected(self.ALLOWED))
        # No shell and no MCP: the two flags that make "no shell" hold beyond --tools.
        self.assertIn("--restricted", argv)
        self.assertIn("--strict-mcp-config", argv)
        self.assertNotIn("Bash", " ".join(argv[3:]))
        self.assertNotIn("--dangerously-skip-permissions", argv)
        self.assertIn(str(self.intent_md), argv[2])
        self.assertIn("## Diagnosis", argv[2])
        self.assertIn("read-only", argv[2])

    def test_result_picks_the_tier(self):
        res = self.tmp / "r.json"
        res.write_text(json.dumps(run(BASE + R2_ONLY)))
        rc, out, _ = self.diagnose(FIX / "bands.yaml", "--result", str(res))
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(out), self.expected(self.ALLOWED))

    def test_needs_exactly_one_of_result_or_tier(self):
        res = self.tmp / "r.json"
        res.write_text(json.dumps(run(BASE + R2_ONLY)))
        for which in ((), ("--tier", "2sigma", "--result", str(res)), ("--tier", "none")):
            with self.subTest(which=which):
                rc, out, _ = call(["diagnose-cmd", str(FIX / "bands.yaml"), *which,
                                   "--intent", str(self.intent_md)])
                self.assertEqual(rc, 2)
                self.assertEqual(out, "")

    def test_allowlist_accepts_exactly_its_shapes(self):
        self.assertEqual(bands.DIAGNOSE_TOOLS, ("Read", "Grep", "Glob", "LS"))
        for tok in bands.DIAGNOSE_TOOLS:
            with self.subTest(tok=tok):
                self.assertEqual(bands.tool_errors(tok), [])
        self.assertEqual(bands.tool_tokens("Read, Bash(git log *) Grep"),
                         ["Read", "Bash(git log *)", "Grep"])
        # Comma/space separators are normalised; the argv carries exactly the tokens
        # that were checked.
        c = cfg()
        c["tiers"]["2sigma"]["tools"] = "Read, Grep  LS"
        rc, out, err = self.diagnose(self.config_file(c))
        self.assertEqual(rc, 0, err)
        self.assertEqual(self.granted(out), "Read,Grep,LS")

    def test_any_bash_entry_is_refused(self):
        # Every shape the round-1 allowlist accepted, and the bare shell: all refused,
        # with the message that points at the fix (pre-fetch, don't grant the shell).
        former = ("gh run view", "gh run list", "gh pr view", "git log", "git show",
                  "git diff", "git status", "cat", "ls", "head", "tail", "wc")
        for tok in [f"Bash({p} *)" for p in former] + ["Bash", "BASH", "bash(cat *)"]:
            with self.subTest(tok=tok):
                errs = bands.tool_errors(f"Read,{tok}")
                self.assertEqual(len(errs), 1)
                self.assertIn("no shell", errs[0])
                self.assertIn("pre-fetches", errs[0])
                c = cfg()
                c["tiers"]["2sigma"]["tools"] = f"Read,{tok}"
                rc, out, err = self.diagnose(self.config_file(c))
                self.assertEqual((rc, out), (2, ""), err)
                self.assertIn("pre-fetches the data the diagnosis reads", err)

    def test_allowlist_refuses_everything_else(self):
        denied = ("Bash(**)", "Bash(* *)", "Bash(rm *)", "Bash(git push *)", "Bash(gh *)",
                  "Bash(sed -i *)", "Bash(*)", "Bash", "BASH", "Bash(gh run view:*)",
                  "Bash(cat)", "Bash(gh run view *", "bash(cat *)", "Bash (cat *)",
                  "mcp__github__create_pull_request", "Agent", "Skill", "WebFetch",
                  "Edit", "EDIT", "write", " Write", "MultiEdit", "NotebookEdit", "read",
                  "--dangerously-skip-permissions")
        for tok in denied:
            with self.subTest(tok=tok):
                self.assertNotEqual(bands.tool_errors(f"Read,{tok}"), [])
                c = cfg()
                c["tiers"]["2sigma"]["tools"] = f"Read,{tok}"
                rc, out, err = self.diagnose(self.config_file(c))
                self.assertEqual(rc, 2, err)
                self.assertIn("allowlist", err)
                self.assertEqual(out, "")
                # Refused at config load, so `check` fails on it too, not just diagnose.
                rc, _, _ = call(["check", str(self.tmp / "bands.yaml"), "--series",
                                 str(FIX / "series-2sigma.json")])
                self.assertEqual(rc, 2)

    def test_propose_tier_tools_are_allowlisted_too(self):
        c = cfg()
        c["tiers"]["3sigma"]["tools"] = "Bash(gh *)"
        self.assertTrue(any("tiers.3sigma" in e for e in bands.validate_config(c)))

    def test_function_refuses_even_an_unvalidated_config(self):
        # The importable door: a hand-built cfg that never went through load_config.
        c = cfg()
        c["tiers"]["2sigma"]["tools"] = "Read,Bash(gh *)"
        with self.assertRaises(bands.BandsError) as ctx:
            bands.diagnose_argv(c, "2sigma", "intent.md")
        self.assertIn("allowlist", str(ctx.exception))

    def test_breaching_tier_tools_win_over_2sigma(self):
        # 3sigma diagnoses with Read only: the command must not grant 2sigma's wider set,
        # and the intent must record the same grant the command carries.
        c = cfg()
        c["tiers"]["3sigma"] = {"action": "diagnose", "tools": "Read"}
        conf = self.config_file(c)
        rc, out, _ = self.diagnose(conf, "--tier", "3sigma")
        self.assertEqual(rc, 0)
        self.assertEqual(self.granted(out), "Read")
        res = self.tmp / "r3.json"
        res.write_text(json.dumps(run(BASE + R1_ONLY, config=c)))
        rc, path, _ = call(["intent", str(conf), "--result", str(res), "--out-dir",
                            str(self.tmp / "intent")])
        text = self.written(path).read_text()
        self.assertIn("Claude may use `Read` and", text)
        self.assertNotIn("Read,Grep", text)

    def test_tier_without_tools_falls_back_to_2sigma(self):
        rc, out, _ = self.diagnose(FIX / "bands.yaml", "--tier", "3sigma")
        self.assertEqual(rc, 0)
        self.assertEqual(self.granted(out), self.ALLOWED)

    def test_no_tools_anywhere_skips_with_exit_0(self):
        # Valid and cautious: 2sigma only logs, 3sigma proposes with no tools.
        c = cfg()
        c["tiers"]["2sigma"] = {"action": "log"}
        conf = self.config_file(c)
        for tier in ("2sigma", "3sigma"):
            with self.subTest(tier=tier):
                rc, out, err = self.diagnose(conf, "--tier", tier,
                                             intent=self.tmp / "missing.md")
                self.assertEqual(rc, 0, err)
                self.assertEqual(json.loads(out), {"skip": "no diagnose tools configured"})

    def test_non_breach_result_skips_with_exit_0(self):
        res = self.tmp / "quiet.json"
        res.write_text(json.dumps(run(BASE + QUIET)))
        rc, out, _ = self.diagnose(FIX / "bands.yaml", "--result", str(res))
        self.assertEqual(rc, 0)
        self.assertIn("skip", json.loads(out))

    def test_missing_intent_exits_2(self):
        rc, _, err = self.diagnose(FIX / "bands.yaml", intent=self.tmp / "missing.md")
        self.assertEqual(rc, 2)
        self.assertIn("no intent file", err)

    def test_result_for_another_metric_exits_2(self):
        res = self.tmp / "other.json"
        res.write_text(json.dumps(run(BASE + R2_ONLY, config=cfg(metric="deploy_latency"))))
        rc, _, err = self.diagnose(FIX / "bands.yaml", "--result", str(res))
        self.assertEqual(rc, 2)
        self.assertIn("deploy_latency", err)


# --- entry points -------------------------------------------------------------------

class EntryPointTest(unittest.TestCase):
    def test_script_and_shim_run_as_subprocesses(self):
        for argv in ([sys.executable, str(REPO / "scripts" / "bands.py"), "--help"],
                     ["bash", str(REPO / "bin" / "bstack-bands"), "check", str(TEMPLATE),
                      "--series", str(FIX / "series-2sigma.json"), "--json"]):
            with self.subTest(argv=argv[1]):
                p = subprocess.run(argv, capture_output=True, text=True, env=clean_env(),
                                   cwd=REPO, timeout=60)
                self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(json.loads(p.stdout)["tier"], "2sigma")


if __name__ == "__main__":
    unittest.main()
