#!/usr/bin/env python3
"""bstack crystallize — rule-of-three pattern detector (Phase 7, v0.9.5).

Scans `docs/conversations/*.md` (or a directory supplied via
`--conversations`) for patterns that recur across >=3 distinct
sessions with explicit failure-mode mention and acknowledgement of
repetition. Surfaces candidates for human P16 approval — never
auto-promotes.

Subcommands:
  candidates [--json] [--conversations DIR] [--min-sessions N] [--limit N]
                                 Emit detected rule-of-three candidates
  promote <slug> [--json] [--conversations DIR] [--min-sessions N]
                                 Draft a primitive scaffold (does NOT auto-merge)

Heuristics (per substrate completion spec, section 6, Phase 7):
  1. Phrase appears in >= min-sessions distinct conversation files.
  2. >= 1 occurrence co-locates (within a 200-char window) with a
     failure-mode keyword (e.g. "failed", "orphaned", "race", ...).
  3. >= 1 occurrence co-locates with an acknowledgement keyword
     (e.g. "again", "twice", "third time", "recurring", ...).

Substring suppression: when two candidates have the same session set
and one phrase is a substring of the other, only the longer phrase
is retained.

Exit codes:
  0  success (zero or more candidates surfaced)
  2  invalid arguments (argparse / usage)
  3  conversations directory missing
  4  promote slug not found in current candidate set
"""

import argparse
import bisect
import json
import os
import re
import sys
from collections import defaultdict
from pathlib import Path


FAILURE_KEYWORDS = (
    "failed",
    "fails",
    "broke",
    "broken",
    "orphaned",
    "race",
    "regression",
    "rolled back",
    "reverted",
    "lost",
    "silently",
    "wrong commit",
    "wrong state",
    "crashed",
    "hung",
    "hang",
    "timeout",
    "timed out",
    "corrupted",
    "shipped broken",
)

ACK_KEYWORDS = (
    "again",
    "twice",
    "third time",
    "three times",
    "recurring",
    "keeps happening",
    "has happened",
    "this happened",
    "second time",
    "rule-of-three",
    "rule of three",
    "recurred",
    "had to redo",
    "had to revert",
    "once more",
)

STOP_WORDS = frozenset(
    """
    a an the and or but if then else when while for to of in on at by with
    is are was were be been being have has had do does did so as it its this
    that these those there here we i you he she they them us our your their
    not no yes from into about over under up down out off again very can
    could will would shall should may might must ought also too just very
    really actually just only than then now today via per across
    """.split()
)

NGRAM_MIN = 2
NGRAM_MAX = 4

# Secret-pattern scrubbing for citation excerpts. Conversation logs
# routinely contain API keys, tokens, and PII. Citations are emitted to
# stdout / JSON and may flow into PR comments or CI artifacts, so the
# excerpt is the last line of defence before that exfiltration path.
SECRET_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9_-]{20,}"),
    re.compile(r"ghp_[A-Za-z0-9]{20,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{40,}"),
    re.compile(r"gho_[A-Za-z0-9]{20,}"),
    re.compile(r"xox[bpasr]-[A-Za-z0-9-]{20,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"AIza[0-9A-Za-z_-]{30,}"),
    re.compile(r"eyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+"),
    re.compile(
        r"(password|secret|token|api[_-]?key|access[_-]?key)\s*[:=]\s*\S{6,}",
        re.IGNORECASE,
    ),
)


def scrub_secrets(text):
    """Replace common secret patterns with [REDACTED] in citation excerpts."""
    for pat in SECRET_PATTERNS:
        text = pat.sub("[REDACTED]", text)
    return text


def _env_keywords(env_var, default):
    raw = os.environ.get(env_var, "").strip()
    if not raw:
        return tuple(default)
    return tuple(k.strip().lower() for k in raw.split(",") if k.strip())


def slugify(phrase):
    return re.sub(r"[^a-z0-9-]+", "-", phrase.lower()).strip("-")


