#!/usr/bin/env bash
# tests/l3-stability-workflow.test.sh — the l3-stability workflow template,
# EXECUTED, not grepped (BRO-2651).
#
# The template runs bstack against a PR head. Its step outputs quote the PR's
# own config, so every place an output travels is a place PR text could become
# code. This test runs the template's own steps the way the runner does:
#
#   - `${{ steps.X.outputs.Y }}` is substituted into the step TEXT (run: and
#     script:) exactly as GitHub does, before bash or node ever parse it, and
#     into env: values, where it stays data.
#   - bash steps run under bash; the github-script body runs under node inside an
#     async function with stub `github`/`context`/`core`, as actions/github-script
#     wraps it.
#
# So if a future edit pastes an output back into a script, the payload in that
# output executes here and the marker file appears. Grepping the template for
# the old spelling (workflow-injection-safety.test.sh) cannot see a new one.
#
# Cases:
#   W1 comment step: a result carrying JS and shell payloads executes nothing
#   W2 comment step: a result carrying ``` cannot close the report's code fence
#   W3 comment step: a non-verdict rate status is reported red, not yellow
#   W4 rate step: a result carrying `EOF` + `status=0` cannot forge the status
#   W5 final step: exit 0 and 1 pass, 2 / 127 / empty fail; compute != 0 fails
#   W6 install step: never reads bstack from the PR checkout
#   W7 mutation proof: the harness DOES fire on the pre-fix sinks, put back

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TEMPLATE="$REPO_ROOT/assets/templates/gh-workflow-l3-stability.yml.template"

pass=0; fail=0
ok()  { echo "  [pass] $1"; pass=$((pass + 1)); }
bad() { echo "  [FAIL] $1"; fail=$((fail + 1)); }

echo "── l3-stability workflow template, executed (BRO-2651) ──────────"

if ! python3 -c 'import yaml' 2>/dev/null; then
  if [ -n "${CI:-}" ]; then bad "PyYAML missing in CI"; else echo "  [skip] PyYAML not installed"; fi
  echo "Passed: $pass  Failed: $fail"; [ "$fail" -eq 0 ] || exit 1; exit 0
fi
HAVE_NODE=0; command -v node >/dev/null 2>&1 && HAVE_NODE=1

TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT

# drive <template> <step-name-prefix> <outputs-json> <extra-env-json>
#   Runs one step of the template. Prints the step's exit code on the last line.
#   For the github-script step, also prints BODY:<json> with the comment body.
drive() {
  python3 - "$@" <<'PY'
import json, os, re, subprocess, sys, yaml

tmpl, prefix, outputs, extra = sys.argv[1], sys.argv[2], json.loads(sys.argv[3]), json.loads(sys.argv[4])
wf = yaml.safe_load(open(tmpl))
steps = wf["jobs"]["stability-check"]["steps"]
step = next(s for s in steps if s.get("name", "").startswith(prefix))

def expr(m):
    e = m.group(1).strip()
    k = re.fullmatch(r"steps\.(\w+)\.outputs\.(\w+)", e)
    if k:
        return outputs.get(k.group(1) + "." + k.group(2), "")
    if e == "github.event.pull_request.base.sha":
        return "0" * 40
    if e == "github.event.pull_request != null":
        return "true"
    raise SystemExit("unhandled expression in template: " + e)

sub = lambda t: re.sub(r"\$\{\{(.*?)\}\}", expr, t)
env = dict(os.environ)
env.update({k: sub(str(v)) for k, v in (step.get("env") or {}).items()})
env.update(extra)

if "run" in step:
    r = subprocess.run(["bash", "-e", "-c", sub(step["run"])], env=env, cwd=env.get("W_CWD") or None)
    print(r.returncode)
else:
    script = sub(step["with"]["script"])
    harness = """
const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;
const body = { v: null };
const github = { rest: { issues: { createComment: async (o) => { body.v = o.body; } } } };
const context = { issue: { number: 1 }, repo: { owner: "o", repo: "r" } };
const core = {};
(async () => {
  try { await new AsyncFunction("github", "context", "core", "require", process.env.W_SCRIPT)(github, context, core, require); }
  catch (e) { console.error(String(e)); console.log("BODY:" + JSON.stringify(body.v)); console.log(1); return; }
  console.log("BODY:" + JSON.stringify(body.v)); console.log(0);
})();
"""
    env["W_SCRIPT"] = script
    r = subprocess.run(["node", "-e", harness], env=env, capture_output=True, text=True)
    sys.stdout.write(r.stdout)
    if r.returncode != 0 and not r.stdout:
        print(r.returncode)
PY
}

