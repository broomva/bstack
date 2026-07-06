#!/usr/bin/env python3
"""
leverage-ship-sensor.py — the EXOGENOUS ship-signal for the bstack leverage loop.

BRO-1707. The transcript sensor (leverage-sensor.py) measures m6 as the ratio of
meta- vs product-EDITING sessions, classified by edited file path. Two weaknesses:
  (a) gameable — one product-path edit flips a whole session to "product";
  (b) proxy — an edit is not a merge; it does not know if CI passed / it shipped.

This sensor re-bases the meta/product signal on MERGED PULL REQUESTS instead of raw
edits, addressing both:
  - unit of account = ONE merged PR (not per-line) → kills size-padding WITHIN a PR;
  - classification = FRACTIONAL by diff file-share across product/meta path families
    (not winner-take-all) → one product file in a 9-file meta PR moves 0.1, not 1.0;
    (KNOWN LIMIT, tracked on BRO-1709: because the unit is the PR and PR-count is
    agent-controlled, splitting one pure-product PR into N still multiplies its ship
    credit — so PR-split inflation is NOT closed here; the enforced-gate promotion adds
    per-surface normalization + branch protection to make each merge cost real work;)
  - only agent-authored PRs count (author allowlist, is_bot excluded, teammate/upstream
    repos omitted from the repo allowlist) → the signal is the AGENT'S leverage, not
    the team's (h ⟂ U preserved).

Exogeneity caveat (deliberate, see BRO-1709): "CI-green" is only as exogenous as the
gate is ENFORCED. On self-owned repos where main is unprotected and the agent authors
.github/workflows/*, green is agent-influenceable. So this sensor (1) caps a PR that
modified its OWN CI definition at the ungated tier, and (2) is SHADOW-only — the main
sensor merges the resulting ratio as a NON-ACTUATING metric until branch protection +
required checks land (BRO-1709), at which point the ratio is promoted to actuate.

Signals used are structural (merge event, author.login/is_bot, file paths,
statusCheckRollup conclusion) — never agent-set PR labels or commit subjects.

Outputs:
  write  : <workspace>/.control/leverage-ship-state.json   (latest snapshot, shadow)
  stdout : human summary (default) | --json

Usage:
  leverage-ship-sensor.py [--workspace DIR] [--window N] [--json] [--no-store]
  leverage-ship-sensor.py --fixture prs.json [--json]   # hermetic: classify a PR list
"""
import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone

# Defaults mirror leverage-sensor.py's PRODUCT_EDIT_RE / META_EDIT_RE so the two
# sensors classify the same universe of paths. Overridable via setpoints.ship_signal.
DEFAULT_PRODUCT = r"/(apps|core|work|freelance|crm|packages|services)/"
DEFAULT_META = (
    r"/(research|docs|\.control|\.claude|skills|scripts|bstack)/|"
    r"/(CLAUDE|AGENTS|METALAYER)\.md$|/Makefile$"
)
# A PR that modifies its own CI definition cannot be granted full green credit — the
# gate it "passed" is one it just authored (h ∈ U). Capped at the ungated tier. (Note:
# a per-PR cap does NOT stop the weaken-then-harvest temporal attack — that needs the
# enforced branch protection + workflow attestation of BRO-1709.)
CI_DEF_RE = re.compile(r"/\.github/(workflows|actions)/", re.IGNORECASE)
# statusCheckRollup signals that mean a check did NOT pass. CheckRun reports via
# `.conclusion`, StatusContext (Vercel/external CI) via `.state` — we read both.
FAIL_SIGNALS = {"FAILURE", "CANCELLED", "TIMED_OUT", "ACTION_REQUIRED",
                "STARTUP_FAILURE", "ERROR", "STALE"}
# Not-yet-complete → NOT green (a PR merged before its gate finished).
PENDING_SIGNALS = {"PENDING", "EXPECTED", "IN_PROGRESS", "QUEUED", "WAITING"}


