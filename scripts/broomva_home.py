#!/usr/bin/env python3
"""The .broomva home: one path resolver and one append-only, hash-chained ledger.

BRO-2909 phase P0. Spec: docs/specs/2026-10-07-broomva-home.html.
Envelope schema: schemas/broomva-event.v1.schema.json (this module's validator
is a hand-written mirror of it; scripts/test_broomva_home.py cross-checks the two
on every fixture in schemas/fixtures/broomva-event/).

Nothing in the repo calls this yet. P0 is the contract and the resolver; writers
are repointed at it per domain in later phases, so importing this module has no
side effects and no existing behavior changes.

Layout (machine scope, BROOMVA_HOME or ~/.broomva, mode 0700):

    config/ secrets/ ledger/ work/ notes/ runs/ views/ index/ cache/ locks/

Stream -> file:

    work/<rest>   -> <home>/work/<rest>/events.jsonl
    anything else -> <home>/ledger/<stream>.jsonl

One event per line, keys sorted, compact separators, UTF-8, "\\n"-terminated.
``prev`` is "sha256:" + hex sha256 of the previous line's exact bytes without
its newline, and null only on the first line of a stream.

What the chain proves, and what it does not: each line's ``prev`` pins the
exact bytes of the line before it, so ``verify()`` detects an edit to any line
EXCEPT THE LAST, and the deletion or reordering of any line that has a
successor. It says nothing about the tail: an edited last line, or lines cut
off the end, verify clean. ``head()`` returns ``{lines, last_id, last_hash}``
for anchoring outside the file; ``verify(stream, expect_head="N:sha256:...")``
then also proves the first N lines, tail included.

~/.life is the legacy home (owner decision A3): ``legacy_homes()`` names it so
readers can fall back to it, and nothing in this module ever writes there.

Python stdlib only.
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import hashlib
import json
import os
import re
import secrets as _secrets
import sys
import threading
import time
from pathlib import Path
from typing import Any, Iterator

SCHEMA_VERSION = 1

LAYOUT = (
    "config",
    "secrets",
    "ledger",
    "work",
    "notes",
    "runs",
    "views",
    "index",
    "cache",
    "locks",
)

FIELDS = (
    "v",
    "id",
    "ts",
    "stream",
    "type",
    "actor",
    "subject",
    "refs",
    "cause",
    "data",
    "prev",
)

ACTOR_KINDS = ("agent", "human", "hook", "loop", "daemon")
SUBJECT_KINDS = (
    "agent",
    "session",
    "work",
    "ask",
    "initiative",
    "ticket",
    "pr",
    "tick",
    "run",
    "loop",
)

# Every pattern below is copied verbatim into schemas/broomva-event.v1.schema.json.
# The test suite asserts the strings are identical, so the two cannot drift.
CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
ULID_RE = r"^[0-7][0-9A-HJKMNP-TV-Z]{25}$"
TS_RE = (
    r"^[0-9]{4}-(0[1-9]|1[0-2])-(0[1-9]|[12][0-9]|3[01])"
    r"T([01][0-9]|2[0-3]):[0-5][0-9]:([0-5][0-9]|60)(\.[0-9]{1,9})?Z$"
)
STREAM_RE = r"^[a-z0-9][a-z0-9._-]*(/[a-z0-9][a-z0-9._-]*)*$"
TYPE_RE = r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$"
ID_BODY = r"[A-Za-z0-9][A-Za-z0-9._/@#+=-]*"
ACTOR_RE = r"^(" + "|".join(ACTOR_KINDS) + r"):" + ID_BODY + r"$"
SUBJECT_RE = r"^(" + "|".join(SUBJECT_KINDS) + r"):" + ID_BODY + r"$"
REF_KEY_RE = r"^[a-z][a-z0-9_]*$"
PREV_RE = r"^sha256:[0-9a-f]{64}$"
# 200, not 255: the lock file name spells the stream with "/" as "__", and the
# name must stay under NAME_MAX (255) for any stream that passes (see lock_file).
STREAM_MAX = 200
KIND_ID_MAX = 256
TYPE_MAX = 128
NAME_MAX = 255
CHUNK = 8192

# ---------------------------------------------------------------- secrets
#
# SECRETS ONLY. Aligned with the secret categories of the conversation bridge's
# redactor (scripts/conversation-history.py, _PII_PATTERNS) and deliberately
# WITHOUT its PII and heuristic rules: email, phone, SSN and credit-card numbers,
# the 64-hex `hex_secret` rule and the `(?<=\s)[a-zA-Z0-9_-]{30,}` long-token rule.
# A ledger is made of sha256 digests, UUIDs, ULIDs and ids; those rules would
# erase it. Also left out, as not secret: OAuth client ids and GCP client_email.
#
# A match anywhere in a string redacts the WHOLE string: a ledger value is not
# prose to be kept readable around a hole, and a partial redaction can leave a
# usable fragment.
#
# Every pattern is linear in its input (BRO-2917): a lookahead that scans a
# run is bounded ({0,255}), a scheme is capped ({0,30}), and the one pattern
# that needs a whole-string context is anchored at \A with atomic steps. The
# test suite times each one on a 200 KB adversarial input.
#
# (name, pattern). The test suite has one generated sample per name, and the
# mutation sweep drops each pattern in turn.
_B = r"(?<![A-Za-z0-9])"  # start boundary: not glued to a preceding alnum
_B64 = r"(?<![A-Za-z0-9+/])"  # start boundary inside base64 text
# A 40-char base64 run with a digit, an uppercase and a lowercase letter: the
# shape of an AWS secret access key (a lowercase 40-hex git SHA has no
# uppercase; a CamelCase path like src/Components/KeyboardShortcutPanelView has
# no digit).
_AWS40 = (
    r"(?<![A-Za-z0-9/+])(?=[A-Za-z0-9/+]{0,39}[A-Z])(?=[A-Za-z0-9/+]{0,39}[a-z])"
    r"(?=[A-Za-z0-9/+]{0,39}[0-9])[A-Za-z0-9/+]{40}(?![A-Za-z0-9/+=])"
)
_AWS_CTX = r"(?i:aws|secret|key)"
# The value after "bearer" is token-shaped: 16+ chars of the token alphabet,
# with a digit, or with an uppercase letter after the first char and a
# lowercase one. "bearer responsibilities" and "Bearer Responsibilities" are
# prose; Authorization: Bearer <anything> is caught by authorization_header.
_TOK = r"[A-Za-z0-9_.~+/=-]"
# Verbatim from the bridge, and byte-identical in scripts/conversation-history.py
# and scripts/build-hf-dataset.py (a test asserts it): TypeSafe-style
# `apikey_<body>` (hex >= 16, or a >= 16 run with a digit, an uppercase and a
# lowercase letter, each within the body's first 256 chars).
APIKEY_PREFIX = (
    r"([Aa][Pp][Ii][Kk][Ee][Yy]_(?:[0-9a-fA-F]{16,}|(?=[A-Za-z0-9_-]{16})(?=[A-Za-z0-9_-]{0,255}[0-9])"
    r"(?=[A-Za-z0-9_-]{0,255}[A-Z])(?=[A-Za-z0-9_-]{0,255}[a-z])[A-Za-z0-9_-]+)[A-Za-z0-9_-]*)"
)
# A command-line flag that names a secret: its value is the next argv item, or
# the rest of the token after "=" (see scrub() for the list form).
SECRET_FLAGS = ("--token", "--password", "--api-key", "--apikey", "--secret", "--client-secret")
SECRET_PATTERNS: tuple[tuple[str, str], ...] = (
    ("openai_anthropic", _B + r"sk-(?:ant-)?[A-Za-z0-9_-]{20,}"),
    ("stripe", _B + r"[sr]k_(?:live|test)_[A-Za-z0-9]{16,}"),
    ("github_token", _B + r"gh[pousr]_[A-Za-z0-9]{20,}"),
    ("github_pat", _B + r"github_pat_[A-Za-z0-9_]{22,}"),
    # xoxe- is a Slack refresh token (token rotation); xoxe.xoxp-... access
    # tokens are caught through their xoxp- part.
    ("slack", _B + r"(?:xox[abeposr]|xapp)-[A-Za-z0-9-]{10,}"),
    ("npm", _B + r"npm_[A-Za-z0-9]{30,}"),
    ("sendgrid", _B + r"SG\.[A-Za-z0-9_-]{16,}\.[A-Za-z0-9_-]{16,}"),
    ("square", _B + r"sq0[a-z]{3}-[A-Za-z0-9_-]{22,}"),
    ("google_api_key", _B + r"AIza[A-Za-z0-9_-]{35}"),
    ("aws_access_key_id", r"(?<![A-Z0-9])(?:AKIA|ASIA|ABIA|ACCA)[0-9A-Z]{16}(?![0-9A-Z])"),
    # _AWS40 in a string that names aws/secret/key OUTSIDE the run (the word
    # case-insensitive, the run not). Either the first context word has such a
    # run after it, or the first such run has a context word after it; each
    # "first" is an atomic (?=(...))\N step, so one anchored pass, no retries.
    (
        "aws_secret_access_key",
        r"(?s)\A(?:(?=(.*?" + _AWS_CTX + r"))\1.*?" + _AWS40
        + r"|(?=(.*?" + _AWS40 + r"))\2.*?" + _AWS_CTX + r")",
    ),
    ("jwt", r"(?<![A-Za-z0-9_-])eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),
    (
        "bearer",
        r"\b(?i:bearer)\s+(?=" + _TOK + r"{16})(?:(?=" + _TOK + r"{0,255}[0-9])"
        r"|(?=" + _TOK + r"{1,255}[A-Z])(?=" + _TOK + r"{0,255}[a-z]))",
    ),
    # Also the quoted JSON form: "Authorization": "Bearer ...".
    (
        "authorization_header",
        r"(?i)\b(?:proxy-)?authorization[\"']?\s*[:=]\s*[\"']?(?:(?:basic|bearer|token|digest|negotiate)\s+"
        r"[A-Za-z0-9_.~+/=:-]{8,}|[A-Za-z0-9_.~+/=-]{20,})",
    ),
    ("apikey_prefix", APIKEY_PREFIX),
    # Verbatim from the bridge: the spoken form `api key: <value of 20+>`.
    ("spoken_api_key", r"(?i)(api[\s-]+key)\s*[=:]\s*[\"']?([^\s'\"]{20,})"),
    # The bridge's generic key=value rule, minus client ids, plus a few names.
    (
        "generic_key_value",
        r"(?i)(?:client.?secret|api.?key|access.?token|refresh.?token|auth.?token|session.?token"
        r"|secret.?key|private.?key|password|passwd)\s*[=:]\s*[\"']?[A-Za-z0-9_.+/=-]{20,}",
    ),
    # NAME=value where the NAME ends with the secret word (GITHUB_TOKEN=..., not
    # MY_APIKEY_FILE=/etc/...). A value starting with "$" is a reference, not a
    # secret. Under a *TOKEN name only, a number of up to 9 digits is a count
    # (MAX_OUTPUT_TOKEN=128000000); API_SECRET=<digits> is still redacted.
    (
        "env_assignment",
        r"(?<![A-Za-z0-9_])[A-Z0-9_]*(?:(?:API_KEY|APIKEY|SECRET|PASSWORD|PASSWD|PASSPHRASE|PRIVATE_KEY"
        r"|SIGNING_KEY|ACCESS_KEY)\s*=\s*[\"']?"
        r"|TOKEN\s*=\s*[\"']?(?![+-]?[0-9]{1,9}(?:\.[0-9]+)?(?![^\s\"'$])))[^\s\"'$]{8,}",
    ),
    # password=<8+> and "password": "<8+>"; the bare prose colon form is left
    # alone. Not `pwd`: PWD=/Users/x/apps is the working directory.
    (
        "password_assignment",
        r"(?i)(?<![A-Za-z0-9])(?:password|passwd|passphrase)"
        r"(?:\s*=\s*[\"']?[^\s\"'$]{8,}|\"\s*:\s*\"[^\"]{8,}\")",
    ),
    ("env_url", r"(?<![A-Za-z0-9_])(?:DATABASE_URL|REDIS_URL|MONGO_URI|MONGODB_URI)\s*=\s*\S+"),
    # --token VALUE, --password=VALUE, ... (SECRET_FLAGS, any case) in one string.
    (
        "cli_secret_flag",
        r"(?i)(?<![A-Za-z0-9-])--(?:token|password|api-?key|secret|client-secret)(?:\s+|=)\S+",
    ),
    # Any scheme, not only http(s): postgres://user:pw@host leaks the same way.
    # The scheme is capped at 31 chars so a long a.a.a... run is not rescanned
    # from every start (that was 14.8 s on 100 KB).
    ("url_credentials", r"(?i)\b[a-z][a-z0-9+.-]{0,30}://[^\s/:@]+:[^\s/@]+@"),
    ("pem_header", r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----"),
    # A private key body pasted without its header. Prefixes read off keys made
    # with OpenSSL 3.6 / ssh-keygen: PKCS#1 RSA (MIIEow.., MIIJKA..), PKCS#8 RSA,
    # PKCS#8 EC, SEC1 EC (P-256 short form, P-384/P-521 long form), PKCS#8
    # Ed25519, OpenSSH. A public certificate (MIIC+TCCA..) matches none of them.
    (
        "pem_body",
        _B64 + r"(?:MII[A-Za-z0-9+/]{3}IBAAK|MII[A-Za-z0-9+/]{3}IBADANBgkqhkiG9w0BAQEFAAS"
        r"|MIG[A-Za-z0-9+/]AgEAMBMGByqGSM49|MHcCAQEE|MI[GH][A-Za-z0-9+/]AgEBB"
        r"|MC4CAQAwBQYDK2Vw|b3BlbnNzaC1rZXktdjE)",
    ),
)
SECRET_VALUE_RES = tuple((name, re.compile(p)) for name, p in SECRET_PATTERNS)

REDACTED = "[redacted]"
# Key names are normalized (camelCase split, lowercased, "-" read as "_") and
# split on "_". A key is secret-named when its LAST segment ends with one of
# SECRET_KEY_ENDINGS (access_token, GITHUB_TOKEN, and with no separator to
# split on, GITHUBTOKEN, XAPIKey, accesstoken), or when its trailing segments
# equal one of SECRET_KEY_SUFFIXES (x-api-key, set-cookie, signing_key).
# tokens_used, token_count, max_tokens and tokenizer match neither.
#
# Left out on purpose, as too generic: "session" and "auth" (session ids,
# auth_method, auth_url are ordinary ledger data). session_token and
# auth_token still match through their last segment, authorization by suffix.
SECRET_KEY_ENDINGS = ("token", "secret", "password", "passwd", "passphrase", "apikey")
# Words that would over-match as endings ("cookie" ends fortunecookie, "bearer"
# pallbearer, "key" monkey), so they match only as whole trailing segments.
SECRET_KEY_SUFFIXES = (
    "api_key",
    "authorization",
    "cookie",
    "private_key",
    "signing_key",
    "credential",
    "credentials",
    "bearer",
    "access_key",
    "secret_key",
)
_SUFFIX_SEGMENTS = tuple(tuple(s.split("_")) for s in SECRET_KEY_SUFFIXES)
# Under a secret-named key, a value that cannot carry a credential is kept: a
# bool, a number, null, a purely numeric string, or a string shorter than
# SECRET_MIN_LEN (the same floor the value patterns use: password=, NAME=).
# So has_password: true, max_token: 4096, next_token: 2 and stop_token: "</s>"
# survive; password: "hunter2" (7 chars) survives too, which is the trade.
SECRET_MIN_LEN = 8
_NUMERIC_RE = re.compile(r"[+-]?[0-9]+(?:\.[0-9]+)?")


class StreamError(Exception):
    """The stream on disk cannot be extended safely (torn or unparseable tail)."""


# ---------------------------------------------------------------- resolver


def home() -> Path:
    """BROOMVA_HOME if set and non-empty, else ~/.broomva. Always absolute."""
    raw = os.environ.get("BROOMVA_HOME", "")
    if raw:
        p = Path(raw).expanduser()
        if not p.is_absolute():
            raise ValueError(f"BROOMVA_HOME: must be an absolute path, got {raw!r}")
        return p
    return Path.home() / ".broomva"


def paths() -> dict[str, Path]:
    """The machine-scope layout: one directory per role, all under home()."""
    h = home()
    return {name: h / name for name in LAYOUT}


def legacy_homes() -> list[Path]:
    """Homes read as a legacy alias only (owner decision A3). Never written."""
    return [Path.home() / ".life"]


def _check_stream(stream: Any) -> str:
    if not isinstance(stream, str) or not stream:
        raise ValueError("stream: must be a non-empty string")
    if stream.startswith("/"):
        raise ValueError(f"stream: absolute paths are refused: {stream!r}")
    segments = stream.split("/")
    if any(s == "" for s in segments):
        raise ValueError(f"stream: empty path segment in {stream!r}")
    if any(s in (".", "..") for s in segments):
        raise ValueError(f"stream: '.' and '..' segments are refused: {stream!r}")
    if len(stream) > STREAM_MAX or not re.fullmatch(STREAM_RE, stream):
        raise ValueError(f"stream: must match {STREAM_RE} (max {STREAM_MAX}): {stream!r}")
    return stream


def stream_file(stream: str) -> Path:
    """Map a stream name to its JSONL file. Refuses traversal and bare 'work'."""
    _check_stream(stream)
    h = home()
    if stream == "work":
        raise ValueError("stream: 'work' needs an item path, e.g. work/<initiative>/<slug>")
    if stream.startswith("work/"):
        target = h / "work" / stream[len("work/"):] / "events.jsonl"
    else:
        target = h / "ledger" / f"{stream}.jsonl"
    # Defense in depth: the pattern already excludes '..', but a symlink inside
    # the home could still point out of it, so compare resolved paths.
    real_home = os.path.realpath(h)
    if os.path.commonpath([os.path.realpath(target), real_home]) != real_home:
        raise ValueError(f"stream: resolves outside the home: {stream!r}")
    return target


def lock_file(stream: str) -> Path:
    """locks/<stream with / as __>.lock, hashed when that would exceed NAME_MAX."""
    _check_stream(stream)
    name = stream.replace("/", "__") + ".lock"
    if len(name.encode("utf-8")) > NAME_MAX:
        digest = hashlib.sha256(stream.encode("utf-8")).hexdigest()[:16]
        name = stream.replace("/", "__")[:200] + "-" + digest + ".lock"
    return home() / "locks" / name


def _mkdirs(directory: Path) -> None:
    """Create directory and missing parents with mode 0700 (umask-proof).

    Directories that already exist are left with the mode they have.
    """
    missing = []
    d = directory
    while not d.exists():
        missing.append(d)
        if d.parent == d:
            break
        d = d.parent
    for d in reversed(missing):
        try:
            d.mkdir(mode=0o700)
        except FileExistsError:
            continue
        os.chmod(d, 0o700)


def _fsync_dir(directory: Path) -> None:
    """fsync a directory, so a file created (or renamed) in it survives a crash."""
    dfd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(dfd)
    finally:
        os.close(dfd)


@contextlib.contextmanager
def _locked(stream: str) -> Iterator[None]:
    """Hold the stream's exclusive flock. Only writers (append, repair) take it.

    Readers (head, verify, tail) take no lock at all: they read a snapshot and
    ignore an unterminated final line, which is a write in progress. A shared
    lock let three overlapping readers delay a writer by 7.7 s (BRO-2917).

    No timeout: a writer that hangs while holding the lock blocks the stream.
    """
    lock = lock_file(stream)
    _mkdirs(lock.parent)
    fd = os.open(lock, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)  # closing the descriptor releases the flock


# ---------------------------------------------------------------- ULID + time

_ulid_lock = threading.Lock()
_ulid_last: tuple[int, int] = (-1, 0)


def _encode(value: int, length: int) -> str:
    out = []
    for _ in range(length):
        out.append(CROCKFORD[value & 31])
        value >>= 5
    return "".join(reversed(out))


def new_ulid(ms: int | None = None) -> str:
    """A 26-char Crockford ULID: 48-bit ms timestamp + 80 random bits.

    Monotonic within a process: a second ULID in the same millisecond is the
    previous randomness + 1, so ids sort in generation order. Across processes
    there is no shared counter: two writers in the same millisecond can put a
    larger id on an earlier line. File order, not id order, is the stream order.
    """
    global _ulid_last
    if ms is None:
        ms = time.time_ns() // 1_000_000
    if not 0 <= ms < 2**48:
        raise ValueError("id: timestamp out of ULID range")
    with _ulid_lock:
        last_ms, last_rand = _ulid_last
        if ms <= last_ms and last_rand + 1 < 2**80:
            ms, rand = last_ms, last_rand + 1
        else:
            rand = int.from_bytes(_secrets.token_bytes(10), "big")
        _ulid_last = (ms, rand)
    return _encode(ms, 10) + _encode(rand, 16)


def ulid_ms(ulid: str) -> int:
    """Decode the millisecond timestamp of a ULID."""
    value = 0
    for ch in ulid[:10]:
        value = value * 32 + CROCKFORD.index(ch)
    return value


def _ts_from_ms(ms: int) -> str:
    secs, milli = divmod(ms, 1000)
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(secs)) + f".{milli:03d}Z"


def ts_key(ts: str) -> tuple[str, int]:
    """Sort key for a valid ts: fixed-width seconds, then the fraction in ns."""
    whole = ts[:19]
    frac = ts[20:-1] if ts[19] == "." else ""
    return whole, int(frac.ljust(9, "0")) if frac else 0


# ---------------------------------------------------------------- scrubbing


def secret_match(value: str) -> str | None:
    """Name of the first secret pattern found in value, or None."""
    for name, rx in SECRET_VALUE_RES:
        if rx.search(value):
            return name
    return None


def secret_key(key: str) -> bool:
    """True if a key NAME marks its value as a secret: the last segment ends
    with one of SECRET_KEY_ENDINGS, or the trailing segments equal one of
    SECRET_KEY_SUFFIXES."""
    norm = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", key).lower().replace("-", "_")
    segments = tuple(s for s in norm.split("_") if s)
    if not segments:
        return False
    if segments[-1].endswith(SECRET_KEY_ENDINGS):
        return True
    return any(len(segments) >= len(suf) and segments[-len(suf):] == suf for suf in _SUFFIX_SEGMENTS)


# A count under a *token name (max_token, MAX_OUTPUT_TOKEN=128000000) has at
# most this many digits; a longer run of digits may be a numeric token.
COUNTER_MAX_DIGITS = 9


def _counter_like(value: Any) -> bool:
    """A number, or a numeric string, of at most COUNTER_MAX_DIGITS digits."""
    if isinstance(value, (int, float)):
        return abs(value) < 10 ** COUNTER_MAX_DIGITS
    return isinstance(value, str) and bool(_NUMERIC_RE.fullmatch(value)) \
        and sum(c.isdigit() for c in value) <= COUNTER_MAX_DIGITS


def _may_be_credential(key: str, value: Any) -> bool:
    """False for a value under a secret-named key that cannot carry a credential.

    null and a bool never can. Under a name ending in "token" (max_token,
    next_token, stop_token), a counter-like number or a string under
    SECRET_MIN_LEN ("</s>") is a count or a marker. Under any other secret name
    (password, secret, api_key, ...) everything else is redacted: YAML reads an
    unquoted numeric password as an int, and a short password is still one.
    """
    if value is None or isinstance(value, bool):
        return False
    if _token_named(key):
        if _counter_like(value):
            return False
        if isinstance(value, str) and len(value) < SECRET_MIN_LEN and not _NUMERIC_RE.fullmatch(value):
            return False
    return True


def _token_named(key: str) -> bool:
    """True if the key's last segment ends with "token" (as secret_key splits it)."""
    norm = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", key).lower().replace("-", "_")
    segments = [s for s in norm.split("_") if s]
    return bool(segments) and segments[-1].endswith("token")