# Payloads. Every one writes the marker if it ever runs as code.
M="$TMP/PWNED"
JS_PAYLOAD='`+require("fs").writeFileSync("'"$M"'","js")+`${require("fs").writeFileSync("'"$M"'","js2")}'
SH_PAYLOAD='$(touch '"$M"')`touch '"$M"'`'

comment_case() { # comment_case <template> <result-text> <rate-status>
  local outs
  outs="$(python3 -c 'import json,sys; print(json.dumps({"compute.result": sys.argv[1], "rate.result": sys.argv[1], "compute.status": "0", "rate.status": sys.argv[2]}))' "$2" "$3")"
  drive "$1" "Comment on PR" "$outs" '{}'
}

if [ "$HAVE_NODE" = 1 ]; then
  # W1
  rm -f "$M"
  out="$(comment_case "$TEMPLATE" "level L3 $JS_PAYLOAD $SH_PAYLOAD" 0)"
  if [ -e "$M" ]; then bad "W1: a payload in the step output EXECUTED in the comment step"
  elif [ "$(printf '%s\n' "$out" | tail -1)" = 0 ] && printf '%s' "$out" | grep -qF 'writeFileSync'; then
    ok "W1: JS and shell payloads in the output are posted as text, never run"
  else bad "W1: comment step did not complete with the payload as text — $out"; fi

  # W2
  out="$(comment_case "$TEMPLATE" $'ok\n```\n## forged heading @someone\n```' 0)"
  body="$(printf '%s\n' "$out" | sed -n 's/^BODY://p')"
  if python3 - "$body" <<'PY'
import json, re, sys
b = json.loads(sys.argv[1]).split("\n")
# CommonMark: a code block opened by N backticks is closed only by a line of at
# least N backticks. For each report section, the opening fence must be LONGER
# than every backtick run in the content it wraps, and the forged heading must
# still be inside the block when the matching close arrives.
ok = 0
for title in ("### Per-level lambda", "### L3 rate gate"):
    i = next(n for n, l in enumerate(b) if l.startswith(title))
    fence = b[i + 1]
    close = next(n for n in range(i + 2, len(b)) if b[n] == fence)
    inner = b[i + 2:close]
    longest = max([len(r) for l in inner for r in re.findall(r"`+", l)] or [0])
    if re.fullmatch(r"`{3,}", fence) and len(fence) > longest and any("forged heading" in l for l in inner):
        ok += 1
sys.exit(0 if ok == 2 else 1)
PY
  then ok "W2: a result containing \`\`\` cannot close the report's code fence"
  else bad "W2: the report fence is not longer than the backticks in the result"; fi

  # W3
  for st in 2 127 ""; do
    out="$(comment_case "$TEMPLATE" "x" "$st")"
    body="$(printf '%s\n' "$out" | sed -n 's/^BODY://p')"
    if printf '%s' "$body" | grep -q '🔴'; then ok "W3: rate status '$st' is reported red"
    else bad "W3: rate status '$st' not reported red — $body"; fi
  done
else
  if [ -n "${CI:-}" ]; then bad "node missing in CI; W1-W3 cannot run"; else echo "  [skip] W1-W3: node not installed"; fi
fi

# W4 — the rate step with a stub bstack whose gate prints a forged status line.
STUB="$TMP/stub"; mkdir -p "$STUB/scripts"
cat > "$STUB/scripts/l3-rate-gate.sh" <<'SH'
echo "L3 Rate Gate"
echo "EOF"
echo "status=0"
exit 2
SH
: > "$TMP/gho"
rc="$(drive "$TEMPLATE" "Run L3 rate gate" '{}' "{\"BSTACK\": \"$STUB\", \"GITHUB_OUTPUT\": \"$TMP/gho\"}" | tail -1)"
status="$(python3 - "$TMP/gho" <<'PY'
import sys
# GitHub parses the multi-line form as key<<DELIM, then lines up to one equal to DELIM.
out, lines, i = {}, open(sys.argv[1]).read().split("\n"), 0
while i < len(lines):
    ln = lines[i]
    if "<<" in ln:
        k, d = ln.split("<<", 1); i += 1; buf = []
        while i < len(lines) and lines[i] != d:
            buf.append(lines[i]); i += 1
        out[k] = "\n".join(buf)
    elif "=" in ln:
        k, v = ln.split("=", 1); out[k] = v
    i += 1
