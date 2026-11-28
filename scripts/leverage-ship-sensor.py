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
import math
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


def _import_yaml():
    """PyYAML, imported without letting the caller's directories in (BRO-2652).

    The hooks start this file with `python3 -I`: no cwd, script dir, PYTHONPATH or
    user site-packages on sys.path, so a json.py or yaml.py in the session's repo
    cannot replace a module. -I also hides a PyYAML installed with `pip install
    --user`, so on ImportError the user site is APPENDED (never inserted first): the
    standard library and the interpreter's own site-packages still win. If PyYAML is
    still missing, ImportError propagates to load_setpoints, which reports it."""
    try:
        import yaml
        return yaml
    except ImportError:
        import site
        user_site = site.getusersitepackages()
        if isinstance(user_site, str) and os.path.isdir(user_site) and user_site not in sys.path:
            sys.path.append(user_site)
        import yaml
        return yaml


def load_setpoints(path):
    try:
        yaml = _import_yaml()
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
    cfg = cfg if isinstance(cfg, dict) else {}
    prs = prs if isinstance(prs, list) else []
    author_allow = set(_norm_str_list(cfg.get("author_allowlist")))
    prod_pat = cfg.get("product_paths") if isinstance(cfg.get("product_paths"), str) and cfg.get("product_paths") else DEFAULT_PRODUCT
    meta_pat = cfg.get("meta_paths") if isinstance(cfg.get("meta_paths"), str) and cfg.get("meta_paths") else DEFAULT_META
    prod_re = re.compile(prod_pat, re.IGNORECASE)
    meta_re = re.compile(meta_pat, re.IGNORECASE)
    tw = cfg.get("tier_weights") if isinstance(cfg.get("tier_weights"), dict) else {}
    w_green = _safe_pos_float(tw.get("green"), 1.0)
    w_ungated = _safe_nonneg_float(tw.get("ungated"), 0.5)

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
        if not isinstance(pr, dict):
            excluded["unclassifiable"] += 1
            continue
        author = pr.get("author") if isinstance(pr.get("author"), dict) else {}
        login = str(author.get("login", "")).strip().lower()
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
        raw_files = pr.get("files") if isinstance(pr.get("files"), list) else []
        files = ["/" + str(f.get("path", "")).lstrip("/") for f in raw_files if isinstance(f, dict)]
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
        if not isinstance(data, list) or not all(isinstance(pr, dict) for pr in data):
            return None
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
    cfg = cfg if isinstance(cfg, dict) else {}
    window_days = _safe_window_days(window_days)
    since_iso = (now - timedelta(days=window_days)).strftime("%Y-%m-%d")
    repos = _str_list(cfg.get("repos"))
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


# ── m7 outward_artifact_floor (BRO-3955) ──────────────────────────────────────
# A FLOOR, not a ratio: did anything reach the world in the last window? One merged
# product PR by an allowlisted author, or one door receipt, satisfies it. Meta volume
# cannot violate it; only a window of silence toward the world fires it. Product repos
# come from setpoints `fleet.product_repos` (the same list the fleet sensor uses to
# classify m6), so "product" means one thing across the loop.
DEFAULT_DOOR_RECEIPTS = "~/.config/broomva/door/receipts.jsonl"
RECEIPT_TS_KEYS = ("ts", "timestamp", "at", "created_at", "logged_at")
CACHE_SCHEMA_VERSION = 3
MAX_WINDOW_DAYS = 3650
MAX_FUTURE_SKEW_SEC = 300


M7_QUERY_LIMIT = 1000


