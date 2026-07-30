#!/usr/bin/env bash
# workflow-injection-safety.test.sh — no `${{ }}` expression may appear inside a
# `run:` block of any workflow (BRO-2032).
#
# WHY THIS EXISTS, concretely. GitHub substitutes `${{ }}` into the script TEXT
# before bash ever parses it. So a step written as
#
#     run: |
#       title="${{ steps.notes.outputs.title }}"
#
# does not pass a value — it pastes one. When release v0.37.2 put the heading
#
#     ### feat: `doctor` §26 — hook liveness across all three surfaces
#
# through that line, the runner produced
#
#     title="feat: `doctor` §26 — hook liveness across all three surfaces"
#
# and bash, for which a backtick inside double quotes is command substitution,
# executed `doctor`. Exit 127 under `set -e`. Run 30406298067 died there, the
# tag was never pushed, and v0.37.2 remains released nowhere — while the merge
# itself was green, because nothing verifies that a release actually happened.
#
# The failure mode is worse than the outage: that job holds `contents: write`
# and a `GH_TOKEN`, so a CHANGELOG heading of `### feat: $(curl x|sh)` is remote
# code execution by anyone able to land a heading.
#
# THE FIX this test enforces: pass values through `env:`. The runner then places
# them in the environment and bash reads them as ordinary variables — data, never
# script. There are NO exemptions, deliberately. A per-value risk assessment
# ("this one is only a SHA") is exactly the kind of enumerated carve-out that
# rots: the next value added under the exemption is not re-assessed. A uniform
# invariant is cheaper to hold and cheaper to check.
#
# MUTATION PROOF (run these to confirm this test can fail):
#   1. In .github/workflows/release.yml, change `tag="$TAG"` back to
#      `tag="${{ steps.version.outputs.tag }}"` → this test must FAIL.
#   2. Delete the `TITLE:` line from the release step's `env:` block and restore
#      `title="${{ steps.notes.outputs.title }}"` → this test must FAIL.
# Both are exercised in tests 3 and 4 below against synthetic fixtures, so the
# proof runs on every CI invocation rather than living only in this comment.

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORKFLOW_DIR="$REPO_ROOT/.github/workflows"

pass=0
fail=0

ok()   { echo "  [ok] $1"; pass=$((pass + 1)); }
bad()  { echo "  [FAIL] $1"; fail=$((fail + 1)); }

echo "── workflow injection safety (BRO-2032) ─────────────────"

# The scanner. Emits "file:line: text" for every `${{ }}` found inside a
# block scalar introduced by `run:`. Block membership is decided by INDENTATION,
# which is what YAML itself uses, rather than by a heuristic on content.
#
# Callers pass explicit paths. Test 1 passes only TRACKED workflows, and that is
# load-bearing rather than incidental: `tests/onboard.test.sh` deploys a
# gitignored `.github/workflows/l3-stability.yml` into the working tree as a side
# effect, so a directory glob makes this test's verdict depend on whether another
# test ran first. Scoping to `git ls-files` also scopes the assertion correctly —
# an untracked file is not on main and never executes in CI, so it cannot be the
# injection sink this test exists to prevent.
scan() {
  /usr/bin/env python3 - "$@" <<'PY'
import re, sys, pathlib

files = []
for a in sys.argv[1:]:
    p = pathlib.Path(a)
    files.extend(sorted(p.glob("*.yml")) if p.is_dir() else [p])

for path in files:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        continue
    in_run = False
    run_indent = 0
    for lineno, line in enumerate(lines, 1):
        m = re.match(r"^(\s*)run:\s*[|>][-+]?\s*$", line)
        if m:
            in_run = True
            run_indent = len(m.group(1))
            continue
        if not in_run:
            continue
        stripped = line.strip()
        if stripped:
            indent = len(line) - len(line.lstrip())
            # Dedent to or past the `run:` key ends the block scalar.
            if indent <= run_indent:
                in_run = False
                continue
        if "${{" in line:
            print(f"{path.name}:{lineno}: {line.strip()}")
PY
}

# ---------------------------------------------------------------------------
# 1. Every workflow that actually ships is clean.
# ---------------------------------------------------------------------------
tracked=()
while IFS= read -r f; do
  [ -n "$f" ] && tracked+=("$REPO_ROOT/$f")