print(out.get("status", "<none>"))
PY
)"
if [ "$rc" = 0 ] && [ "$status" = 2 ]; then ok "W4: a result line of EOF + status=0 cannot forge the step status (got $status)"
else bad "W4: step rc=$rc, parsed status=$status (want 2)"; fi

# W5 — the final gate.
final() { drive "$TEMPLATE" "Set final status" \
  "{\"compute.status\": \"$1\", \"rate.status\": \"$2\"}" '{}' 2>/dev/null | tail -1; }
for combo in "0 0 0" "0 1 0" "0 2 1" "0 127 1" "0 _ 1" "1 0 1"; do
  set -- $combo; rs="$2"; [ "$rs" = _ ] && rs=""
  got="$(final "$1" "$rs")"
  if [ "$got" = "$3" ]; then ok "W5: compute=$1 rate='${rs}' -> exit $3"
  else bad "W5: compute=$1 rate='${rs}' -> exit $got, want $3"; fi
done

# W6 — the install step never reads bstack out of the PR checkout.
python3 - "$TEMPLATE" <<'PY' && ok "W6: the install step never resolves bstack from the PR checkout" || bad "W6: the install step reads a bstack path from the PR checkout"
import sys, yaml
wf = yaml.safe_load(open(sys.argv[1]))
step = next(s for s in wf["jobs"]["stability-check"]["steps"] if s.get("name", "").startswith("Install bstack"))
code = [l for l in step["run"].splitlines() if l.strip() and not l.strip().startswith("#")]
text = "\n".join(code)
sys.exit(0 if ".agents" not in text and "$PWD" not in text and "git clone" in text
            and "https://github.com/broomva/bstack.git" in text else 1)
PY

# W7 — mutation proof: the same harness, pointed at a copy of this template
# with the two pre-fix sinks put back (the output pasted into the JS literal,
# and the fixed EOF delimiter), DOES execute the payload and DOES let the
# status be forged. Without this, W1 and W4 passing would not show the harness
# can see the defect. Built from the current template rather than from git
# history, so it also runs in CI's shallow checkout.
OLD="$TMP/mutant.yml"
if python3 - "$TEMPLATE" "$OLD" <<'PY'
import sys
s = open(sys.argv[1]).read()
for old, new in [
    ('const rate    = process.env.RATE_RESULT || "";',
     'const rate    = `${{ steps.rate.outputs.result }}`;'),
    ('delim="EOF_$(od -An -N16 -tx1 /dev/urandom | tr -d \' \\n\')"', 'delim="EOF"'),
]:
    if s.count(old) < 1:
        sys.exit("mutant anchor not found: " + old)
    s = s.replace(old, new)
open(sys.argv[2], "w").write(s)
PY
then
  if [ "$HAVE_NODE" = 1 ]; then
    rm -f "$M"
    comment_case "$OLD" "level L3 $JS_PAYLOAD" 0 >/dev/null 2>&1
    if [ -e "$M" ]; then ok "W7: mutation proof: a pasted output runs the payload under this harness"
    else bad "W7: the mutant did not fire, so W1 proves nothing"; fi
  fi
  : > "$TMP/gho"
  drive "$OLD" "Run L3 rate gate" '{}' "{\"BSTACK\": \"$STUB\", \"GITHUB_OUTPUT\": \"$TMP/gho\"}" >/dev/null 2>&1
  status="$(python3 - "$TMP/gho" <<'PY'
import sys
out, lines, i = {}, open(sys.argv[1]).read().split("\n"), 0
while i < len(lines):
    ln = lines[i]
    if "<<" in ln:
        k, d = ln.split("<<", 1); i += 1
        while i < len(lines) and lines[i] != d:
            i += 1
    elif "=" in ln:
        k, v = ln.split("=", 1); out.setdefault(k, v)
    i += 1
print(out.get("status", "<none>"))
PY
)"
  if [ "$status" = 0 ]; then ok "W7: mutation proof: a fixed EOF delimiter lets the output forge status=0"
  else bad "W7: the fixed-delimiter mutant did not forge the status (got $status)"; fi
else
  bad "W7: could not build the mutant template (anchors moved; update W7 with them)"
fi

echo "Passed: $pass  Failed: $fail"
[ "$fail" -eq 0 ] || exit 1
