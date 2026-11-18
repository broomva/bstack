#!/usr/bin/env python3
"""Versioned verification records; structure does not certify source truth."""

from __future__ import annotations

import argparse
import json
import math
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONTRACTS = ("review-result", "claim-review", "measurement", "capability-plan")


class ContractError(ValueError):
    """An input does not satisfy its declared contract."""


def _text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def loads(raw: str) -> object:
    """Refuse duplicate keys and non-JSON numbers rather than guessing a verdict."""

    def unique(pairs):
        out = {}
        for key, value in pairs:
            if key in out:
                raise ContractError("duplicate JSON key")
            out[key] = value
        return out

    def nonfinite(_):
        raise ContractError("non-finite JSON number")

    return json.loads(raw, object_pairs_hook=unique, parse_constant=nonfinite)


def validate_review_result(result: object) -> None:
    """Dependency-free enforcement of the live review boundary and score rule."""
    if not isinstance(result, dict):
        raise ContractError("review result must be an object")
    required = {
        "verdict",
        "anti_slop_score",
        "axes",
        "criticality",
        "findings",
        "blind_spots_surfaced",
        "summary",
    }
    if not required <= result.keys():
        raise ContractError("review result is missing required fields")
    if result.keys() - required - {"model_used", "codex_elapsed_sec"}:
        raise ContractError("review result has unknown fields")
    if result["verdict"] not in ("pass", "concerns", "fail"):
        raise ContractError("unknown review verdict")
    score, axes = result["anti_slop_score"], result["axes"]
    if type(score) is not int or not 0 <= score <= 10:
        raise ContractError("anti_slop_score must be an integer from 0 to 10")
    if (
        not isinstance(axes, list)
        or len(axes) != 5
        or any(type(a) is not int or not 0 <= a <= 2 for a in axes)
    ):
        raise ContractError("axes must contain five integers from 0 to 2")
    if sum(axes) != score:
        raise ContractError("anti_slop_score must equal the sum of axes")
    if result["verdict"] == "pass" and (score < 7 or 0 in axes):
        raise ContractError("pass requires score >=7 and no zero axis")
    if result["criticality"] not in ("high", "medium", "low") or not _text(
        result["summary"]
    ):
        raise ContractError("criticality and summary must be populated")
    spots = result["blind_spots_surfaced"]
    if not isinstance(spots, list) or any(not _text(s) for s in spots):
        raise ContractError("blind_spots_surfaced must be an array of text")
    if "model_used" in result and not _text(result["model_used"]):
        raise ContractError("model_used must be nonempty text")
    if "codex_elapsed_sec" in result:
        elapsed = result["codex_elapsed_sec"]
        if (
            type(elapsed) not in (int, float)
            or not math.isfinite(elapsed)
            or elapsed < 0
        ):
            raise ContractError("codex_elapsed_sec must be finite and nonnegative")
    if not isinstance(result["findings"], list):
        raise ContractError("findings must be an array")
    fields = {
        "severity",
        "category",
        "file",
        "line",
        "issue",
        "evidence",
        "fix_hint",
        "settling_observation",
    }
    for finding in result["findings"]:
        if not isinstance(finding, dict) or set(finding) != fields:
            raise ContractError("finding fields are incomplete or unknown")
        if finding["severity"] not in ("critical", "warning", "info"):
            raise ContractError("unknown finding severity")
        if finding["category"] not in (
            "security",
            "correctness",
            "design",
            "perf",
            "style",
            "tests",
        ):
            raise ContractError("unknown finding category")
        for key in ("issue", "evidence", "settling_observation"):
            if not _text(finding[key]):
                raise ContractError(f"finding.{key} must be nonempty text")
        for key in ("file", "fix_hint"):
            if finding[key] is not None and not isinstance(finding[key], str):
                raise ContractError(f"finding.{key} must be text or null")
        line = finding["line"]
        if line is not None and (type(line) is not int or line < 1):
            raise ContractError("finding.line must be a positive integer or null")


def _relations(name: str, data: object) -> None:
    if name == "claim-review":
        claims = {c["id"] for c in data["claims"]}
        if len(claims) != len(data["claims"]):
            raise ContractError("duplicate claim id")
        receipts = [data[r] for r in ("skeptic", "believer", "refuter")]
        if len({r["dispatch_id"] for r in receipts}) != 3:
            raise ContractError("three distinct dispatch receipts required")
        sources = set(data["primary_sources"])
        for receipt in receipts:
            if receipt["artifact_hash"] != data["artifact_hash"]:
                raise ContractError("review receipt artifact hash differs")
            if set(receipt["reviewed_claim_ids"]) != claims:
                raise ContractError("every receipt must cover all scoped claims")
            if not set(receipt["raw_sources_read"]) <= sources:
                raise ContractError("receipt names an unknown primary source")
            if any(f["claim_id"] not in claims for f in receipt["findings"]):
                raise ContractError("finding names an unknown claim")
        if set(data["refuter"]["raw_sources_read"]) != sources:
            raise ContractError("refuter must inspect every primary source")
    elif name == "capability-plan":
        if len({r["id"] for r in data}) != len(data):
            raise ContractError("duplicate operation id")