def resolve_workspace(arg=None):
    if arg:
        return os.path.abspath(arg)
    env = os.environ.get("BSTACK_WORKSPACE") or os.environ.get("BROOMVA_WORKSPACE")
    if env:
        return os.path.abspath(env)
    try:
        top = subprocess.run(["git", "rev-parse", "--show-toplevel"],
                             capture_output=True, text=True, timeout=5).stdout.strip()
        if top:
            return top
    except Exception:
        pass
    return os.getcwd()


def load_setpoints(path):
    try:
        import yaml
        with open(path) as f:
            return yaml.safe_load(f) or {}
    except Exception as e:
        print(f"[ship-sensor] WARN could not load setpoints ({path}): {e}", file=sys.stderr)
        return {}


def classify_prs(prs, cfg):
    """PURE. Classify a list of PR dicts into meta/product ship-units.

    Each PR dict: {number, mergedAt, author:{login,is_bot}, files:[{path}],
                   statusCheckRollup:[{conclusion}], _repo?}
    Returns {"m6s_meta_work_ship_ratio": float|None, "raw": {...}}.
    """
    author_allow = {str(a).lower() for a in cfg.get("author_allowlist", [])}
    prod_re = re.compile(cfg.get("product_paths", DEFAULT_PRODUCT), re.IGNORECASE)
    meta_re = re.compile(cfg.get("meta_paths", DEFAULT_META), re.IGNORECASE)
    tw = cfg.get("tier_weights", {}) or {}
    w_green = float(tw.get("green", 1.0))
    w_ungated = float(tw.get("ungated", 0.5))

    # FAIL CLOSED: the author allowlist is the h ⟂ U-critical filter. A missing/mistyped
    # `author_allowlist` must NOT silently count every author (teammates, upstream) — that
    # would poison the governor with the team's throughput. No allowlist → null signal.
    if not author_allow:
        return {"m6s_meta_work_ship_ratio": None,
                "raw": {"pr_count_counted": 0, "product_ship": 0.0, "meta_ship": 0.0,
                        "excluded": {"bot": 0, "author": len(prs), "unclassifiable": 0},
                        "details": [], "note": "no author_allowlist — failing closed (h ⟂ U)"}}

    product_ship = meta_ship = 0.0
    counted = 0
    excluded = {"bot": 0, "author": 0, "unclassifiable": 0}
    details = []
    for pr in prs:
        author = pr.get("author") or {}
        login = str(author.get("login", "")).lower()
        if author.get("is_bot"):
            excluded["bot"] += 1
            continue
        # Only the agent's own merged PRs count, never a teammate's throughput.
        if login not in author_allow:
            excluded["author"] += 1
            continue
        # gh returns REPO-RELATIVE paths (apps/x.ts, scripts/y.sh, CLAUDE.md); the
        # shared path regexes were written for ABSOLUTE tool_use paths and anchor on
        # surrounding slashes, so normalize each to a leading-slash form before matching
        # (else "apps/x.ts" fails "/(apps|…)/" and everything reads unclassifiable).
        files = ["/" + str(f.get("path", "")).lstrip("/") for f in (pr.get("files") or [])]
        # Mutually exclusive, PRODUCT-precedence (mirrors leverage-sensor.py's session
        # rule: `if product elif meta`). Otherwise a path matching BOTH families (e.g.
        # apps/x/docs/y.md) double-counts, tot > len(files), and shares don't sum to 1.
        pf = mf = 0
        for f in files:
            if prod_re.search(f):
                pf += 1
            elif meta_re.search(f):
                mf += 1
        tot = pf + mf
        if tot == 0:
            # nothing classifiable (e.g. a PR touching only .github/ or root configs)
            excluded["unclassifiable"] += 1
            continue
        # ship gate — exogenous: green only if at least one completed check exists, none
        # failed, and none is still pending, AND the PR did not modify its own CI. Read
        # BOTH CheckRun `.conclusion` and StatusContext `.state` (Vercel/external CI report
        # only the latter — reading conclusion alone false-greens a failed status check).
        roll = pr.get("statusCheckRollup") or []
        concls = [str(c.get("conclusion", "")).upper() for c in roll
                  if isinstance(c, dict) and c.get("conclusion")]
        states = [str(c.get("state", "")).upper() for c in roll
                  if isinstance(c, dict) and c.get("state") and not c.get("conclusion")]
        signals = concls + states
        green = (bool(signals)
                 and not any(s in FAIL_SIGNALS for s in signals)
                 and not any(s in PENDING_SIGNALS for s in signals))
        touched_ci = any(CI_DEF_RE.search(f) for f in files)
        gate = w_green if (green and not touched_ci) else w_ungated
        # unit-weighted: the PR contributes `gate` total, split by file-share. A
        # 1-file and a 50-file product PR both contribute `gate` → size-padding is
        # inert; a mixed PR splits fractionally → the single-file flip is dead.
        pshare, mshare = pf / tot, mf / tot
        product_ship += gate * pshare
        meta_ship += gate * mshare
        counted += 1
        details.append({
            "number": pr.get("number"), "repo": pr.get("_repo"),
            "gate": round(gate, 2), "p": round(pshare, 2), "m": round(mshare, 2),
            "green": green, "touched_ci": touched_ci,
        })
    denom = product_ship + meta_ship
    m6s = round(meta_ship / denom, 4) if denom > 0 else None
    return {
        "m6s_meta_work_ship_ratio": m6s,
        "raw": {
            "pr_count_counted": counted,
            "product_ship": round(product_ship, 3),
            "meta_ship": round(meta_ship, 3),
            "excluded": excluded,
            "details": details[:50],
        },
    }


