#!/usr/bin/env bash
# plugin-marketplace.test.sh — drift-guard for the plugin marketplaces (BRO-3627).
#
# .claude-plugin/marketplace.json (Claude Code) lists two plugins:
#   bstack         — the hooks + CLI plugin; its version must equal VERSION
#                    (and it must stay the first versioned entry, which is the
#                    one `bstack release tag` reads — BRO-3200).
#   bstack-skills  — the required companion skills, installed from the
#                    broomva/skills monorepo by path. Its skill set must equal
#                    {bstack primer} ∪ {roster entries with required: true}, so a
#                    roster change that forgets the plugin goes red here.
#
# .agents/plugins/marketplace.json (Codex) lists only bstack-skills — the hooks
# plugin is Claude-specific — and must carry the same skill paths, sourced by
# git URL from broomva/skills.
#
# Offline by default. The remote check (every listed path is a SKILL.md on
# broomva/skills main) follows roster-monorepo-sync.test.sh: auto when gh is
# authed, BSTACK_ROSTER_CHECK_REMOTE=1 requires it, =0 disables.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ROSTER="${BSTACK_SKILLS_YAML:-$HERE/references/companion-skills.yaml}"
MARKET="${BSTACK_MARKETPLACE_JSON:-$HERE/.claude-plugin/marketplace.json}"
CODEX_MARKET="${BSTACK_CODEX_MARKETPLACE_JSON:-$HERE/.agents/plugins/marketplace.json}"

PASS=0; FAIL=0; FAILED=()
ok()   { PASS=$((PASS + 1)); echo "  [pass] $1"; }
fail() { FAIL=$((FAIL + 1)); FAILED+=("$1"); echo "  [FAIL] $1"; }

# check <roster> <marketplace> — prints one "ok|fail<TAB>message" line per check.
check() {
  python3 -I - "$1" "$2" "$HERE/VERSION" <<'PY'
import json, sys
import yaml
roster_path, market_path, version_path = sys.argv[1:4]
try:
    m = json.load(open(market_path))
    roster = yaml.safe_load(open(roster_path))["skills"]
except Exception as e:
    print(f"fail\tmanifests do not parse: {e}"); sys.exit()
version = open(version_path).read().strip()
plugins = {p.get("name"): p for p in m.get("plugins", [])}

b = plugins.get("bstack")
first_versioned = next((p for p in m.get("plugins", []) if p.get("version")), None)
if b and b.get("version") == version and first_versioned is b:
    print(f"ok\tbstack entry is the first versioned plugin at VERSION {version}")
else:
    print(f"fail\tbstack entry must exist, be the first versioned plugin, and carry VERSION {version}")

s = plugins.get("bstack-skills")
if not s:
    print("fail\tno bstack-skills plugin"); sys.exit()
src = s.get("source") or {}
if src.get("source") == "github" and src.get("repo") == "broomva/skills" and s.get("strict") is False:
    print("ok\tbstack-skills sources broomva/skills with strict:false")
else:
    print("fail\tbstack-skills must source github broomva/skills with strict:false")
if "version" in s:
    print("fail\tbstack-skills must not pin a version (it follows broomva/skills main)")
else:
    print("ok\tbstack-skills carries no version")

paths = s.get("skills") or []
bad = [p for p in paths if not (p.startswith("./skills/") and p.count("/") == 3)]
if bad:
    print(f"fail\tpaths not shaped ./skills/<category>/<name>: {bad}")
else:
    print(f"ok\tall {len(paths)} paths are ./skills/<category>/<name>")
have = sorted(p.rstrip("/").rsplit("/", 1)[-1] for p in paths)
want = sorted({"bstack"} | {r["name"] for r in roster if r.get("required")})
if have == want:
    print(f"ok\tbstack-skills == primer + {len(want) - 1} required roster skills")
else:
    print(f"fail\tbstack-skills drifted from the required roster: missing {sorted(set(want) - set(have))}, extra {sorted(set(have) - set(want))}")
PY
}

report() {
  while IFS=$'\t' read -r verdict msg; do
    [ -n "$verdict" ] || continue
    if [ "$verdict" = ok ]; then ok "$msg"; else fail "$msg"; fi
  done
}