done < <(git -C "$REPO_ROOT" ls-files '.github/workflows/*.yml' 2>/dev/null)

if [ "${#tracked[@]}" -eq 0 ]; then
  bad "1. no tracked workflows found — scanner would pass vacuously"
else
  hits="$(scan "${tracked[@]}")"
  if [ -z "$hits" ]; then
    ok "1. no \${{ }} inside any run: block across ${#tracked[@]} tracked workflow(s)"
  else
    bad "1. \${{ }} found inside run: block(s) — pass these through env: instead"
    printf '        %s\n' "$hits"
  fi
fi

# 1b. Surface untracked workflows rather than silently skipping them. An
#     untracked workflow is either test pollution or a gate that is cited but
#     never runs (l3-stability.yml is gitignored at .gitignore:18 while CLAUDE.md
#     and AGENTS.md both name it as Gate G2). Informational: it is not this
#     test's job to fail on it, but it must not be invisible either.
untracked_n=0
for f in "$WORKFLOW_DIR"/*.yml; do
  [ -e "$f" ] || continue
  git -C "$REPO_ROOT" ls-files --error-unmatch "${f#"$REPO_ROOT"/}" >/dev/null 2>&1 || {
    untracked_n=$((untracked_n + 1))
    echo "  [info] untracked workflow present, not scanned and never runs in CI: $(basename "$f")"
  }
done
[ "$untracked_n" -eq 0 ] && echo "  [info] no untracked workflows in the tree"

# ---------------------------------------------------------------------------
# 2. The specific sink that broke v0.37.2 is gone, and its replacement is wired.
# ---------------------------------------------------------------------------
rel="$WORKFLOW_DIR/release.yml"
if grep -q 'title="\$TITLE"' "$rel" && grep -qE '^\s+TITLE: \$\{\{ steps\.notes\.outputs\.title \}\}' "$rel"; then
  ok "2. release title arrives via env: TITLE, not by text substitution"
else
  bad "2. release.yml must set title from \$TITLE with TITLE wired in env:"
fi

# ---------------------------------------------------------------------------
# 3. MUTATION PROOF (positive control): the scanner catches a reintroduced sink.
#    Without this, test 1 passing would be indistinguishable from a scanner that
#    never matches anything — the self-certifying-guard failure this repo has
#    already been bitten by.
# ---------------------------------------------------------------------------
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
cat > "$tmp/mutant.yml" <<'YAML'
jobs:
  x:
    steps:
      - name: reintroduced sink
        run: |
          set -euo pipefail
          title="${{ steps.notes.outputs.title }}"
          echo "$title"
YAML
if [ -n "$(scan "$tmp/mutant.yml")" ]; then
  ok "3. mutation proof — scanner FAILS a reintroduced \${{ }} sink"
else
  bad "3. scanner did not catch a reintroduced sink — the guard is vacuous"
fi

# ---------------------------------------------------------------------------
# 4. MUTATION PROOF (negative control): the scanner does NOT fire on the correct
#    form. A check that always fails is exactly as non-discriminating as one that
#    always passes — closing only one polarity is how the ablation false-retire
#    survived its first fix.
# ---------------------------------------------------------------------------
cat > "$tmp/clean.yml" <<'YAML'
jobs:
  x:
    steps:
      - name: correct form
        env:
          TITLE: ${{ steps.notes.outputs.title }}
        run: |
          set -euo pipefail
          title="$TITLE"
          echo "$title"
YAML
if [ -z "$(scan "$tmp/clean.yml")" ]; then
  ok "4. negative control — scanner stays quiet on the env: form"
else
  bad "4. scanner fired on the correct env: form (false positive)"
fi

# ---------------------------------------------------------------------------
# 5. The release title strips backticks, so a code span cannot reach the tag
#    message even if some future path reintroduces text splicing.
# ---------------------------------------------------------------------------
if grep -q 'gsub(/`/, "")' "$rel"; then
  ok "5. release title strips backticks in the awk extractor (defence in depth)"
else
  bad "5. release.yml awk extractor should strip backticks from the title"
fi

echo "─────────────────────────────────────"
echo "  Passed: $pass"
echo "  Failed: $fail"
if [ "$fail" -gt 0 ]; then
  echo "  workflow-injection-safety FAILED."
  exit 1
fi
echo "  workflow-injection-safety passed."
exit 0