def _secret_flag(item: Any) -> bool:
    """True for an argv item that is a bare secret flag (its value is the next item)."""
    return isinstance(item, str) and item.lower() in SECRET_FLAGS


def _check_keys(value: Any, field: str) -> None:
    """JSON objects have string keys; Python dicts may not. Refuse, never coerce."""
    if isinstance(value, dict):
        for k, v in value.items():
            if not isinstance(k, str):
                raise ValueError(f"{field}: non-string key {k!r}")
            _check_keys(v, field)
    elif isinstance(value, list):
        for v in value:
            _check_keys(v, field)


def scrub(value: Any) -> Any:
    """Return a copy with secrets replaced. Recursive over dicts and lists.

    - A value under a secret-named key becomes "[redacted]", unless it cannot
      carry a credential (_may_be_credential: null or a bool anywhere; under a
      *token name also a number of up to COUNTER_MAX_DIGITS digits or a string
      under SECRET_MIN_LEN chars), in which case it is kept.
    - A string containing any SECRET_PATTERNS match becomes "[redacted]" whole.
    - In a list, the string right after a bare secret flag (SECRET_FLAGS, any
      case: ["--token", "<value>"]) becomes "[redacted]"; "--token=<value>"
      is caught by the cli_secret_flag pattern.
    - A key that itself matches a secret pattern is renamed "[redacted-key-N]"
      (N counts from 1 per scrub call); its value is scrubbed as usual.
    """
    counter = [0]

    def walk(v: Any) -> Any:
        if isinstance(v, dict):
            out: dict = {}
            for k, item in v.items():
                if isinstance(k, str) and secret_match(k):
                    counter[0] += 1
                    new_key = f"[redacted-key-{counter[0]}]"
                    while new_key in v or new_key in out:
                        counter[0] += 1
                        new_key = f"[redacted-key-{counter[0]}]"
                    out[new_key] = walk(item)
                elif isinstance(k, str) and secret_key(k) and _may_be_credential(k, item):
                    out[k] = REDACTED
                else:
                    out[k] = walk(item)
            return out
        if isinstance(v, list):
            items = []
            for i, item in enumerate(v):
                if i > 0 and _secret_flag(v[i - 1]) and isinstance(item, str):
                    items.append(REDACTED)
                else:
                    items.append(walk(item))
            return items
        if isinstance(v, str) and secret_match(v):
            return REDACTED
        return v

    return walk(value)