_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9'\-]*")


def tokenize_with_positions(text):
    """Yield ``(token, start_pos)`` tuples in a single pass over the text.

    Tokens are lowercased and stripped of leading/trailing apostrophes and
    hyphens. Empty post-strip tokens are skipped. Positions index into the
    original (un-normalized) text, so they remain valid for excerpt + window
    extraction.
    """
    for m in _TOKEN_PATTERN.finditer(text):
        tok = m.group().lower().strip("-'")
        if tok:
            yield (tok, m.start())


def normalize_tokens(text):
    """Backwards-compatible helper — returns the token list only."""
    return [t for t, _pos in tokenize_with_positions(text)]


def _ngram_is_content(gram_tokens):
    """Reject n-grams whose content kernel is too thin to be a real pattern.

    Tightening rules (compared to the v0.9.5 first pass):
      - 2-grams must be entirely content: both tokens non-stopword, both
        of length >= 2. Mixed-content 2-grams are the dominant false-
        positive source (substrings of real patterns).
      - 3+-grams must have no stop-word at either edge. A leading or
        trailing "the"/"a"/"of"/etc. is almost always a phrasing variant
        of the same recurring kernel and adds noise.
      - 3+-grams must contain >= ceil(n/2) content tokens overall.

    ``gram_tokens`` is a tuple of pre-lowercased tokens (no spaces).
    """
    n = len(gram_tokens)
    if n < 2:
        return False
    if n == 2:
        return all(t not in STOP_WORDS and len(t) > 1 for t in gram_tokens)
    if gram_tokens[0] in STOP_WORDS or gram_tokens[-1] in STOP_WORDS:
        return False
    content = [t for t in gram_tokens if t not in STOP_WORDS and len(t) > 1]
    return len(content) >= max(2, n // 2 + 1)


def _build_line_starts(text):
    """Sorted list of newline-start offsets (line k starts at result[k-1])."""
    starts = [0]
    pos = 0
    while True:
        nl = text.find("\n", pos)
        if nl < 0:
            break
        starts.append(nl + 1)
        pos = nl + 1
    return starts


def _line_info(text, start, line_starts=None):
    """Return (line_no, excerpt) for offset ``start`` within ``text``.

    When ``line_starts`` is provided, line lookup is O(log lines) via bisect;
    otherwise we fall back to two ``text.find/rfind`` scans. The latter is
    fine for a handful of lookups but quadratic at corpus scale.
    """
    if line_starts is not None:
        line_no = bisect.bisect_right(line_starts, start)
        line_start = line_starts[line_no - 1] if line_no else 0
    else:
        line_no = text.count("\n", 0, start) + 1
        line_start = text.rfind("\n", 0, start) + 1
    line_end = text.find("\n", line_start)
    if line_end == -1:
        line_end = len(text)
    excerpt = scrub_secrets(text[line_start:line_end].strip()[:200])
    return (line_no, excerpt)


def _signal_in_window(text, position, keywords, window=200):
    lo = max(0, position - window)
    hi = min(len(text), position + window)
    snippet = text[lo:hi].lower()
    hits = []
    for kw in keywords:
        if kw in snippet:
            hits.append(kw)
    return hits


def detect_candidates(conv_dir, min_sessions=3, limit=20):
    """Scan ``conv_dir`` for rule-of-three phrase candidates.

    Two passes for corpus-scale performance:
      1. Stream-tokenize each file, accumulate ``phrase -> [file, ...]``
         (file membership only). Defer position/line/excerpt computation.
      2. Filter to phrases with ``len(files) >= min_sessions``, then resolve
         positions, line numbers, excerpts and failure/ack signals only for
         the surviving phrases. Per-file token offsets are reused from
         pass 1; line numbers come from a per-file bisect index.

    This keeps the per-n-gram cost in pass 1 to a constant-factor set/dict
    operation, deferring the O(file_size) line-lookup work until after the
    rule-of-three threshold has filtered down to candidate phrases.
    """
    failure_kws = _env_keywords("CRYSTALLIZE_FAILURE_KEYWORDS", FAILURE_KEYWORDS)
    ack_kws = _env_keywords("CRYSTALLIZE_ACK_KEYWORDS", ACK_KEYWORDS)

    files = sorted(conv_dir.glob("*.md"))
    file_texts = {}
    file_token_positions = {}
    file_line_starts = {}
    file_first_token_idx = {}

    for f in files:
        try:
            text = f.read_text(errors="replace")
        except OSError:
            continue
        file_texts[f] = text
        tp = list(tokenize_with_positions(text))
        file_token_positions[f] = tp
        file_line_starts[f] = _build_line_starts(text)
        idx = defaultdict(list)
        for ti, (tok, _pos) in enumerate(tp):
            idx[tok].append(ti)
        file_first_token_idx[f] = idx

    # Pass 1: phrase -> list of files containing it (no positions yet).
    phrase_files = defaultdict(list)
    for f, tp in file_token_positions.items():
        tlen = len(tp)
        if tlen == 0:
            continue
        seen_in_file = set()
        for n in range(NGRAM_MIN, NGRAM_MAX + 1):
            if tlen < n:
                continue
            for i in range(tlen - n + 1):
                gram_tokens = tuple(tp[i + k][0] for k in range(n))
                if gram_tokens in seen_in_file:
                    continue
                seen_in_file.add(gram_tokens)
                if not _ngram_is_content(gram_tokens):
                    continue
                phrase_files[" ".join(gram_tokens)].append(f)

    # Pass 2: resolve positions, signals, citations for surviving phrases.
    candidates = []
    for phrase, fs in phrase_files.items():
        if len(fs) < min_sessions:
            continue
        phrase_tokens = tuple(phrase.split())
        n = len(phrase_tokens)
        first_tok = phrase_tokens[0]
        per_file = {}
        for f in fs:
            tp = file_token_positions[f]
            for ti in file_first_token_idx[f].get(first_tok, ()):
                if ti + n > len(tp):
                    continue
                matched = True
                for k in range(1, n):
                    if tp[ti + k][0] != phrase_tokens[k]:
                        matched = False
                        break
                if matched:
                    start = tp[ti][1]
                    line_no, excerpt = _line_info(
                        file_texts[f], start, file_line_starts[f]
                    )
                    per_file[f] = (start, line_no, excerpt)
                    break

        failure_signals = []
        ack_signals = []
        for f, (start, _line, _excerpt) in per_file.items():
            text = file_texts[f]
            for kw in _signal_in_window(text, start, failure_kws):
                failure_signals.append({"file": f.name, "keyword": kw})
            for kw in _signal_in_window(text, start, ack_kws):
                ack_signals.append({"file": f.name, "keyword": kw})
        if not failure_signals or not ack_signals:
            continue
        citations = []
        for f, (_start, line, excerpt) in sorted(per_file.items()):
            citations.append({"file": f.name, "line": line, "excerpt": excerpt})
        candidates.append(
            {
                "slug": slugify(phrase),
                "phrase": phrase,
                "session_count": len(per_file),
                "failure_signals": failure_signals,
                "ack_signals": ack_signals,
                "citations": citations,
            }
        )

    candidates = _suppress_substrings(candidates)
    candidates.sort(key=lambda c: (-c["session_count"], -len(c["phrase"]), c["phrase"]))
    return candidates[:limit]


SUPPRESSION_DELTA = 1


def _suppress_substrings(candidates):
    """Drop a substring candidate whose evidence is essentially the same
    as a longer phrase's.

    The shorter phrase is kept only when it recurs strictly more often
    than the longer phrase by more than ``SUPPRESSION_DELTA`` sessions —
    that's the case where the shorter is the recurring kernel and the
    longer is a single phrasing variant that misses some occurrences.

    With delta=1, a shorter phrase that only picks up one extra
    "boilerplate" occurrence (e.g. a casual mention in a session
    unrelated to the real pattern) is suppressed in favour of the
    longer, more specific phrasing.
    """
    by_len = sorted(candidates, key=lambda c: -len(c["phrase"]))
    drop = set()
    for i, shorter in enumerate(by_len):
        for longer in by_len[:i]:
            if shorter["phrase"] == longer["phrase"]:
                continue
            if shorter["phrase"] in longer["phrase"]:
                if shorter["session_count"] <= longer["session_count"] + SUPPRESSION_DELTA:
                    drop.add(shorter["phrase"])
                    break
    return [c for c in candidates if c["phrase"] not in drop]


def render_scaffold(c):
    failure_kw_list = sorted({s["keyword"] for s in c["failure_signals"]})
    ack_kw_list = sorted({s["keyword"] for s in c["ack_signals"]})
    citations_md = "\n".join(
        "- `{file}:{line}` — {excerpt}".format(**cit) for cit in c["citations"]
    )
    return (
        "# Primitive candidate: {phrase}\n\n"
        "> DRAFT — `bstack crystallize promote` produced this scaffold from rule-of-three\n"
        "> detection in `docs/conversations`. **This does NOT auto-merge a primitive.**\n"
        "> The four P16 conditions still apply; this scaffold is a starting point, not a decision.\n\n"
        "## Pattern (auto-detected)\n\n"
        'Phrase: **"{phrase}"** observed in {session_count} distinct sessions.\n\n'
        "## Failure-mode signals (auto-detected)\n\n"
        "{failure_kws}\n\n"
        "## Acknowledgement signals (auto-detected)\n\n"
        "{ack_kws}\n\n"
        "## Citations\n\n"
        "{citations}\n\n"
        "## P16 manual gates (fill these in before promoting)\n\n"
        "- [ ] **Concrete mechanism** — name the executable behaviour the primitive enforces.\n"
        "- [ ] **Stated invariant** — what holds once the primitive is in force.\n"
        "- [ ] **Stated failure mode** — write a one-paragraph counterexample.\n"
        "- [ ] **Short name** — `Name (Pn)` form.\n"
        "- [ ] **Promotion target** — skill / SKILL.md / AGENTS.md row / .control/policy.yaml gate.\n"
        "- [ ] **Composition** — which existing primitives it cooperates with.\n\n"
        "## Next steps\n\n"
        "1. Sharpen the phrase into a primitive name.\n"
        "2. Draft an AGENTS.md row + table entry.\n"
        "3. Decide if a `.control/policy.yaml` gate can enforce it.\n"
        "4. Open a PR — humans review; never auto-merge a primitive.\n"
    ).format(
        phrase=c["phrase"],
        session_count=c["session_count"],
        failure_kws=", ".join(failure_kw_list) or "(none)",
        ack_kws=", ".join(ack_kw_list) or "(none)",
        citations=citations_md or "(none)",
    )


def cmd_candidates(args):
    conv_dir = Path(args.conversations).resolve()
    if not conv_dir.is_dir():
        print(
            "crystallize: conversations directory not found: {}".format(conv_dir),
            file=sys.stderr,
        )
        return 3
    candidates = detect_candidates(
        conv_dir, min_sessions=args.min_sessions, limit=args.limit
    )
    if args.json:
        payload = {
            "candidates": candidates,
            "count": len(candidates),
            "min_sessions": args.min_sessions,
            "limit": args.limit,
            "conversations": str(conv_dir),
        }
        json.dump(payload, sys.stdout, indent=2)
        sys.stdout.write("\n")
        return 0

    print(
        "  crystallize: scanned {dir} (min-sessions={min_n}, limit={limit})".format(
            dir=conv_dir, min_n=args.min_sessions, limit=args.limit
        )
    )
    if not candidates:
        print("  No rule-of-three candidates surfaced.")
        print(
            "  Heuristics: >={n} distinct sessions, failure-mode keyword nearby, ack keyword nearby.".format(
                n=args.min_sessions
            )
        )
        return 0
    print("  Found {n} rule-of-three candidate(s):".format(n=len(candidates)))
    print()
    bar = "  " + "=" * 60
    for i, c in enumerate(candidates, 1):
        print(bar)
        print('  [{i}] "{p}" ({n} sessions)'.format(i=i, p=c["phrase"], n=c["session_count"]))
        print("      Slug: {}".format(c["slug"]))
        f_summary = ", ".join(
            "{keyword} ({file})".format(**s) for s in c["failure_signals"][:3]
        )
        a_summary = ", ".join(
            "{keyword} ({file})".format(**s) for s in c["ack_signals"][:3]
        )
        print("      Failure signals: {}".format(f_summary or "(none)"))
        print("      Ack signals: {}".format(a_summary or "(none)"))
        print("      Citations ({}):".format(len(c["citations"])))
        for cit in c["citations"][:5]:
            print("        - {file}:{line} -- {excerpt}".format(**cit))
        if len(c["citations"]) > 5:
            print("        ... and {} more".format(len(c["citations"]) - 5))
        print("      -> bstack crystallize promote {}".format(c["slug"]))
    print(bar)
    print()
    print("  P16 reminder: review each candidate against the four conditions:")
    print("    1. >=3 distinct instances (auto-checked above)")
    print("    2. Concrete mechanism (verify manually)")
    print("    3. Stated invariant (verify manually)")
    print("    4. Stated failure mode (auto-checked above)")
    return 0


def cmd_promote(args):
    conv_dir = Path(args.conversations).resolve()
    if not conv_dir.is_dir():
        print(
            "crystallize: conversations directory not found: {}".format(conv_dir),
            file=sys.stderr,
        )
        return 3
    candidates = detect_candidates(
        conv_dir, min_sessions=args.min_sessions, limit=args.limit
    )
    target = next((c for c in candidates if c["slug"] == args.slug), None)
    if target is None:
        print(
            "crystallize: slug '{}' not found in current candidate set.".format(args.slug),
            file=sys.stderr,
        )
        print(
            "  Run `bstack crystallize candidates` to list available slugs.",
            file=sys.stderr,
        )
        return 4
    scaffold = render_scaffold(target)
    if args.json:
        payload = {
            "slug": target["slug"],
            "phrase": target["phrase"],
            "session_count": target["session_count"],
            "scaffold": scaffold,
            "auto_merged": False,
        }
        json.dump(payload, sys.stdout, indent=2)
        sys.stdout.write("\n")
    else:
        print(scaffold)
    return 0


def _common_args(p):
    p.add_argument(
        "--conversations",
        default="docs/conversations",
        help="Directory to scan (default: docs/conversations)",
    )
    p.add_argument(
        "--min-sessions",
        type=int,
        default=3,
        help="Minimum distinct sessions (default: 3)",
    )
    p.add_argument(
        "--limit",
        type=int,
        default=20,
        help="Maximum candidates to surface (default: 20)",
    )
    p.add_argument("--json", action="store_true", help="Emit JSON instead of text")


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="crystallize",
        description="Rule-of-three pattern detector (Phase 7, v0.9.5).",
    )
    sub = parser.add_subparsers(dest="cmd")
    sp_c = sub.add_parser("candidates", help="Emit detected candidates")
    _common_args(sp_c)
    sp_p = sub.add_parser("promote", help="Draft primitive scaffold (does NOT auto-merge)")
    sp_p.add_argument("slug")
    _common_args(sp_p)
    args = parser.parse_args(argv)
    if args.cmd == "candidates":
        return cmd_candidates(args)
    if args.cmd == "promote":
        return cmd_promote(args)
    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
