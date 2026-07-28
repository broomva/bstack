#!/usr/bin/env python3
"""hook-liveness.py — resolve every REGISTERED Claude Code hook command to the
script(s) it actually invokes, and report the dead ones. Backs doctor.sh §26
(BRO-2021).

WHY a second resolver next to doctor §25:
  §25 answers a narrower question — "does the first absolute-path token of a
  hook wired in the WORKSPACE settings.json exist?" It deliberately bails on
  anything it cannot statically resolve (composites, wrappers, ${VAR} forms) and
  it never looks at the other two registration surfaces. Claude Code loads hooks
  from THREE places:

    1. project  <workspace>/.claude/settings.json (+ settings.local.json)
    2. user     ~/.claude/settings.json
    3. plugin   <plugin-root>/hooks/hooks.json   (${CLAUDE_PLUGIN_ROOT}-rooted)

  A hook registered on surface 2 or 3 fires on EVERY session and was invisible
  to every check bstack had. The observed failure this closes: a Stop hook whose
  own path resolved fine, but which internally invoked a bookkeeping script at a
  path that had moved during a monorepo reorg — it ran 136ms per session doing
  nothing, for 8 days, silently.

RESOLUTION MODEL (per hook command string):
  * Tokenize with shlex(punctuation_chars=True) so `;`, `&&`, `|`, `>` are their
    own tokens — otherwise `/bin/sh '/a/x.sh';` yields a token with a trailing
    semicolon and the path check false-fails.
  * Expand ${CLAUDE_PLUGIN_ROOT} (plugin surface only), ${HOME}/$HOME and a
    leading ~. Anything still carrying $ or { after that is UNRESOLVED — counted,
    never reported as dead (we refuse to guess).
  * A token is a script to verify when it is (a) the program position of a
    command/pipeline segment, (b) directly after a known interpreter, or (c) a
    script-extension path anywhere in the command. (b)+(c) are what make wrapper
    forms work: `bash GUARD.sh python3 SENSOR.py --throttle N` verifies BOTH
    GUARD.sh and SENSOR.py, where §25 verifies neither.

SELF-GUARDING COMMANDS ARE NOT DEFECTS:
  The inline-conditional form some installers emit —
      if [ -f '/p/x.sh' ] && [ -x '/p/x.sh' ]; then /bin/sh '/p/x.sh'; else cat >/dev/null; fi
  — checks its own target before running it. A missing script there is INTENDED
  (the hook degrades to a no-op by construction). So every path that appears
  inside a `[ -f ... ]` / `[[ -x ... ]]` / `test -e ...` guard in the SAME command
  is collected first and excluded from the dead set. This is the one false
  positive that would otherwise fire on nearly every real machine.

EXECUTABILITY:
  +x is only load-bearing when the script is invoked DIRECTLY (`/p/x.sh`). Under
  an explicit interpreter (`bash /p/x.sh`) the mode bit is irrelevant, so a
  non-executable interpreter-invoked script is reported as info, never a gap.
  Direct + non-executable is a real "Permission denied" at fire time → dead.

INNER REFERENCES (the bug that actually got missed):
  For a live hook script under a bstack-governed root, statically resolve its
  own simple assignments (VAR=..., ${VAR:-default}, $HOME) and flag any fully
  resolved absolute *script* path (.sh/.py/...) that does not exist. Heuristic
  and therefore ADVISORY only: it never resolves command substitution, so
  anything containing $( ... ) is skipped rather than guessed at.

Output: TSV on stdout, one finding per line, for doctor.sh to format —
    STAT<TAB>surface<TAB>entries<TAB>scripts<TAB>guarded<TAB>unresolved
    NONE<TAB>surface<TAB>path                       (surface file absent)
    DEAD<TAB>surface<TAB>path<TAB>event<TAB>reason  (missing / not executable)
    SOFT<TAB>surface<TAB>path<TAB>event<TAB>skill   (companion skill not installed)
    INFO<TAB>surface<TAB>path<TAB>event<TAB>reason  (advisory)
    INNER<TAB>surface<TAB>hook<TAB>var<TAB>path     (live hook, dead inner ref)
Exit 0 always — this is a reporter, not a gate.
"""

import json
import os
import re
import shlex
import sys

SCRIPT_EXTS = (".sh", ".bash", ".zsh", ".py", ".js", ".mjs", ".cjs", ".ts", ".rb", ".pl")
INTERPRETERS = {
    "bash", "sh", "zsh", "dash", "ksh", "env", "exec", "command",
    "python", "python2", "python3", "node", "nodejs", "ruby", "perl",
    "deno", "bun", "uv", "osascript",
}
KEYWORDS = {"if", "then", "else", "elif", "fi", "do", "done", "while", "until",
            "for", "case", "esac", "function", "time", "!", "{", "}"}