def validate(name: str, data: object) -> None:
    if name == "review-result":
        validate_review_result(data)
        return
    import jsonschema

    schema = loads((ROOT / "schemas" / f"{name}.v1.json").read_text())
    validator = jsonschema.Draft202012Validator(schema)
    errors = list(validator.iter_errors(data))
    if errors:
        # Paths and rule names suffice; values could contain private evidence.
        e = errors[0]
        raise ContractError(
            f"invalid {name} record at {list(e.absolute_path)} ({e.validator})"
        )
    _relations(name, data)


def install(workspace: Path) -> dict:
    """Install missing versioned schemas without replacing existing artifacts."""
    result = {"installed": [], "preserved": []}
    for name in CONTRACTS:
        source = ROOT / "schemas" / f"{name}.v1.json"
        loads(source.read_text())
        target = workspace / "schemas" / source.name
        if target.exists() or target.is_symlink():
            result["preserved"].append(source.name)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
            result["installed"].append(source.name)
    return result


def doctor(workspace: Path) -> dict:
    """Advisory diagnostics. Missing evidence/dependencies are inconclusive."""
    report = {
        "mode": "advisory",
        "contracts": {},
        "validator": "inconclusive",
        "companions": {},
    }
    for name in CONTRACTS:
        source = ROOT / "schemas" / f"{name}.v1.json"
        target = workspace / "schemas" / source.name
        try:
            current = loads(target.read_text())
            expected = loads(source.read_text())
            report["contracts"][name] = (
                "available" if current == expected else "diverged"
            )
        except (OSError, ValueError):
            report["contracts"][name] = "missing_or_unreadable"
    try:
        sample = {
            "claim": "fixture signal",
            "conditions": "fixture run",
            "observation_ref": "fixture:target",
            "positive_control": "pass",
            "control_ref": "fixture:control",
            "target_outcome": "not_detected",
            "falsification_criterion": "controlled null",
            "absence_refutes": True,
            "claim_verdict": "refuted",
        }
        validate("measurement", sample)  # positive control: valid record MUST pass
        try:
            validate("measurement", {**sample, "positive_control": "missing"})
        except ContractError:
            report["validator"] = "operational_on_fixtures"
    except (ImportError, OSError, ValueError):
        pass
    home = Path.home()
    for name, script in (
        ("cross-review", "review_packet.py"),
        ("handback", "capability_plan.py"),
        ("prove-the-negative", "verdict.py"),
    ):
        paths = [
            home / root / "skills" / name / "scripts" / script
            for root in (".agents", ".claude", ".codex")
        ]
        report["companions"][name] = (
            "present_version_unverified"
            if any(p.is_file() for p in paths)
            else "unavailable"
        )
    return report


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("contract", choices=(*CONTRACTS, "install", "doctor"))
    ap.add_argument("record", nargs="?", help="JSON path, or - for stdin")
    ap.add_argument("--workspace", type=Path, default=Path.cwd())
    args = ap.parse_args()
    try:
        if args.contract == "install":
            print(json.dumps(install(args.workspace)))
            return 0
        if args.contract == "doctor":
            print(json.dumps(doctor(args.workspace)))
            return 0
        if args.record is None:
            ap.error("a record path or - is required")
        raw = sys.stdin.read() if args.record == "-" else Path(args.record).read_text()
        data = loads(raw)
        validate(args.contract, data)
        result = {
            "valid": True,
            "contract": args.contract,
            "scope": "structure/references only; truth and authority unverified",
        }
        if args.contract == "measurement":
            result["claim_verdict"] = data["claim_verdict"]
        elif args.contract == "capability-plan":
            result["executable_now"] = [
                r["id"] for r in data if r["tier"] == "executable_now"
            ]
        print(json.dumps(result))
        return 0
    except ImportError:
        print(
            json.dumps(
                {
                    "valid": False,
                    "status": "inconclusive_dependency",
                    "reason": "jsonschema unavailable; validation is inconclusive",
                }
            )
        )
        return 2
    except (OSError, ValueError) as exc:
        print(json.dumps({"valid": False, "status": "invalid", "reason": str(exc)}))
        return 1


if __name__ == "__main__":
    sys.exit(main())