# ---------------------------------------------------------------- validator


def _is_kind_id(value: Any, pattern: str) -> bool:
    return isinstance(value, str) and len(value) <= KIND_ID_MAX and re.fullmatch(pattern, value) is not None


def _refuse_secret(field: str, value: str) -> None:
    name = secret_match(value)
    if name:
        raise ValueError(f"{field}: value has the shape of a secret ({name}); ids are never rewritten, so it is refused")


def validate(event: Any) -> None:
    """Raise ValueError naming the first offending field.

    Mirrors schemas/broomva-event.v1.schema.json, and is stricter in three ways
    the schema cannot express:
      - patterns use re.fullmatch, so a trailing newline that Python's re.search
        would let past '$' is refused (ECMA-262 '$' refuses it too);
      - stream, type, actor, subject and refs values that match a secret pattern
        are refused (data is scrubbed instead; ids are never silently rewritten);
      - a dict with non-string keys is refused (only reachable from Python).
    """
    if not isinstance(event, dict):
        raise ValueError("event: must be a JSON object")
    for f in FIELDS:
        if f not in event:
            raise ValueError(f"{f}: required field missing")
    extra = sorted((k for k in event if k not in FIELDS), key=str)
    if extra:
        raise ValueError(f"{extra[0]}: unknown field (additionalProperties is false)")

    v = event["v"]
    if isinstance(v, bool) or not isinstance(v, (int, float)) or v != SCHEMA_VERSION:
        raise ValueError(f"v: must be {SCHEMA_VERSION}")
    if not isinstance(event["id"], str) or not re.fullmatch(ULID_RE, event["id"]):
        raise ValueError("id: must be a 26-char Crockford base32 ULID")
    if not isinstance(event["ts"], str) or not re.fullmatch(TS_RE, event["ts"]):
        raise ValueError("ts: must be RFC 3339 UTC ending in Z")
    s = event["stream"]
    if not isinstance(s, str) or len(s) > STREAM_MAX or not re.fullmatch(STREAM_RE, s):
        raise ValueError("stream: must be a lowercase path-like name")
    _refuse_secret("stream", s)
    t = event["type"]
    if not isinstance(t, str) or len(t) > TYPE_MAX or not re.fullmatch(TYPE_RE, t):
        raise ValueError("type: must be '<domain>.<verb>[.<more>]', lowercase dotted")
    _refuse_secret("type", t)
    if not _is_kind_id(event["actor"], ACTOR_RE):
        raise ValueError(f"actor: must be '<kind>:<id>' with kind in {'|'.join(ACTOR_KINDS)}")
    _refuse_secret("actor", event["actor"])
    if not _is_kind_id(event["subject"], SUBJECT_RE):
        raise ValueError(f"subject: must be '<kind>:<id>' with kind in {'|'.join(SUBJECT_KINDS)}")
    _refuse_secret("subject", event["subject"])
    refs = event["refs"]
    if not isinstance(refs, dict):
        raise ValueError("refs: must be an object")
    for k, val in refs.items():
        if not isinstance(k, str) or not re.fullmatch(REF_KEY_RE, k):
            raise ValueError(f"refs: key {k!r} must be a string matching {REF_KEY_RE}")
        if not _is_kind_id(val, SUBJECT_RE):
            raise ValueError(f"refs: value of {k!r} must be a '<kind>:<id>' string")
        _refuse_secret("refs", val)
    c = event["cause"]
    if c is not None and (not isinstance(c, str) or not re.fullmatch(ULID_RE, c)):
        raise ValueError("cause: must be a ULID or null")
    if not isinstance(event["data"], dict):
        raise ValueError("data: must be an object")
    _check_keys(event["data"], "data")
    p = event["prev"]
    if p is not None and (not isinstance(p, str) or not re.fullmatch(PREV_RE, p)):
        raise ValueError("prev: must be 'sha256:<64 hex>' or null")