SEPARATORS = {";", "&&", "||", "|", "&", "(", ")"}
REDIRECTS = {">", ">>", "<", "<<", "<<<", ">&", "&>", ">|"}
TEST_OPEN = {"[", "[[", "test"}
TEST_CLOSE = {"]", "]]"}

# A path inside one of these guards is self-checked by the command itself.
GUARD_RE = re.compile(
    r'(?:\[\[?|\btest\b)\s+-[A-Za-z]+\s+'
    r'(?:"([^"]*)"|\'([^\']*)\'|([^\s\];&|]+))'
)
ASSIGN_RE = re.compile(r'^\s*(?:export\s+|local\s+|declare\s+)?'
                       r'([A-Za-z_][A-Za-z0-9_]*)=(.*)$')
VAR_RE = re.compile(r'\$\{([A-Za-z_][A-Za-z0-9_]*)(?::[-=]([^{}]*))?\}'
                    r'|\$([A-Za-z_][A-Za-z0-9_]*)')
UNSAFE_VALUE = ("$(", "`", "|", ";", "&&")
MAX_SCAN_BYTES = 256 * 1024


def expand(tok, home, plugin_root):
    """Expand the three forms a hook command legitimately carries."""
    s = tok
    if plugin_root:
        s = s.replace("${CLAUDE_PLUGIN_ROOT}", plugin_root)
        s = s.replace("$CLAUDE_PLUGIN_ROOT", plugin_root)
    s = s.replace("${HOME}", home).replace("$HOME", home)
    if s == "~":
        s = home
    elif s.startswith("~/"):
        s = home + s[1:]
    return s


def unresolved(path):
    return "$" in path or "{" in path or path.startswith("~")


def guard_paths(cmd, home, plugin_root):
    out = set()
    for m in GUARD_RE.finditer(cmd):
        raw = m.group(1) or m.group(2) or m.group(3) or ""
        if raw:
            out.add(expand(raw, home, plugin_root))
    return out


def scripts_in(cmd, home, plugin_root):
    """Yield (path, direct) for every script this command invokes.

    direct=True means the script is executed by path (its +x bit matters);
    direct=False means an interpreter or wrapper runs it (mode bit irrelevant).
    """
    try:
        lex = shlex.shlex(cmd, posix=True, punctuation_chars=True)
        lex.whitespace_split = True
        toks = list(lex)
    except ValueError:
        toks = cmd.split()

    found = []
    expect_program = True   # next non-flag token is a program to run
    after_interp = False    # previous token was an interpreter → next is its script
    in_test = False
    skip_next = False
    for tok in toks:
        if skip_next:
            skip_next = False
            continue
        if tok in REDIRECTS:
            skip_next = True          # redirection target is a file, not a script
            continue
        if in_test:
            if tok in TEST_CLOSE:
                in_test = False
            continue
        if tok in TEST_OPEN:
            in_test = True            # guarded paths handled by guard_paths()
            continue
        if tok in SEPARATORS:
            expect_program, after_interp = True, False
            continue
        if tok in KEYWORDS:
            expect_program = tok not in ("fi", "done", "esac", "}")
            after_interp = False
            continue
        if tok.startswith("-"):
            continue                  # a flag never names the program
        if "=" in tok and not tok.startswith("/") and not tok.startswith("$") \
                and expect_program and not after_interp:
            continue                  # VAR=value prefix assignment

        path = expand(tok, home, plugin_root)
        base = os.path.basename(path)
        is_interp = base in INTERPRETERS or re.match(r'^python3\.\d+$', base)

        if is_interp and (expect_program or after_interp):
            expect_program, after_interp = False, True
            continue

        looks_script = path.endswith(SCRIPT_EXTS)
        if after_interp or expect_program or looks_script:
            if path.startswith("/") or unresolved(path):
                found.append((path, bool(expect_program and not after_interp)))
        expect_program, after_interp = False, False
    return found


def companion_skill(path, home):
    """Skill name if the path lives under an (uninstalled) companion skill dir.

    bstack itself is never a companion — it must not hide behind this. Mirrors
    doctor §25 so a workspace audited before `npx skills add` stays honest.
    """
    for root in (os.path.join(home, ".claude", "skills") + os.sep,
                 os.path.join(home, ".agents", "skills") + os.sep):
        if path.startswith(root):
            name = path[len(root):].split(os.sep)[0]
            if name and name != "bstack":
                return name
    return None


def iter_hooks(settings_path):
    try:
        with open(settings_path) as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return
    for event, blocks in (data.get("hooks") or {}).items():
        for block in blocks or []:
            if not isinstance(block, dict):
                continue
            for hook in block.get("hooks") or []:
                cmd = (hook or {}).get("command") or ""
                if cmd:
                    yield event, cmd


def shellish(path):
    if path.endswith((".sh", ".bash", ".zsh")):
        return True
    try:
        with open(path, "rb") as fh:
            first = fh.readline(128).decode("utf-8", "replace")
    except OSError:
        return False
    return first.startswith("#!") and ("sh" in first)