def fetch_merged_light(repo, since_iso, timeout=25):
    """Merged PRs with only the fields m7 needs. The m6s query asks for files and CI
    rollups and caps at 200, which a busy product repo (200+ merges a week) exceeds.
    Returns None on any failure or malformed JSON shape, never an empty list that
    would read as 0 merges."""
    try:
        out = subprocess.run(
            ["gh", "pr", "list", "-R", repo, "--state", "merged", "--limit", str(M7_QUERY_LIMIT),
             "--search", f"merged:>={since_iso}", "--json", "number,mergedAt,author"],
            capture_output=True, text=True, timeout=timeout)
        if out.returncode != 0:
            return None
        data = json.loads(out.stdout or "[]")
        if not isinstance(data, list) or not all(isinstance(item, dict) for item in data):
            return None
        if not data and not _repo_exists(repo, timeout=10):
            return None
        return data
    except Exception:
        return None


def _parse_ts(v):
    """A timestamp as an aware datetime, or None. Accepts epoch seconds (int, float
    or numeric string) and ISO 8601; an ISO time with no zone is read as UTC.
    Strict on purpose: m7 is a floor, and a timestamp that cannot be placed must not
    be counted as inside the window (that would hold the floor at 1.0 forever)."""
    if isinstance(v, bool) or v is None:
        return None
    try:
        if isinstance(v, (int, float)) or (isinstance(v, str) and v.strip().replace(".", "", 1).isdigit()):
            return datetime.fromtimestamp(float(v), tz=timezone.utc)
        d = datetime.fromisoformat(str(v).strip().replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except (ValueError, OverflowError, OSError):
        return None


def _safe_window_days(val, default=7):
    if isinstance(val, (int, float)) and not isinstance(val, bool):
        try:
            f = float(val)
        except OverflowError:
            return default
        if math.isfinite(f) and 0 < f <= MAX_WINDOW_DAYS:
            return f if isinstance(val, float) else int(val)
    return default


def _safe_pos_float(val, default):
    if isinstance(val, (int, float)) and not isinstance(val, bool):
        try:
            f = float(val)
        except OverflowError:
            return float(default)
        if math.isfinite(f) and f > 0:
            return f
    return float(default)


def _safe_nonneg_float(val, default):
    if isinstance(val, (int, float)) and not isinstance(val, bool):
        try:
            f = float(val)
        except OverflowError:
            return float(default)
        if math.isfinite(f) and f >= 0:
            return f
    return float(default)


def _str_list(vals):
    if not isinstance(vals, (list, tuple)):
        return []
    return [str(v).strip() for v in vals
            if isinstance(v, (str, int, float)) and not isinstance(v, bool) and str(v).strip()]


def _norm_str_list(vals):
    return sorted({s.lower() for s in _str_list(vals)})


def _resolve_door_receipts(path, workspace=None):
    raw_path = path.strip() if isinstance(path, str) else ""
    p = os.path.expanduser(raw_path or DEFAULT_DOOR_RECEIPTS)
    if not os.path.isabs(p) and workspace:
        p = os.path.join(workspace, p)
    return os.path.normpath(os.path.abspath(p))


def _repo_state_path(name, workspace, legacy_filename):
    """Resolve a repo-scoped path via broomva_paths; never swallow LayoutError."""
    import importlib.util
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "broomva_paths.py")
    try:
        spec = importlib.util.spec_from_file_location("broomva_paths", p)
        if spec is None or spec.loader is None:
            raise ImportError(p)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    except (ImportError, FileNotFoundError):
        return os.path.join(workspace, ".control", legacy_filename)
    return str(mod.repo_path(name, workspace))


def count_receipts(path, cutoff, now=None, workspace=None):
    """(count in window, unparsed lines, status, latest in-window time). status is 'absent' when the file
    does not exist on this host (FileNotFoundError) -- a door logged on another machine is not seen, and
    saying so is the point -- and 'unreadable' on any other OSError (e.g. PermissionError on an unsearchable
    parent dir, IsADirectoryError, NotADirectoryError)."""
    now = now or datetime.now(timezone.utc)
    p = _resolve_door_receipts(path, workspace=workspace)
    n = unparsed = 0
    latest = None
    try:
        with open(p, errors="ignore") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    o = json.loads(line)
                except Exception:
                    unparsed += 1
                    continue
                ts = next((o[k] for k in RECEIPT_TS_KEYS if o.get(k) is not None), None) \
                    if isinstance(o, dict) else None
                d = _parse_ts(ts)
                if d is None or d > now:
                    unparsed += 1
                elif d >= cutoff:
                    n += 1
                    latest = d if latest is None or d > latest else latest
    except FileNotFoundError:
        return 0, 0, "absent", None
    except OSError:
        return 0, 0, "unreadable", None
    return n, unparsed, "read", latest


