#!/usr/bin/env python3
"""
bstack cross-review — P20 cross-model adversarial review for GitHub PRs.

Fixes BRO-1227: prior Cato sub-agent dispatches stalled within 6-7 tool
uses because they tried to read files from the local working tree, which
was on a different branch than the PR's head SHA.

Fix B (this implementation): always read from git via `gh`, never the
working tree. `gh pr diff` + `gh api repos/.../contents/<path>?ref=<sha>`
give us the exact files at the exact commit, with zero cwd dependence.
This script can be invoked from any cwd; only the --repo + PR number
matter.

Invocation:
  bstack cross-review <pr-num> --repo <owner/name> [opts]

Options:
  --repo <owner/name>     GitHub repo slug (required)
  --model <name>          Reviewer model (default: gpt-5.4 via codex CLI)
  --dry-run               Fetch diff + file contents, skip codex, print plan
  --no-codex              Build bundle, write to --out, skip codex invocation
  --out <path>            Where to write the structured JSON output
                          (default: <cwd>/.bstack-cross-review/<pr>.json)
  --post-comment          After review, post verdict to PR as a comment
  --timeout <sec>         Codex call timeout (default: 240)
  --quiet                 Suppress progress logs

Exit codes:
  0   review completed, verdict pass
  10  review completed, verdict concerns (NOT a script failure; CI may gate)
  20  review completed, verdict fail
  30  codex unavailable / skipped (treated as Rule 2a skipped)
  2   bad invocation / gh failure
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


def log(msg: str, quiet: bool = False) -> None:
    if not quiet:
        print(f"[cross-review] {msg}", file=sys.stderr)


def run_gh(args: list[str], *, json_out: bool = False, capture: bool = True) -> Any:
    """Run a `gh` subprocess. Returns stdout (parsed JSON if json_out)."""
    cmd = ["gh", *args]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=capture,
            text=True,
            check=True,
        )
    except FileNotFoundError:
        print("bstack cross-review: gh CLI not found in PATH", file=sys.stderr)
        sys.exit(2)
    except subprocess.CalledProcessError as exc:
        print(
            f"bstack cross-review: gh {' '.join(args)} failed (exit {exc.returncode})",
            file=sys.stderr,
        )
        if exc.stderr:
            print(exc.stderr, file=sys.stderr)
        sys.exit(2)
    if json_out:
        return json.loads(proc.stdout)
    return proc.stdout


def fetch_pr_metadata(repo: str, pr: int) -> dict[str, Any]:
    return run_gh(
        [
            "pr",
            "view",
            str(pr),
            "--repo",
            repo,
            "--json",
            "number,title,body,baseRefName,headRefName,headRefOid,state,mergeable,author,additions,deletions,changedFiles,files,url",
        ],
        json_out=True,
    )


def fetch_pr_diff(repo: str, pr: int) -> str:
    return run_gh(["pr", "diff", str(pr), "--repo", repo])


def fetch_file_at_sha(repo: str, path: str, sha: str) -> str | None:
    """Fetch file content at a specific commit via gh api. Returns None on 404."""
    try:
        proc = subprocess.run(
            [
                "gh",
                "api",
                f"repos/{repo}/contents/{path}?ref={sha}",
                "--jq",
                ".content",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
    except subprocess.CalledProcessError as exc:
        if "404" in (exc.stderr or ""):
            return None
        # Some other error — propagate as None and let bundle note it
        return None
    encoded = proc.stdout.strip().replace("\n", "")
    if not encoded:
        return None
    try:
        return base64.b64decode(encoded).decode("utf-8", errors="replace")
    except Exception:
        return None


def build_bundle(repo: str, pr: int, *, quiet: bool = False) -> dict[str, Any]:
    log(f"fetching PR #{pr} metadata from {repo}", quiet=quiet)
    meta = fetch_pr_metadata(repo, pr)
    log(
        f"PR #{pr} head={meta['headRefOid'][:8]} files={meta['changedFiles']} (+{meta['additions']}/-{meta['deletions']})",
        quiet=quiet,
    )

    log("fetching diff", quiet=quiet)
    diff = fetch_pr_diff(repo, pr)

    head_sha = meta["headRefOid"]
    files_section: list[dict[str, Any]] = []
    LOCK_PATTERNS = (
        "bun.lock",
        "package-lock.json",
        "yarn.lock",
        "pnpm-lock.yaml",
        "Cargo.lock",
        "uv.lock",
        "poetry.lock",
        "Pipfile.lock",
        "go.sum",
        "composer.lock",
    )
    for f in meta.get("files", []):
        path = f.get("path")
        if not path:
            continue
        # Skip lock files — diff has all the signal
        basename = path.rsplit("/", 1)[-1]
        if basename in LOCK_PATTERNS:
            files_section.append({"path": path, "skipped": True, "reason": "lock file"})
            continue
        # Skip large adds (>2000 LOC) — diff carries the signal
        additions = f.get("additions", 0)
        if additions > 2000:
            files_section.append(
                {"path": path, "skipped": True, "reason": f"large add ({additions} additions)"}
            )
            continue
        log(f"  fetching {path}@{head_sha[:8]}", quiet=quiet)
        content = fetch_file_at_sha(repo, path, head_sha)
        if content is None:
            files_section.append({"path": path, "content": None, "reason": "deleted or unreadable"})
        else:
            files_section.append({"path": path, "content": content})

    return {
        "repo": repo,
        "pr": pr,
        "url": meta.get("url"),
        "title": meta.get("title"),
        "body": meta.get("body"),
        "base_ref": meta.get("baseRefName"),
        "head_ref": meta.get("headRefName"),
        "head_sha": head_sha,
        "author": (meta.get("author") or {}).get("login"),
        "stats": {
            "additions": meta.get("additions"),
            "deletions": meta.get("deletions"),
            "changed_files": meta.get("changedFiles"),
        },
        "diff": diff,
        "files": files_section,
    }


REVIEWER_PROMPT = """You are Cato, a cross-vendor adversarial reviewer.
You are reviewing a GitHub pull request to surface security, correctness,
and design issues that a same-family (Anthropic) reviewer might miss.