def inner_refs(path, home):
    """Dead script paths a live shell hook references through its own variables.

    Deliberately narrow: only literal `VAR=value` assignments whose value carries
    no command substitution, only ${VAR} / ${VAR:-default} / $HOME expansion, and
    only values that end in a script extension AND fully resolve. Everything else
    is skipped rather than guessed — this is advisory output, so a false positive
    costs more than a miss.
    """
    try:
        if os.path.getsize(path) > MAX_SCAN_BYTES:
            return []
        text = open(path, encoding="utf-8", errors="replace").read()
    except OSError:
        return []
    env, order = {"HOME": home}, []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        m = ASSIGN_RE.match(line)
        if not m:
            continue
        name, value = m.group(1), m.group(2).strip()
        if value[:1] in ('"', "'") and value[-1:] == value[:1] and len(value) > 1:
            value = value[1:-1]
        elif " " in value or "\t" in value:
            continue                      # unquoted multi-word — not a plain path
        if any(bad in value for bad in UNSAFE_VALUE):
            continue                      # command substitution etc — never guess
        resolved, ok = value, True
        for _ in range(5):
            if "$" not in resolved:
                break

            def sub(mm):
                key = mm.group(1) or mm.group(3)
                if key in env:
                    return env[key]
                if mm.group(2) is not None:
                    return mm.group(2)
                return mm.group(0)
            new = VAR_RE.sub(sub, resolved)
            if new == resolved:
                break
            resolved = new
        if "$" in resolved:
            ok = False
        env[name] = resolved if ok else value
        if ok:
            order.append((name, resolved))

    dead = []
    for name, value in order:
        if not value.startswith("/") or not value.endswith(SCRIPT_EXTS):
            continue
        # Only report a reference the script actually USES elsewhere.
        uses = len(re.findall(r'\$\{?%s\b' % re.escape(name), text))
        if uses < 1 or os.path.exists(value):
            continue
        dead.append((name, value))
    return dead


def main(argv):
    home = os.path.expanduser("~")
    surfaces, inner_roots = [], []
    i = 1
    while i < len(argv):
        arg = argv[i]
        if arg == "--home":
            home = argv[i + 1]
            i += 2
        elif arg == "--inner-root":
            inner_roots.append(os.path.realpath(argv[i + 1]))
            i += 2
        elif arg == "--surface":
            # kind|label|path|plugin_root  (plugin_root optional)
            parts = argv[i + 1].split("|")
            while len(parts) < 4:
                parts.append("")
            surfaces.append(parts[:4])
            i += 2
        else:
            i += 1

    out = []
    for kind, label, path, plugin_root in surfaces:
        if not path or not os.path.isfile(path):
            out.append("NONE\t%s\t%s" % (label, path))
            continue
        entries = 0
        seen, guarded_n, unres_n = {}, 0, 0
        for event, cmd in iter_hooks(path):
            entries += 1
            guards = guard_paths(cmd, home, plugin_root)
            for script, direct in scripts_in(cmd, home, plugin_root):
                if script in guards:
                    guarded_n += 1
                    continue
                if unresolved(script):
                    unres_n += 1
                    continue
                key = (script, direct)
                seen.setdefault(key, event)
        live, findings, dead_n = [], [], 0
        for (script, direct), event in sorted(seen.items()):
            if not os.path.exists(script):
                skill = companion_skill(script, home)
                if skill:
                    findings.append("SOFT\t%s\t%s\t%s\t%s" % (label, script, event, skill))
                else:
                    dead_n += 1
                    findings.append("DEAD\t%s\t%s\t%s\tmissing" % (label, script, event))
                continue
            if not os.access(script, os.X_OK):
                if direct:
                    dead_n += 1
                    findings.append("DEAD\t%s\t%s\t%s\tnot executable" % (label, script, event))
                    continue
                findings.append("INFO\t%s\t%s\t%s\tnot executable (invoked under an "
                                "interpreter, so it still fires)" % (label, script, event))
            live.append(script)

        if inner_roots:
            for script in sorted(set(live)):
                real = os.path.realpath(script)
                if not any(real == r or real.startswith(r + os.sep) for r in inner_roots):
                    continue
                if not shellish(script):
                    continue
                for var, ref in inner_refs(script, home):
                    findings.append("INNER\t%s\t%s\t%s\t%s" % (label, script, var, ref))

        # STAT first so the surface summary heads its own findings. Field 6 is
        # unresolved:dead — doctor needs the dead count to decide ok-vs-info.
        out.append("STAT\t%s\t%d\t%d\t%d\t%d:%d"
                   % (label, entries, len(seen), guarded_n, unres_n, dead_n))
        out.extend(findings)

    sys.stdout.write("\n".join(out) + ("\n" if out else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