def fetch_merged_prs(repo, since_iso, timeout=25):
    """One `gh pr list` per repo → all fields needed. Returns None on ANY failure
    (unauth / offline / timeout / unresolvable repo) so the caller degrades gracefully —
    a None repo is skipped and flagged in errors, never fabricated as an empty (which
    would read as a reachable 0-PR repo).

    `--search merged:>=DATE` bounds the fetch server-side (fetching files+CI for every
    merged PR of a busy repo otherwise times out). The one gap it leaves: the search API
    returns rc 0 + empty for a NONEXISTENT repo, hiding a deleted/renamed allowlist entry
    (e.g. the 404'd arcan-glass) as a silent 0 — so on an EMPTY result we probe repo
    existence to tell '0 PRs in window' apart from 'unreachable'. Offline/unauth still
    surface as rc != 0 directly. Window is re-applied client-side by _in_window()."""
    try:
        out = subprocess.run(
            ["gh", "pr", "list", "-R", repo, "--state", "merged", "--limit", "200",
             "--search", f"merged:>={since_iso}",
             "--json", "number,mergedAt,author,files,statusCheckRollup"],
            capture_output=True, text=True, timeout=timeout)
        if out.returncode != 0:
            return None
        data = json.loads(out.stdout or "[]")
        if not data and not _repo_exists(repo, timeout=10):
            return None  # empty because the repo is unreachable, not because 0 PRs
        for pr in data:
            pr["_repo"] = repo
        return data
    except Exception:
        return None


def _repo_exists(repo, timeout=10):
    """Cheap resolve check (rc 1 for a nonexistent/renamed repo). Called only when a
    PR search returns empty, to distinguish a reachable 0-PR repo from a dead one."""
    try:
        r = subprocess.run(["gh", "repo", "view", repo, "--json", "name"],
                           capture_output=True, text=True, timeout=timeout)
        return r.returncode == 0
    except Exception:
        return False