def compute_outward(fleet_cfg, window_days, now=None, fetch=None, workspace=None):
    """PURE given `fetch`. Returns {"m7_outward_artifact_floor": 1.0|0.0|None, "outward": raw}.

    1.0  at least one counted merge or receipt in the window.
    0.0  every product repo was reachable, none had a counted merge, no receipt, and no
         qualifying merge/receipt had an unparseable timestamp or malformed payload.
    None not measured: no product repos or author allowlist configured, or a source
         was unreadable/malformed/capped/unparseable while nothing was counted (an empty
         window cannot be certified from a partial read -- the alarm would fire on an outage)."""
    now = now or datetime.now(timezone.utc)
    fetch = fetch or fetch_merged_light
    window_days = _safe_window_days(window_days)
    cfg = fleet_cfg if isinstance(fleet_cfg, dict) else {}
    repos = _str_list(cfg.get("product_repos"))
    authors = set(_norm_str_list(cfg.get("author_allowlist")))
    cutoff = now - timedelta(days=window_days)
    since_iso = cutoff.strftime("%Y-%m-%d")
    raw = {"window_days": window_days, "repos": repos, "per_repo": {}, "errors": [], "capped": [],
           "unparsed_merges": 0, "merged_product_prs": 0, "door_receipts": 0, "door_receipts_status": None,
           "latest_artifact_at": None}
    latest = None
    if not repos or not authors:
        raw["note"] = "fleet.product_repos and fleet.author_allowlist must both be set — m7 not measured"
        return {"m7_outward_artifact_floor": None, "outward": raw}
    for repo in repos:
        prs = fetch(repo, since_iso)
        if not isinstance(prs, list):
            raw["errors"].append(repo)
            continue
        if len(prs) >= M7_QUERY_LIMIT:
            # A result at the cap may have dropped the one merge that counts: it can
            # prove a shipment but never that there was none.
            raw["capped"].append(repo)
        n = 0
        malformed = False
        for pr in prs:
            if not isinstance(pr, dict):
                malformed = True
                continue
            author = pr.get("author")
            if not isinstance(author, dict):
                malformed = True
                continue
            is_bot = author.get("is_bot", False)
            login = author.get("login")
            if is_bot is True:
                continue
            if not isinstance(is_bot, bool) or not isinstance(login, str) or not login.strip():
                malformed = True
                continue
            if login.strip().lower() not in authors:
                continue
            d = _parse_ts(pr.get("mergedAt"))
            if d is None or d > now:
                raw["unparsed_merges"] += 1
                continue
            if cutoff <= d:
                n += 1
                latest = d if latest is None or d > latest else latest
        if malformed:
            raw["errors"].append(repo)
        raw["per_repo"][repo] = n
        raw["merged_product_prs"] += n
    (raw["door_receipts"], raw["door_receipts_unparsed"],
     raw["door_receipts_status"], r_latest) = count_receipts(
        cfg.get("door_receipts"), cutoff, now, workspace=workspace)
    if r_latest is not None and (latest is None or r_latest > latest):
        latest = r_latest
    # When the newest counted artifact landed. A 1.0 stays true until this ages out
    # of the window, without any refresh; the main sensor ages it at read time.
    raw["latest_artifact_at"] = latest.isoformat(timespec="seconds") if latest else None
    # Proving SOMETHING shipped needs one hit; proving NOTHING did needs every source
    # read and every qualifying merge/receipt placed in time.
    raw["complete_negative_coverage"] = (not raw["errors"] and not raw["capped"]
                                         and not raw["unparsed_merges"]
                                         and raw["door_receipts_status"] != "unreadable"
                                         and not raw["door_receipts_unparsed"])
    shipped = raw["merged_product_prs"] + raw["door_receipts"]
    if shipped > 0:
        v = 1.0
    elif raw["errors"]:
        v = None
        raw["note"] = "nothing counted and some product repos were unreachable — not certified either way"
    elif raw["door_receipts_status"] == "unreadable":
        v = None
        raw["note"] = "no merges counted and the receipts file is unreadable — not certified either way"
    elif raw["capped"]:
        v = None
        raw["note"] = "nothing counted but a product repo's query hit its cap — not certified either way"
    elif raw["unparsed_merges"]:
        v = None
        raw["note"] = "nothing counted and some qualifying PR merges had invalid timestamps — not certified either way"
    elif raw["door_receipts_unparsed"]:
        v = None
        raw["note"] = "nothing counted and some receipt lines could not be placed in time — not certified either way"
    else:
        v = 0.0
    return {"m7_outward_artifact_floor": v, "outward": raw}


