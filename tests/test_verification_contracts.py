"""Exercise public CLI, bootstrap, schemas and advisory diagnostics offline."""

from __future__ import annotations

import copy
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import jsonschema

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "contracts", ROOT / "scripts/verification_contracts.py"
)
contracts = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(contracts)
REVIEW_SPEC = importlib.util.spec_from_file_location(
    "review", ROOT / "scripts/cross-review.py"
)
review = importlib.util.module_from_spec(REVIEW_SPEC)
REVIEW_SPEC.loader.exec_module(review)


def good_review():
    return {
        "verdict": "pass",
        "anti_slop_score": 10,
        "axes": [2] * 5,
        "criticality": "low",
        "findings": [],
        "blind_spots_surfaced": [],
        "summary": "No reproduced material defect within reviewed scope.",
    }


def good_measurement():
    return {
        "claim": "target signal",
        "conditions": "run-1, detection limit 0.1",
        "observation_ref": "raw:target",
        "positive_control": "pass",
        "control_ref": "raw:control",
        "target_outcome": "not_detected",
        "falsification_criterion": "controlled null falsifies scoped prediction",
        "absence_refutes": True,
        "claim_verdict": "refuted",
    }


def good_packet():
    packet = {
        "artifact_hash": "sha256:abc",
        "primary_sources": ["raw:1"],
        "claims": [
            {
                "id": "c1",
                "claim": "target signal",
                "what_supports_it": "raw:1",
                "whats_missing": "control",
                "status": "untested",
            }
        ],
    }
    for role in ("skeptic", "believer", "refuter"):
        packet[role] = {
            "dispatch_id": role,
            "fresh_context": True,
            "artifact_hash": "sha256:abc",
            "reviewed_claim_ids": ["c1"],
            "raw_sources_read": ["raw:1"],
            "findings": [],
        }
    return packet


def good_plan():
    common = {
        "operation": "observe target",
        "owner": "agent",
        "settling_observation": "raw result meets declared condition",
    }
    return [
        {
            **common,
            "id": "read",
            "tier": "executable_now",
            "authorized": True,
            "catalog_ref": "tool:read",
            "input_schema_ref": "schema:read.input",
            "output_schema_ref": "schema:read.output",
            "readiness_evidence": "receipt:live",
        },
        {
            **common,
            "id": "wrapper",
            "tier": "request_quote",
            "substrate_ref": "local",
            "missing_prerequisite": "wrapper",
            "promotion_check": "wrapper smoke passes",
        },
        {
            **common,
            "id": "manual",
            "tier": "send_out",
            "external_executor": "owner",
            "handoff_artifact": "work-order.md",
            "return_evidence": "raw receipt",
        },
    ]