def compute(cfg, window_days, now=None):
    """Scan the repo allowlist and classify. Returns (record_fields, gh_ok)."""
    now = now or datetime.now(timezone.utc)
    since_iso = (now - timedelta(days=window_days)).strftime("%Y-%m-%d")
    repos = cfg.get("repos", []) or []
    all_prs, per_repo, errors = [], [], []
    for repo in repos:
        prs = fetch_merged_prs(repo, since_iso)
        if prs is None:
            errors.append(repo)
            continue
        # belt+suspenders: filter client-side by mergedAt (the --search + --limit 200
        # cap can otherwise let an old PR slip in on a very active repo).
        cutoff = (now - timedelta(days=window_days))
        kept = [p for p in prs if _in_window(p.get("mergedAt"), cutoff)]
        all_prs.extend(kept)
        per_repo.append({"repo": repo, "prs": len(kept)})
    result = classify_prs(all_prs, cfg)
    result["raw"]["per_repo"] = per_repo
    result["raw"]["errors"] = errors
    # gh_ok if at least one allowlisted repo was reachable; all-fail → signal is null
    gh_ok = bool(repos) and len(errors) < len(repos)
    if not gh_ok:
        result["m6s_meta_work_ship_ratio"] = None
    return result, gh_ok


def _in_window(merged_at, cutoff):
    if not merged_at:
        return False
    try:
        ts = datetime.fromisoformat(str(merged_at).replace("Z", "+00:00"))
        return ts >= cutoff
    except Exception:
        return True  # keep on parse failure rather than silently drop


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workspace", default=None)
    ap.add_argument("--window", type=int, default=None)
    ap.add_argument("--fixture", default=None, help="classify a PR-list JSON file (hermetic test)")
    ap.add_argument("--config-json", default=None, help="inline ship_signal config as JSON (hermetic test)")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--no-store", action="store_true")
    ap.add_argument("--throttle", type=int, default=0,
                    help="skip recompute if ship-state younger than N sec (SessionStart wire)")
    args = ap.parse_args()

    workspace = resolve_workspace(args.workspace)

    if args.throttle and not args.fixture:
        try:
            st = json.load(open(os.path.join(workspace, ".control", "leverage-ship-state.json")))
            if time.time() - datetime.fromisoformat(st["measured_at"]).timestamp() < args.throttle:
                return
        except Exception:
            pass
    if args.config_json:
        cfg = json.loads(args.config_json)
        window = args.window if args.window is not None else 7
    else:
        setpoints_path = os.path.join(workspace, ".control", "leverage-setpoints.yaml")
        setpoints = load_setpoints(setpoints_path)
        cfg = setpoints.get("ship_signal", {}) or {}
        window = args.window if args.window is not None else setpoints.get("window_days", 7)

    if args.fixture:
        prs = json.load(open(args.fixture))
        result = classify_prs(prs, cfg)
        gh_ok = True
        repos = ["<fixture>"]
    else:
        result, gh_ok = compute(cfg, window)
        repos = cfg.get("repos", [])

    record = {
        "measured_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "window_days": window,
        "shadow": bool(cfg.get("shadow", True)),
        "gh_ok": gh_ok,
        "repos": repos,
        "m6s_meta_work_ship_ratio": result["m6s_meta_work_ship_ratio"],
        "raw": result["raw"],
    }

    if not args.no_store and not args.fixture:
        state_file = os.path.join(workspace, ".control", "leverage-ship-state.json")
        d = os.path.dirname(state_file)
        os.makedirs(d, exist_ok=True)
        # atomic swap — the background SessionStart writer must never expose a truncated
        # file to the main sensor's Stop-time read (merge_ship_shadow json.load).
        fd, tmp = tempfile.mkstemp(dir=d, prefix=".ship-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(record, f, indent=2)
            os.replace(tmp, state_file)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    if args.json:
        print(json.dumps(record, indent=2))
    else:
        r = record["m6s_meta_work_ship_ratio"]
        raw = record["raw"]
        print(f"[ship-signal SHADOW] m6s_meta_work_ship_ratio = {r}  "
              f"(gh_ok={gh_ok}, {raw.get('pr_count_counted', 0)} PRs; "
              f"product_ship={raw.get('product_ship')}, meta_ship={raw.get('meta_ship')})")
        if raw.get("errors"):
            print(f"  unreachable repos (degraded): {raw['errors']}")
        print("  NON-ACTUATING until BRO-1709 (branch protection) promotes it.")


if __name__ == "__main__":
    main()