Read the PR title, body, full diff, and the post-change content of each
changed file. Then emit a single JSON object on stdout with this schema:

{
  "verdict": "pass" | "concerns" | "fail",
  "anti_slop_score": 0-10,
  "criticality": "high" | "medium" | "low",
  "findings": [
    {
      "severity": "critical" | "warning" | "info",
      "category": "security" | "correctness" | "design" | "perf" | "style" | "tests",
      "file": "<path or null>",
      "line": "<int or null>",
      "issue": "one-sentence description",
      "evidence": "what in the code supports this",
      "fix_hint": "concrete suggestion or null"
    }
  ],
  "blind_spots_surfaced": ["..."],
  "summary": "2-3 sentence overall assessment"
}

Scoring rules (anti_slop_score, 0-10):
  - 10 = production-grade, all invariants explicit, defenses in depth, tests cover regressions
  -  7 = ships safely but has noted concerns
  -  4 = ship-blocking issues present (verdict must be "fail" or "concerns")
  -  0 = unsafe / broken

A verdict of "pass" requires score >= 7.
Be specific about file:line where possible. Do not invent issues.
Output ONLY the JSON object. No prose, no markdown fence.
"""


def build_codex_prompt(bundle: dict[str, Any]) -> str:
    parts: list[str] = [REVIEWER_PROMPT, "", "---", ""]
    parts.append(f"## PR #{bundle['pr']} — {bundle['title']}")
    parts.append(f"Repo: {bundle['repo']}")
    parts.append(f"Author: @{bundle.get('author')}")
    parts.append(f"Branch: {bundle['head_ref']} → {bundle['base_ref']}")
    parts.append(f"Head SHA: {bundle['head_sha']}")
    parts.append(
        f"Stats: +{bundle['stats']['additions']}/-{bundle['stats']['deletions']} across {bundle['stats']['changed_files']} files"
    )
    if bundle.get("body"):
        parts.append("")
        parts.append("### PR description")
        parts.append(bundle["body"])
    parts.append("")
    parts.append("### Diff")
    parts.append("```diff")
    parts.append(bundle["diff"])
    parts.append("```")
    parts.append("")
    parts.append("### Post-change file contents")
    for f in bundle["files"]:
        path = f["path"]
        if f.get("skipped"):
            parts.append(f"#### {path} — SKIPPED ({f['reason']})")
            parts.append("")
            continue
        content = f.get("content")
        if content is None:
            parts.append(f"#### {path} — UNREADABLE ({f.get('reason', 'unknown')})")
            parts.append("")
            continue
        parts.append(f"#### {path}")
        parts.append("```")
        # Trim individual files to 2000 lines as a safety bound
        lines = content.splitlines()
        if len(lines) > 2000:
            parts.append("\n".join(lines[:2000]))
            parts.append(f"... [truncated, {len(lines) - 2000} more lines]")
        else:
            parts.append(content)
        parts.append("```")
        parts.append("")
    parts.append("---")
    parts.append("")
    parts.append("Emit the JSON verdict now.")
    return "\n".join(parts)


def invoke_codex(prompt: str, model: str, timeout: int, quiet: bool = False) -> dict[str, Any]:
    if not shutil.which("codex"):
        log("codex CLI not found — emitting verdict=skipped", quiet=quiet)
        return {
            "verdict": "skipped",
            "reason": "codex CLI not installed; install from https://github.com/openai/codex",
        }
    log(f"invoking codex exec ({model}, timeout {timeout}s)", quiet=quiet)
    started = time.time()
    try:
        proc = subprocess.run(
            [
                "codex",
                "exec",
                "--sandbox",
                "read-only",
                "--model",
                model,
                "--skip-git-repo-check",
                prompt,
            ],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return {"verdict": "skipped", "reason": f"codex exec timed out after {timeout}s"}
    elapsed = time.time() - started
    log(f"codex exec returned in {elapsed:.1f}s (exit {proc.returncode})", quiet=quiet)
    if proc.returncode != 0:
        return {
            "verdict": "skipped",
            "reason": f"codex exec exited {proc.returncode}",
            "stderr_tail": (proc.stderr or "")[-500:],
        }

    out = proc.stdout
    # Codex may wrap JSON in prose / log lines — find the first { … } object.
    parsed = try_parse_json(out)
    if parsed is None:
        return {
            "verdict": "skipped",
            "reason": "codex output was not valid JSON",
            "stdout_tail": out[-1000:],
        }
    parsed["model_used"] = model
    parsed["codex_elapsed_sec"] = round(elapsed, 1)
    return parsed


def try_parse_json(text: str) -> dict[str, Any] | None:
    text = text.strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Fall back: extract first balanced { ... } object
    depth = 0
    start = -1
    in_string = False
    escape = False
    for i, ch in enumerate(text):
        if escape:
            escape = False
            continue
        if ch == "\\":
            escape = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start != -1:
                try:
                    return json.loads(text[start : i + 1])
                except json.JSONDecodeError:
                    start = -1
    return None


def format_verdict_md(result: dict[str, Any], bundle: dict[str, Any]) -> str:
    verdict = result.get("verdict", "skipped")
    score = result.get("anti_slop_score", "n/a")
    crit = result.get("criticality", "n/a")
    parts = [
        f"## bstack cross-review (P20) — PR #{bundle['pr']}",
        "",
        f"- **Verdict**: `{verdict}`",
        f"- **Anti-slop score**: `{score}/10`",
        f"- **Criticality**: `{crit}`",
        f"- **Reviewer model**: `{result.get('model_used', 'n/a')}`",
        f"- **Head SHA**: `{bundle['head_sha']}`",
        "",
    ]
    if result.get("summary"):
        parts.append(f"**Summary**: {result['summary']}")
        parts.append("")
    findings = result.get("findings") or []
    if findings:
        parts.append("### Findings")
        parts.append("")
        for i, f in enumerate(findings, 1):
            sev = f.get("severity", "info")
            cat = f.get("category", "misc")
            where = f.get("file") or "(no file)"
            line = f.get("line")
            loc = f"{where}:{line}" if line else where
            parts.append(f"{i}. **[{sev}/{cat}]** `{loc}` — {f.get('issue', '')}")
            if f.get("evidence"):
                parts.append(f"   - evidence: {f['evidence']}")
            if f.get("fix_hint"):
                parts.append(f"   - fix: {f['fix_hint']}")
            parts.append("")
    if result.get("blind_spots_surfaced"):
        parts.append("### Anthropic-family blind spots surfaced")
        for b in result["blind_spots_surfaced"]:
            parts.append(f"- {b}")
        parts.append("")
    if verdict == "skipped":
        parts.append(f"_Skipped: {result.get('reason', 'unknown')}_")
        parts.append("")
    parts.append("---")
    parts.append("_Generated by `bstack cross-review` (BRO-1227)._")
    return "\n".join(parts)


def exit_code_for_verdict(verdict: str) -> int:
    return {"pass": 0, "concerns": 10, "fail": 20, "skipped": 30}.get(verdict, 30)


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="bstack cross-review",
        description="P20 cross-model adversarial review of a GitHub PR via remote git fetch (Fix B for BRO-1227).",
    )
    parser.add_argument("pr", type=int, help="PR number")
    parser.add_argument("--repo", required=True, help="GitHub repo slug owner/name")
    parser.add_argument("--model", default="gpt-5.4", help="Reviewer model (default: gpt-5.4)")
    parser.add_argument("--dry-run", action="store_true", help="Fetch bundle, print plan, no codex")
    parser.add_argument("--no-codex", action="store_true", help="Build bundle + write, skip codex")
    parser.add_argument("--out", default=None, help="JSON output path")
    parser.add_argument("--post-comment", action="store_true", help="Post verdict to PR as comment")
    parser.add_argument("--timeout", type=int, default=240, help="Codex timeout seconds")
    parser.add_argument("--quiet", action="store_true", help="Suppress progress logs")
    args = parser.parse_args()

    bundle = build_bundle(args.repo, args.pr, quiet=args.quiet)

    out_path = (
        Path(args.out)
        if args.out
        else Path.cwd() / ".bstack-cross-review" / f"{args.pr}.json"
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)

    if args.dry_run:
        plan = {
            "pr": bundle["pr"],
            "head_sha": bundle["head_sha"],
            "files_fetched": [
                {"path": f["path"], "skipped": bool(f.get("skipped")), "bytes": len((f.get("content") or ""))}
                for f in bundle["files"]
            ],
            "diff_bytes": len(bundle["diff"]),
            "would_invoke_codex_with_model": args.model,
            "out_path": str(out_path),
        }
        json.dump(plan, sys.stdout, indent=2)
        print()
        return 0

    if args.no_codex:
        out_path.write_text(json.dumps(bundle, indent=2))
        log(f"bundle written to {out_path}", quiet=args.quiet)
        return 0

    prompt = build_codex_prompt(bundle)
    result = invoke_codex(prompt, args.model, args.timeout, quiet=args.quiet)
    out_payload = {
        "pr": bundle["pr"],
        "repo": bundle["repo"],
        "head_sha": bundle["head_sha"],
        "url": bundle["url"],
        "title": bundle["title"],
        "reviewed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "result": result,
    }
    out_path.write_text(json.dumps(out_payload, indent=2))
    log(f"verdict={result.get('verdict')} score={result.get('anti_slop_score', 'n/a')} → {out_path}", quiet=args.quiet)

    md = format_verdict_md(result, bundle)
    md_path = out_path.with_suffix(".md")
    md_path.write_text(md)

    if args.post_comment:
        log(f"posting verdict to {bundle['url']}", quiet=args.quiet)
        try:
            subprocess.run(
                ["gh", "pr", "comment", str(args.pr), "--repo", args.repo, "--body-file", str(md_path)],
                check=True,
            )
        except subprocess.CalledProcessError as exc:
            log(f"failed to post comment: {exc}", quiet=args.quiet)

    # Echo summary to stdout
    print(json.dumps(result, indent=2))
    return exit_code_for_verdict(result.get("verdict", "skipped"))


if __name__ == "__main__":
    sys.exit(main())