class ContractTests(unittest.TestCase):
    def test_all_shipped_schemas_are_valid(self):
        for name in contracts.CONTRACTS:
            schema = json.loads((ROOT / "schemas" / f"{name}.v1.json").read_text())
            jsonschema.Draft202012Validator.check_schema(schema)

    def test_review_score_rules(self):
        for axes in ([2] * 5, [1, 1, 1, 2, 2], [1] * 5, [2, 2, 2, 2, 0]):
            for verdict in ("pass", "concerns", "fail"):
                result = {
                    **good_review(),
                    "axes": axes,
                    "anti_slop_score": sum(axes),
                    "verdict": verdict,
                }
                if verdict == "pass" and (sum(axes) < 7 or 0 in axes):
                    with self.assertRaises(contracts.ContractError):
                        contracts.validate_review_result(result)
                else:
                    contracts.validate_review_result(result)

    def test_malformed_reviews_never_pass(self):
        for patch_value in (
            {"axes": [True] * 5},
            {"anti_slop_score": 9},
            {"findings": {}},
            {"summary": " "},
            {"verdict": "approved"},
            {"codex_elapsed_sec": float("inf")},
            {"axes": [2] * 4},
        ):
            with self.subTest(value=patch_value):
                with self.assertRaises(contracts.ContractError):
                    contracts.validate_review_result({**good_review(), **patch_value})
        with self.assertRaises(contracts.ContractError):
            contracts.validate_review_result({"verdict": "pass", "anti_slop_score": 0})

    def test_settling_observation_required_and_rendered(self):
        finding = {
            "severity": "warning",
            "category": "correctness",
            "file": "x.py",
            "line": 1,
            "issue": "possible stale read",
            "evidence": "x.py:1",
            "fix_hint": None,
            "settling_observation": "a fresh read returning the same value",
        }
        result = {**good_review(), "findings": [finding]}
        contracts.validate_review_result(result)
        rendered = review.format_verdict_md(result, {"pr": 1, "head_sha": "abc"})
        self.assertIn(finding["settling_observation"], rendered)
        for bad in (None, " ", 7):
            broken = copy.deepcopy(result)
            broken["findings"][0]["settling_observation"] = bad
            with self.assertRaises(contracts.ContractError):
                contracts.validate_review_result(broken)

    def test_json_parser_rejects_duplicate_nonfinite_and_nonobject(self):
        for raw in (
            '{"verdict":"fail","verdict":"pass"}',
            "[]",
            "null",
            '{"anti_slop_score":NaN}',
            'prefix {"x":1,"x":2} suffix',
        ):
            self.assertIsNone(review.try_parse_json(raw))

    def test_every_measurement_control_target_combination(self):
        for control in ("pass", "fail", "missing", "error", "skipped"):
            for target in ("detected", "not_detected", "error", "skipped"):
                for falsifies in (True, False):
                    verdict = (
                        "inconclusive_no_positive_control"
                        if control != "pass"
                        else "inconclusive"
                        if target in ("error", "skipped")
                        else "detected"
                        if target == "detected"
                        else "refuted"
                        if falsifies
                        else "not_detected"
                    )
                    result = {
                        **good_measurement(),
                        "positive_control": control,
                        "target_outcome": target,
                        "absence_refutes": falsifies,
                        "claim_verdict": verdict,
                    }
                    contracts.validate("measurement", result)
                    if verdict != "refuted":
                        with self.assertRaises(contracts.ContractError):
                            contracts.validate(
                                "measurement", {**result, "claim_verdict": "refuted"}
                            )

    def test_claim_receipt_integrity(self):
        contracts.validate("claim-review", good_packet())
        for role in ("skeptic", "believer", "refuter"):
            for key, bad in (
                ("fresh_context", False),
                ("artifact_hash", "changed"),
                ("reviewed_claim_ids", []),
                ("raw_sources_read", ["summary"]),
            ):
                p = good_packet()
                p[role][key] = bad
                with self.assertRaises(contracts.ContractError):
                    contracts.validate("claim-review", p)
        p = good_packet()
        p["believer"]["dispatch_id"] = p["skeptic"]["dispatch_id"]
        with self.assertRaises(contracts.ContractError):
            contracts.validate("claim-review", p)

    def test_capability_tiers_preserve_nonexecutable_proposals(self):
        contracts.validate("capability-plan", good_plan())
        for field in (
            "authorized",
            "input_schema_ref",
            "output_schema_ref",
            "readiness_evidence",
        ):
            plan = good_plan()
            del plan[0][field]
            with self.assertRaises(contracts.ContractError):
                contracts.validate("capability-plan", plan)
        plan = good_plan()
        plan[1]["id"] = plan[0]["id"]
        with self.assertRaises(contracts.ContractError):
            contracts.validate("capability-plan", plan)

    def test_install_preserves_existing_files_and_broken_symlinks(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            first = contracts.install(workspace)
            self.assertEqual(len(first["installed"]), 4)
            sentinel = workspace / "schemas/claim-review.v1.json"
            sentinel.write_text("custom schema; preserve")
            link = workspace / "schemas/measurement.v1.json"
            link.unlink()
            link.symlink_to(workspace / "not-present.json")
            second = contracts.install(workspace)
            self.assertFalse(second["installed"])
            self.assertEqual(len(second["preserved"]), 4)
            self.assertEqual(sentinel.read_text(), "custom schema; preserve")
            self.assertTrue(link.is_symlink())

    def test_doctor_controls_catch_broken_or_vacuous_validator(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            self.assertEqual(
                contracts.doctor(workspace)["validator"], "operational_on_fixtures"
            )
            for implementation in (
                lambda *_: None,
                lambda *_: (_ for _ in ()).throw(contracts.ContractError("dead")),
            ):
                with patch.object(contracts, "validate", implementation):
                    self.assertEqual(
                        contracts.doctor(workspace)["validator"], "inconclusive"
                    )
            contracts.install(workspace)
            report = contracts.doctor(workspace)
            self.assertTrue(
                all(state == "available" for state in report["contracts"].values())
            )
            (workspace / "schemas/measurement.v1.json").write_text("{}")
            self.assertEqual(
                contracts.doctor(workspace)["contracts"]["measurement"], "diverged"
            )

    def test_bootstrap_distributes_contracts_and_doctor_remains_advisory(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            workspace = Path(tmp) / "workspace"
            home.mkdir()
            workspace.mkdir()
            subprocess.run(["git", "init", "-q", str(workspace)], check=True)
            env = {
                **os.environ,
                "HOME": str(home),
                "BSTACK_DOCTOR_HOME": str(home),
                "BROOMVA_WORKSPACE": str(workspace),
                "BSTACK_SKIP_SKILLS": "1",
                "BSTACK_SKIP_RCS": "1",
                "BROOMVA_STATE_DIR": str(home / ".state"),
            }
            command = ["bash", str(ROOT / "scripts/bootstrap.sh")]
            first = subprocess.run(
                command, env=env, cwd=workspace, capture_output=True, text=True
            )
            self.assertEqual(first.returncode, 0, first.stderr + first.stdout)
            for name in contracts.CONTRACTS:
                self.assertEqual(
                    (workspace / "schemas" / f"{name}.v1.json").read_bytes(),
                    (ROOT / "schemas" / f"{name}.v1.json").read_bytes(),
                )
            schema = workspace / "schemas/claim-review.v1.json"
            schema.write_text('{"custom": true}')
            second = subprocess.run(
                command, env=env, cwd=workspace, capture_output=True, text=True
            )
            self.assertEqual(second.returncode, 0, second.stderr + second.stdout)
            self.assertEqual(schema.read_text(), '{"custom": true}')
            doctor_command = ["bash", str(ROOT / "scripts/doctor.sh")]
            verbose = subprocess.run(
                doctor_command, env=env, cwd=workspace, capture_output=True, text=True
            )
            self.assertIn('"mode": "advisory"', verbose.stdout)
            self.assertIn('"claim-review": "diverged"', verbose.stdout)
            quiet = subprocess.run(
                doctor_command + ["--quiet"],
                env=env,
                cwd=workspace,
                capture_output=True,
                text=True,
            )
            self.assertNotIn('"mode": "advisory"', quiet.stdout)
            self.assertEqual(quiet.returncode, verbose.returncode)
            for name in contracts.CONTRACTS:
                (workspace / "schemas" / f"{name}.v1.json").unlink()
            missing = subprocess.run(
                doctor_command + ["--quiet"],
                env=env,
                cwd=workspace,
                capture_output=True,
                text=True,
            )
            self.assertEqual(missing.returncode, quiet.returncode)

            # Advisory additions must not change either counters or strict policy.
            def counts(out):
                return next(
                    line
                    for line in out.splitlines()
                    if line.startswith("[bstack doctor]") and "passed" in line
                )

            self.assertEqual(counts(missing.stdout), counts(quiet.stdout))

    def test_verify_cli_is_explicit_when_dependency_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            record = Path(tmp) / "record.json"
            record.write_text(json.dumps(good_measurement()))
            script = ROOT / "scripts/verification_contracts.py"
            run = subprocess.run(
                [sys.executable, "-I", "-S", str(script), "measurement", str(record)],
                capture_output=True,
                text=True,
            )
            self.assertEqual(run.returncode, 2)
            self.assertEqual(
                json.loads(run.stdout)["status"], "inconclusive_dependency"
            )

    def test_public_verify_returns_only_catalog_ids_and_keeps_inconclusive_verdict(
        self,
    ):
        for name, record in (
            ("capability-plan", good_plan()),
            (
                "measurement",
                {
                    **good_measurement(),
                    "positive_control": "missing",
                    "claim_verdict": "inconclusive_no_positive_control",
                },
            ),
        ):
            run = subprocess.run(
                [str(ROOT / "bin/bstack"), "verify", name, "-"],
                input=json.dumps(record),
                capture_output=True,
                text=True,
            )
            self.assertEqual(run.returncode, 0, run.stderr + run.stdout)
            output = json.loads(run.stdout)
            if name == "capability-plan":
                self.assertEqual(output["executable_now"], ["read"])
            else:
                self.assertEqual(
                    output["claim_verdict"], "inconclusive_no_positive_control"
                )


class LiveReviewCliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.directory = Path(self.tmp.name)
        self.response = self.directory / "response.json"
        metadata = {
            "number": 1,
            "title": "fixture",
            "headRefOid": "abc",
            "headRefName": "feature",
            "baseRefName": "main",
            "changedFiles": 0,
            "additions": 1,
            "deletions": 0,
            "author": {"login": "fixture"},
            "url": "https://example.invalid/pull/1",
            "files": [],
        }
        gh = self.directory / "gh"
        gh.write_text(
            "#!/usr/bin/env python3\nimport json,sys\n"
            + 'if sys.argv[1:3] == ["pr","view"]: print('
            + repr(json.dumps(metadata))
            + ")\n"
            + 'elif sys.argv[1:3] == ["pr","diff"]: print("diff --git a/x b/x")\n'
            + "else: raise SystemExit(2)\n"
        )
        gh.chmod(0o755)
        codex = self.directory / "codex"
        codex.write_text(
            "#!/usr/bin/env python3\nfrom pathlib import Path\n"
            + "print(Path("
            + repr(str(self.response))
            + ").read_text())\n"
        )
        codex.chmod(0o755)
        self.env = {
            **os.environ,
            "PATH": str(self.directory) + os.pathsep + os.environ["PATH"],
        }

    def invoke(self, raw):
        self.response.write_text(raw)
        return subprocess.run(
            [
                str(ROOT / "bin/bstack"),
                "cross-review",
                "1",
                "--repo",
                "test/repo",
                "--out",
                str(self.directory / "receipt.json"),
            ],
            cwd=self.directory,
            env=self.env,
            capture_output=True,
            text=True,
        )

    def test_actual_cli_never_passes_invalid_model_json(self):
        for raw in (
            '{"verdict":"pass","anti_slop_score":0}',
            "[]",
            '{"verdict":NaN}',
            json.dumps({**good_review(), "anti_slop_score": 0}),
            json.dumps(
                {**good_review(), "axes": [2, 2, 2, 2, 0], "anti_slop_score": 8}
            ),
        ):
            run = self.invoke(raw)
            self.assertEqual(run.returncode, 30, run.stderr + run.stdout)
            self.assertEqual(json.loads(run.stdout)["verdict"], "skipped")
            receipt = json.loads((self.directory / "receipt.json").read_text())
            self.assertEqual(receipt["head_sha"], "abc")
            self.assertEqual(receipt["result"]["verdict"], "skipped")

    def test_syntax_error_log_candidate_does_not_hide_valid_review(self):
        result = good_review()
        raw = "progress {pending}\n" + json.dumps(result)
        self.assertEqual(review.try_parse_json(raw), result)
        run = self.invoke(raw)
        self.assertEqual(run.returncode, 0, run.stderr + run.stdout)
        self.assertEqual(json.loads(run.stdout)["verdict"], "pass")
        # A structurally ambiguous object still refuses, even with a good one later.
        for rejected in ('{"x": 1, "x": 2}', '{"x": NaN}'):
            run = self.invoke(rejected + "\n" + json.dumps(result))
            self.assertEqual(run.returncode, 30, run.stderr + run.stdout)

    def test_large_integer_duration_does_not_overflow_review_boundary(self):
        result = {**good_review(), "codex_elapsed_sec": 10**400}
        contracts.validate_review_result(result)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "review.json"
            path.write_text(json.dumps(result))
            verify = subprocess.run(
                [str(ROOT / "bin/bstack"), "verify", "review-result", str(path)],
                capture_output=True,
                text=True,
            )
            self.assertEqual(verify.returncode, 0, verify.stderr + verify.stdout)
        run = self.invoke(json.dumps(result))
        self.assertEqual(run.returncode, 0, run.stderr + run.stdout)
        elapsed = json.loads(run.stdout)["codex_elapsed_sec"]
        self.assertIsInstance(elapsed, (int, float))
        self.assertGreaterEqual(elapsed, 0)
        self.assertLess(elapsed, 10**400)  # host measurement replaces model metadata
        for invalid in (-1, float("inf"), float("-inf"), True, "0"):
            with self.assertRaises(contracts.ContractError):
                contracts.validate_review_result(
                    {**good_review(), "codex_elapsed_sec": invalid}
                )

    def test_valid_pass_concerns_and_failure_keep_existing_exit_codes(self):
        for verdict, code in (("pass", 0), ("concerns", 10), ("fail", 20)):
            result = {**good_review(), "verdict": verdict}
            run = self.invoke(json.dumps(result))
            self.assertEqual(run.returncode, code, run.stderr + run.stdout)
            actual = json.loads(run.stdout)
            contracts.validate_review_result(actual)
            schema = json.loads((ROOT / "schemas/review-result.v1.json").read_text())
            jsonschema.validate(actual, schema)


if __name__ == "__main__":
    unittest.main()