[ -f "$MARKET" ] || { echo "no marketplace at $MARKET"; exit 2; }
report < <(check "$ROSTER" "$MARKET")

# The guard can go red: a roster that gains a required skill must fail.
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
python3 -I - "$ROSTER" "$TMP/roster.yaml" <<'PY'
import sys, yaml
d = yaml.safe_load(open(sys.argv[1]))
d["skills"].append({"name": "zzz-probe", "repo": "broomva/skills", "required": True})
yaml.safe_dump(d, open(sys.argv[2], "w"))
PY
if check "$TMP/roster.yaml" "$MARKET" | grep -q $'^fail\tbstack-skills drifted.*zzz-probe'; then
  ok "red path: a new required roster skill missing from bstack-skills is reported"
else
  fail "red path: a new required roster skill was not reported"
fi

# Codex: same bstack-skills paths, git-URL source, no hooks plugin.
while IFS=$'\t' read -r verdict msg; do
  [ -n "$verdict" ] || continue
  if [ "$verdict" = ok ]; then ok "$msg"; else fail "$msg"; fi
done < <(python3 -I - "$MARKET" "$CODEX_MARKET" <<'PY'
import json, sys
try:
    claude = json.load(open(sys.argv[1]))
    codex = json.load(open(sys.argv[2]))
except Exception as e:
    print(f"fail\tcodex marketplace does not parse: {e}"); sys.exit()
names = [p.get("name") for p in codex.get("plugins", [])]
if names == ["bstack-skills"]:
    print("ok\tcodex marketplace lists only bstack-skills")
else:
    print(f"fail\tcodex marketplace must list only bstack-skills, got {names}")
    sys.exit()
c = codex["plugins"][0]
want = next(p for p in claude["plugins"] if p["name"] == "bstack-skills")["skills"]
if c.get("skills") == want:
    print(f"ok\tcodex bstack-skills carries the same {len(want)} paths as the Claude entry")
else:
    print("fail\tcodex bstack-skills paths differ from the Claude entry")
if c.get("source") == {"source": "url", "url": "https://github.com/broomva/skills.git"}:
    print("ok\tcodex bstack-skills sources broomva/skills by git URL")
else:
    print(f"fail\tcodex bstack-skills source must be the broomva/skills git URL, got {c.get('source')}")
PY
)

# Remote: every path is a SKILL.md on broomva/skills main.
REMOTE="${BSTACK_ROSTER_CHECK_REMOTE:-auto}"
if [ "$REMOTE" != "0" ] && command -v gh >/dev/null 2>&1 && gh auth status >/dev/null 2>&1; then
  TREE="$(gh api 'repos/broomva/skills/git/trees/main?recursive=1' --jq '.tree[].path' 2>/dev/null | grep -E '/SKILL\.md$')"
  if [ -n "$TREE" ]; then
    MISSING=""
    for p in $(python3 -I -c 'import json,sys;print(" ".join(next(x for x in json.load(open(sys.argv[1]))["plugins"] if x["name"]=="bstack-skills")["skills"]))' "$MARKET"); do
      grep -qxF "${p#./}/SKILL.md" <<<"$TREE" || MISSING="$MISSING $p"
    done
    if [ -z "$MISSING" ]; then ok "every bstack-skills path resolves on broomva/skills main"; else fail "paths missing on broomva/skills main:$MISSING"; fi
  elif [ "$REMOTE" = "1" ]; then
    fail "remote check required but could not list the broomva/skills tree"
  else
    echo "  [skip] remote check: could not list broomva/skills tree"
  fi
elif [ "$REMOTE" = "1" ]; then
  fail "remote check required (BSTACK_ROSTER_CHECK_REMOTE=1) but gh is unavailable/unauthed"
else
  echo "  [skip] remote path check (gh unavailable/unauthed; set BSTACK_ROSTER_CHECK_REMOTE=1 to require)"
fi

echo ""
echo "plugin-marketplace: $PASS passed, $FAIL failed"
if [ "$FAIL" -ne 0 ]; then
  printf '  - %s\n' "${FAILED[@]}"
  exit 1
fi