# ---------------------------------------------------------------- ledger


def line_hash(line: bytes) -> str:
    return "sha256:" + hashlib.sha256(line).hexdigest()


def encode(event: dict) -> bytes:
    return json.dumps(
        event, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")


def _line_ending_at(fd: int, end: int) -> bytes:
    """The bytes of the line whose newline is at offset ``end``, without it.

    Reads backwards in CHUNK-sized pieces, collected in a list and joined once.
    """
    pos = end  # chunks hold bytes before the newline
    chunks: list[bytes] = []
    while pos > 0:
        start = max(0, pos - CHUNK)
        chunk = os.pread(fd, pos - start, start)
        nl = chunk.rfind(b"\n")
        if nl != -1:
            chunks.append(chunk[nl + 1:])
            break
        chunks.append(chunk)
        pos = start
    return b"".join(reversed(chunks))


def _last_line(fd: int) -> bytes | None:
    """The last line of the file without its newline; None if the file is empty.

    For writers, under the lock: an unterminated tail there is torn, not in
    progress, so it raises StreamError.
    """
    size = os.fstat(fd).st_size
    if size == 0:
        return None
    if os.pread(fd, 1, size - 1) != b"\n":
        raise StreamError("stream: last line has no trailing newline (torn write?); run repair")
    return _line_ending_at(fd, size - 1)


def _complete(raw: bytes) -> bytes:
    """raw cut after its last newline. For lock-free readers: bytes past the
    last newline are a write in progress (or a torn tail, which append refuses
    and repair fixes), never a line to check."""
    return raw[: raw.rfind(b"\n") + 1]


def append(
    stream: str,
    type: str,
    subject: str,
    data: dict | None,
    *,
    actor: str,
    refs: dict | None = None,
    cause: str | None = None,
) -> dict:
    """Append one event to a stream under an exclusive lock; return the event.

    The id and ts are taken *after* the lock is held, so events in one stream
    are written in ts order; if the clock reads earlier than the stream's last
    ts (clock stepped back), the last ts is reused so ts stays non-decreasing.

    A short write, or an OSError from the write, the file fsync or (for a new
    file) the directory fsync, truncates the file back to its size before the
    write, still under the lock, and re-raises: a failed append leaves the
    stream exactly as it was, so a retry cannot duplicate the event.
    """
    _check_stream(stream)
    target = stream_file(stream)
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise ValueError("data: must be an object")
    _check_keys(data, "data")
    if refs is None:
        refs = {}
    if not isinstance(refs, dict):
        raise ValueError("refs: must be an object")
    clean = scrub(data)

    # Validate everything except id/ts/prev before touching the disk, so a bad
    # call never creates directories or takes the lock.
    probe = {
        "v": SCHEMA_VERSION,
        "id": "0" * 26,
        "ts": "1970-01-01T00:00:00.000Z",
        "stream": stream,
        "type": type,
        "actor": actor,
        "subject": subject,
        "refs": refs,
        "cause": cause,
        "data": clean,
        "prev": None,
    }
    validate(probe)
    try:
        encode(probe)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"data: not JSON-serializable ({exc})") from exc

    _mkdirs(target.parent)
    with _locked(stream):
        created = not target.exists()
        fd = os.open(target, os.O_RDWR | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            last = _last_line(fd)
            prev = None if last is None else line_hash(last)
            ms = time.time_ns() // 1_000_000
            ts = _ts_from_ms(ms)
            if last is not None:
                try:
                    last_ts = json.loads(last)["ts"]
                    if ts_key(ts) < ts_key(last_ts):
                        ts = last_ts
                except (ValueError, KeyError, TypeError, IndexError) as exc:
                    raise StreamError(f"stream: last line is not a valid event ({exc}); run verify") from exc
            event = dict(probe, id=new_ulid(ms), ts=ts, prev=prev)
            validate(event)
            line = encode(event) + b"\n"
            size_before = os.fstat(fd).st_size
            try:
                written = os.write(fd, line)
                if written != len(line):
                    raise OSError(f"short write: {written} of {len(line)} bytes")
                # An fsync error (EIO) after a full write must roll back too:
                # left in place, the line is there while append raises, and
                # the caller's retry writes the event twice.
                os.fsync(fd)
                if created:
                    _fsync_dir(target.parent)
            except OSError:
                os.ftruncate(fd, size_before)
                raise
        finally:
            os.close(fd)
    return event


def _parse_expect_head(expect_head: str) -> tuple[int, str]:
    lines, _, digest = expect_head.partition(":")
    if not lines.isdigit() or int(lines) < 1:
        raise ValueError("expect_head: must be 'LINES:sha256:<64 hex>' with LINES >= 1")
    if re.fullmatch(r"[0-9a-f]{64}", digest):
        digest = "sha256:" + digest
    if not re.fullmatch(PREV_RE, digest):
        raise ValueError("expect_head: must be 'LINES:sha256:<64 hex>' with LINES >= 1")
    return int(lines), digest


def verify_file(
    path: Path, stream: str | None = None, expect_head: str | None = None, notes: list[str] | None = None
) -> list[str]:
    """Check one JSONL file: parse, validate, chain, unique ids, ts order.

    Takes no lock. Bytes after the last newline are a write in progress and are
    not checked (nor reported as a problem); if ``notes`` is a list, a line
    saying how many were skipped is appended to it.

    Without expect_head the last line and the line count are NOT proven (see
    the module docstring). With expect_head "N:sha256:<hex>", line N must exist
    and hash to that value; the chain then carries the proof to lines 1..N.
    Lines appended after N are checked by the chain like any others.
    """
    anchor = _parse_expect_head(expect_head) if expect_head is not None else None
    problems: list[str] = []
    try:
        raw = Path(path).read_bytes()
    except FileNotFoundError:
        return [f"{path}: no such stream file"]
    complete = _complete(raw)
    if len(complete) < len(raw) and notes is not None:
        notes.append(
            f"{len(raw) - len(complete)} bytes after the last newline not checked: a write in progress, "
            "or a torn tail (append refuses one; run repair)"
        )
    lines = complete.split(b"\n")[:-1]
    prev_line: bytes | None = None
    prev_ts: str | None = None
    seen: dict[str, int] = {}
    for n, line in enumerate(lines, start=1):
        try:
            event = json.loads(line.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            problems.append(f"line {n}: not valid JSON ({exc})")
            prev_line, prev_ts = line, None
            continue
        try:
            validate(event)
        except ValueError as exc:
            problems.append(f"line {n}: invalid event: {exc}")
        if isinstance(event, dict):
            expected = None if prev_line is None else line_hash(prev_line)
            if event.get("prev") != expected:
                problems.append(f"line {n}: prev is {event.get('prev')!r}, expected {expected!r}")
            if stream is not None and event.get("stream") != stream:
                problems.append(f"line {n}: stream is {event.get('stream')!r}, file belongs to {stream!r}")
            eid = event.get("id")
            if isinstance(eid, str):
                if eid in seen:
                    problems.append(f"line {n}: duplicate id {eid} (first on line {seen[eid]})")
                else:
                    seen[eid] = n
            ts = event.get("ts")
            if isinstance(ts, str) and re.fullmatch(TS_RE, ts):
                if prev_ts is not None and ts_key(ts) < ts_key(prev_ts):
                    problems.append(f"line {n}: ts {ts} is earlier than the previous line's {prev_ts}")
                prev_ts = ts
        prev_line = line
    if anchor is not None:
        want_n, want_hash = anchor
        if len(lines) < want_n:
            problems.append(f"head: expected at least {want_n} lines ending {want_hash}, found {len(lines)} (truncated)")
        elif line_hash(lines[want_n - 1]) != want_hash:
            problems.append(
                f"head: line {want_n} hashes to {line_hash(lines[want_n - 1])}, anchor says {want_hash} (edited)"
            )
    return problems


def verify(stream: str, expect_head: str | None = None, notes: list[str] | None = None) -> list[str]:
    """Verify a stream by name, without a lock. An empty list means clean."""
    target = stream_file(stream)
    if expect_head is not None:
        _parse_expect_head(expect_head)
    return verify_file(target, stream, expect_head, notes)


def head(stream: str) -> dict:
    """{lines, last_id, last_hash} of a stream, for anchoring outside the file.

    Pass f"{lines}:{last_hash}" to verify(expect_head=...) later. An empty or
    missing stream is {0, None, None}. Takes no lock: it reads the file up to
    its size at open, and counts and hashes complete lines only, so a write in
    progress is invisible and the count and the hash describe the same line.
    """
    empty = {"lines": 0, "last_id": None, "last_hash": None}
    target = stream_file(stream)
    try:
        fd = os.open(target, os.O_RDONLY)
    except FileNotFoundError:
        return empty
    try:
        size = os.fstat(fd).st_size
        count, pos = 0, 0
        # The last line is taken from the bytes counted, never read again: a
        # rollback and a new append between two reads would hash a fragment.
        last, partial = b"", bytearray()
        while pos < size:
            chunk = os.pread(fd, min(1 << 20, size - pos), pos)
            if not chunk:
                break  # shrunk under us (a rolled-back append); keep what was read
            nl = chunk.rfind(b"\n")
            if nl == -1:
                partial += chunk
            else:
                count += chunk.count(b"\n")
                start = chunk.rfind(b"\n", 0, nl) + 1
                last = bytes(chunk[start:nl]) if start else bytes(partial + chunk[:nl])
                partial = bytearray(chunk[nl + 1:])
            pos += len(chunk)
        if count == 0:
            return empty
    finally:
        os.close(fd)
    try:
        last_id = json.loads(last).get("id")
    except (ValueError, AttributeError):
        last_id = None
    return {"lines": count, "last_id": last_id, "last_hash": line_hash(last)}


def _is_chained_event(torn: bytes, prev_line: bytes | None, stream: str) -> bool:
    """True if torn is exactly the line append() would have written after
    prev_line: canonical bytes of a valid event of this stream, chained to it."""
    try:
        event = json.loads(torn.decode("utf-8"))
        validate(event)
    except (UnicodeDecodeError, ValueError):
        return False
    expected = None if prev_line is None else line_hash(prev_line)
    return event.get("stream") == stream and event.get("prev") == expected and encode(event) == torn


def repair(stream: str) -> dict:
    """Recover a torn tail (e.g. power loss mid-write); refuse a clean stream.

    If the bytes after the last newline are a complete event, canonically
    encoded and chained to the line before them (only the newline was lost),
    the newline is appended: {action: "newline_appended", ...}.

    Otherwise they are written to "<stream file>.torn-<utc>" beside the stream
    file (never deleted; the file and its directory are fsynced first), then
    the stream is truncated to its last newline: {action: "moved", ...}.

    Both return {action, torn_file, torn_bytes, kept_bytes}.
    """
    target = stream_file(stream)
    with _locked(stream):
        try:
            fd = os.open(target, os.O_RDWR)
        except FileNotFoundError as exc:
            raise StreamError(f"repair: no such stream file {target}") from exc
        try:
            size = os.fstat(fd).st_size
            if size == 0 or os.pread(fd, 1, size - 1) == b"\n":
                raise StreamError("repair: the last line ends with a newline; nothing to repair")
            keep = 0
            pos = size
            while pos > 0:
                start = max(0, pos - CHUNK)
                nl = os.pread(fd, pos - start, start).rfind(b"\n")
                if nl != -1:
                    keep = start + nl + 1
                    break
                pos = start
            torn = os.pread(fd, size - keep, keep)
            prev_line = _line_ending_at(fd, keep - 1) if keep else None
            if _is_chained_event(torn, prev_line, stream):
                try:
                    if os.pwrite(fd, b"\n", size) != 1:
                        raise OSError("repair: short write of the newline")
                    os.fsync(fd)
                except OSError:
                    os.ftruncate(fd, size)
                    raise
                return {"action": "newline_appended", "torn_file": None, "torn_bytes": 0, "kept_bytes": size + 1}
            stamp = time.strftime("%Y%m%dT%H%M%S", time.gmtime()) + f".{time.time_ns() // 1_000_000 % 1000:03d}Z"
            torn_path = target.with_name(f"{target.name}.torn-{stamp}")
            tfd = os.open(torn_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            try:
                if os.write(tfd, torn) != len(torn):
                    raise OSError(f"repair: short write to {torn_path}")
                os.fsync(tfd)
            finally:
                os.close(tfd)
            # The sidecar's directory entry must be durable before the bytes it
            # holds are cut from the stream, or a crash can lose them both.
            _fsync_dir(target.parent)
            os.ftruncate(fd, keep)
            os.fsync(fd)
        finally:
            os.close(fd)
    return {"action": "moved", "torn_file": str(torn_path), "torn_bytes": len(torn), "kept_bytes": keep}


def tail(stream: str, n: int = 10) -> list[str]:
    """The last n complete lines. Takes no lock; reads the whole file.

    Split on "\\n" only: str.splitlines() would also split inside a JSON
    string holding U+2028, U+2029 or U+0085, which json.dumps with
    ensure_ascii=False leaves unescaped.
    """
    try:
        raw = stream_file(stream).read_bytes()
    except FileNotFoundError:
        return []
    lines = _complete(raw).decode("utf-8").split("\n")[:-1]
    return lines[-n:] if n > 0 else []


# ---------------------------------------------------------------- CLI


def _json_arg(name: str, raw: str | None) -> Any:
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except ValueError as exc:
        raise ValueError(f"{name}: not valid JSON ({exc})") from exc


def main(argv: list[str] | None = None) -> int:
    """Exit codes: 0 ok, 1 verify found problems, 2 bad input or an I/O error."""
    ap = argparse.ArgumentParser(prog="broomva_home.py", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("paths", help="print the home and its layout")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("append", help="append one event; prints it as JSON")
    p.add_argument("stream")
    p.add_argument("type")
    p.add_argument("subject")
    p.add_argument("--actor", required=True)
    p.add_argument("--refs")
    p.add_argument("--cause")
    p.add_argument("--data")

    p = sub.add_parser("verify", help="check a stream; exit 1 on any problem")
    p.add_argument("stream")
    p.add_argument("--expect-head", metavar="LINES:HASH", help="anchor from `head`; proves the tail too")

    p = sub.add_parser("head", help="print {lines, last_id, last_hash} for external anchoring")
    p.add_argument("stream")

    p = sub.add_parser("tail", help="print the last N lines of a stream")
    p.add_argument("stream")
    p.add_argument("-n", type=int, default=10)

    p = sub.add_parser("repair", help="fix a last line that lacks its newline: terminate it if it is a whole chained event, else move it aside")
    p.add_argument("stream")

    args = ap.parse_args(argv)
    try:
        if args.cmd == "paths":
            layout = {"home": str(home()), **{k: str(v) for k, v in paths().items()}}
            layout_legacy = [str(x) for x in legacy_homes()]
            if args.json:
                print(json.dumps({**layout, "legacy_homes": layout_legacy}, indent=2))
            else:
                for k, v in layout.items():
                    print(f"{k}\t{v}")
                for x in layout_legacy:
                    print(f"legacy\t{x}\t(read-only)")
            return 0
        if args.cmd == "append":
            event = append(
                args.stream,
                args.type,
                args.subject,
                _json_arg("data", args.data),
                actor=args.actor,
                refs=_json_arg("refs", args.refs),
                cause=args.cause,
            )
            print(encode(event).decode("utf-8"))
            return 0
        if args.cmd == "verify":
            notes: list[str] = []
            problems = verify(args.stream, args.expect_head, notes)
            for note in notes:
                print(f"note: {note}", file=sys.stderr)
            for line in problems:
                print(line)
            if problems:
                return 1
            print(f"ok {args.stream}: {stream_file(args.stream)}")
            return 0
        if args.cmd == "head":
            print(json.dumps(head(args.stream)))
            return 0
        if args.cmd == "tail":
            for line in tail(args.stream, args.n):
                print(line)
            return 0
        if args.cmd == "repair":
            done = repair(args.stream)
            if done["action"] == "newline_appended":
                print(
                    f"repaired {args.stream}: the last line was a complete event chained to the one before it, "
                    f"missing only its newline; appended the newline ({done['kept_bytes']} bytes)"
                )
            else:
                print(
                    f"repaired {args.stream}: moved {done['torn_bytes']} torn bytes to {done['torn_file']}, "
                    f"kept {done['kept_bytes']} bytes"
                )
            return 0
    except (ValueError, StreamError, OSError) as exc:
        print(f"error: {exc}".splitlines()[0], file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":
    sys.exit(main())