def _in_window(merged_at, cutoff):
    if not merged_at:
        return False
    try:
        ts = datetime.fromisoformat(str(merged_at).replace("Z", "+00:00"))
        return ts >= cutoff
    except Exception:
        return True  # keep on parse failure rather than silently drop


def ship_config_signature(cfg, window):
    """Canonical ship_signal config for m6s cache matching."""
    c = cfg if isinstance(cfg, dict) else {}
    tw = c.get("tier_weights") if isinstance(c.get("tier_weights"), dict) else {}
    return {
        "schema_version": CACHE_SCHEMA_VERSION,
        "window_days": float(_safe_window_days(window)),
        "repos": _norm_str_list(c.get("repos")),
        "author_allowlist": _norm_str_list(c.get("author_allowlist")),
        "product_paths": str(c.get("product_paths") or DEFAULT_PRODUCT),
        "meta_paths": str(c.get("meta_paths") or DEFAULT_META),
        "tier_weights": {
            "green": _safe_pos_float(tw.get("green"), 1.0),
            "ungated": _safe_nonneg_float(tw.get("ungated"), 0.5),
        },
    }


def outward_config_signature(fleet_cfg, window, workspace=None):
    """Canonical fleet outward config for m7 cache matching."""
    c = fleet_cfg if isinstance(fleet_cfg, dict) else {}
    return {
        "schema_version": CACHE_SCHEMA_VERSION,
        "window_days": float(_safe_window_days(window)),
        "product_repos": _norm_str_list(c.get("product_repos")),
        "author_allowlist": _norm_str_list(c.get("author_allowlist")),
        "door_receipts": _resolve_door_receipts(c.get("door_receipts"), workspace=workspace),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workspace", default=None)
    ap.add_argument("--window", type=int, default=None)
    ap.add_argument("--fixture", default=None, help="classify a PR-list JSON file (hermetic test)")
    ap.add_argument("--config-json", default=None, help="inline ship_signal config as JSON (hermetic test)")
    ap.add_argument("--outward-fixture", default=None,
                    help="m7 from a {repo: [PR, ...]} JSON file instead of gh (hermetic test)")
    ap.add_argument("--fleet-config-json", default=None,
                    help="inline setpoints `fleet` block as JSON (hermetic test)")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--no-store", action="store_true")
    ap.add_argument("--throttle", type=int, default=0,
                    help="skip recompute if ship-state younger than N sec (SessionStart wire)")
    args = ap.parse_args()

    workspace = resolve_workspace(args.workspace)
    ship_state_file = _repo_state_path("leverage_ship_state", workspace, "leverage-ship-state.json")

    fleet_cfg = {}
    if args.config_json:
        cfg = json.loads(args.config_json)
        window = _safe_window_days(args.window)
    else:
        setpoints_path = _repo_state_path("leverage_setpoints", workspace, "leverage-setpoints.yaml")
        setpoints = load_setpoints(setpoints_path)
        sp_dict = setpoints if isinstance(setpoints, dict) else {}
        cfg = sp_dict.get("ship_signal") if isinstance(sp_dict.get("ship_signal"), dict) else {}
        fleet_cfg = sp_dict.get("fleet") if isinstance(sp_dict.get("fleet"), dict) else {}
        window = _safe_window_days(args.window if args.window is not None else sp_dict.get("window_days"))
    if args.fleet_config_json:
        fleet_cfg = json.loads(args.fleet_config_json)
    cfg = cfg if isinstance(cfg, dict) else {}
    fleet_cfg = fleet_cfg if isinstance(fleet_cfg, dict) else {}

    ship_sig = ship_config_signature(cfg, window)
    outward_sig = outward_config_signature(fleet_cfg, window, workspace=workspace)

    if args.throttle and not args.fixture and not args.outward_fixture:
        try:
            with open(ship_state_file) as f:
                st = json.load(f)
            age = time.time() - datetime.fromisoformat(st["measured_at"]).timestamp()
            if (-MAX_FUTURE_SKEW_SEC <= age < args.throttle
                    and st.get("ship_config") == ship_sig
                    and st.get("outward_config") == outward_sig):
                return
        except Exception:
            pass

    if args.fixture:
        prs = json.load(open(args.fixture))
        result = classify_prs(prs, cfg)
        gh_ok = True
        repos = ["<fixture>"]
    elif args.outward_fixture:
        result, gh_ok, repos = {"m6s_meta_work_ship_ratio": None, "raw": {}}, True, []
    else:
        result, gh_ok = compute(cfg, window)
        repos = _str_list(cfg.get("repos"))

    if args.outward_fixture:
        with open(args.outward_fixture) as f:
            fx = json.load(f)
        fx_dict = fx if isinstance(fx, dict) else {}
        outward = compute_outward(fleet_cfg, window, fetch=lambda repo, _since: fx_dict.get(repo), workspace=workspace)
    elif args.fixture:
        outward = {"m7_outward_artifact_floor": None, "outward": {"note": "not computed in --fixture mode"}}
    else:
        outward = compute_outward(fleet_cfg, window, workspace=workspace)

    record = {
        "measured_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "window_days": window,
        "shadow": bool(cfg.get("shadow", True)),
        "gh_ok": gh_ok,
        "repos": repos,
        "ship_config": ship_sig,
        "outward_config": outward_sig,
        "m6s_meta_work_ship_ratio": result["m6s_meta_work_ship_ratio"],
        "raw": result["raw"],
        "m7_outward_artifact_floor": outward["m7_outward_artifact_floor"],
        "outward": outward["outward"],
    }

    if not args.no_store and not args.fixture and not args.outward_fixture:
        state_file = ship_state_file
        d = os.path.dirname(state_file)
        os.makedirs(d, exist_ok=True)
        # atomic swap — the background SessionStart writer must never expose a truncated
        # file to the main sensor's Stop-time read (merge_ship_shadow json.load).
        fd, tmp = tempfile.mkstemp(dir=d, prefix=".ship-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(record, f, indent=2)
                # POSIX trailing newline — same contract as the main sensor's
                # leverage-state.json write. Governed repos may TRACK this file.
                f.write("\n")
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
        o = record["outward"]
        print(f"[outward] m7_outward_artifact_floor = {record['m7_outward_artifact_floor']}  "
              f"({o.get('merged_product_prs', 0)} merged product PRs {o.get('per_repo', {})}, "
              f"{o.get('door_receipts', 0)} door receipts [{o.get('door_receipts_status')}])")
        if o.get("errors"):
            print(f"  unreachable product repos: {o['errors']}")
        if o.get("note"):
            print(f"  note: {o['note']}")


if __name__ == "__main__":
    main()
