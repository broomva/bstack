#!/usr/bin/env bash
# bstack repair — apply targeted fixes for gaps surfaced by doctor.
#
# Re-runs `bstack doctor` (--quiet), reads the gap list, and offers fixes:
#   - Missing CLAUDE.md / AGENTS.md / .control/policy.yaml → scaffold from template
#   - Missing policy block (ci_watch / ci_heal / auto_merge) → append from template
#   - Missing hook in .claude/settings.json → merge from snippet (≥ 0.2.3)
#
# Modes:
#   default     — interactive; asks before each fix
#   --apply-all — apply every fix without asking (CI / scripted use)
#   --dry-run   — list what would be fixed; do not write
#
# Always idempotent. Never destructive.

set -uo pipefail

INTERACTIVE=1
DRY_RUN=0
while [ $# -gt 0 ]; do
    case "$1" in
        --apply-all) INTERACTIVE=0; shift ;;
        --dry-run)   DRY_RUN=1; shift ;;
        --help|-h)
            grep -E '^#( |$)' "$0" | sed 's/^# \?//' | head -25
            exit 0
            ;;
        *) shift ;;
    esac
done

WORKSPACE_DIR="${BROOMVA_WORKSPACE:-$PWD}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SKILL_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
TEMPLATES_DIR="$SKILL_ROOT/assets/templates"
DOCTOR="$SCRIPT_DIR/doctor.sh"

if [ ! -f "$DOCTOR" ]; then
    echo "bstack repair: doctor.sh not found at $DOCTOR" >&2
    exit 2
fi

confirm() {
    [ "$INTERACTIVE" = "0" ] && return 0
    [ "$DRY_RUN" = "1" ] && return 1
    local prompt="$1"
    read -r -p "$prompt [y/N] " reply
    [ "${reply:-N}" = "y" ] || [ "${reply:-N}" = "Y" ]
}

scaffold_if_missing() {
    local target="$1"
    local template="$2"
    if [ -f "$WORKSPACE_DIR/$target" ]; then return; fi
    if [ ! -f "$TEMPLATES_DIR/$template" ]; then
        echo "  [skip] $target — template missing in skill: $template"
        return
    fi
    if confirm "Scaffold $target from $template?"; then
        mkdir -p "$WORKSPACE_DIR/$(dirname "$target")"
        sed "s/{{WORKSPACE_NAME}}/$(basename "$WORKSPACE_DIR")/g" \
            "$TEMPLATES_DIR/$template" > "$WORKSPACE_DIR/$target"
        echo "  [fix] scaffolded $target"
    elif [ "$DRY_RUN" = "1" ]; then
        echo "  [dry-run] would scaffold $target"
    else
        echo "  [skip] $target (declined)"
    fi
}

append_policy_block_if_missing() {
    local block_name="$1"
    local pol="$WORKSPACE_DIR/.control/policy.yaml"
    if [ ! -f "$pol" ]; then
        echo "  [skip] $block_name — .control/policy.yaml absent (scaffold first)"
        return
    fi
    if grep -qE "^${block_name}:" "$pol"; then return; fi
    if confirm "Append $block_name: block to .control/policy.yaml?"; then
        python3 - "$TEMPLATES_DIR/policy.yaml.template" "$block_name" "$pol" <<'PYEOF'
import sys
from pathlib import Path

template, block_name, target = sys.argv[1], sys.argv[2], sys.argv[3]
text = Path(template).read_text()
lines = text.splitlines()
in_block = False
block_lines = []
for line in lines:
    if line.startswith(f"{block_name}:"):
        in_block = True
        block_lines.append(line)
        continue
    if in_block:
        if line and not line.startswith(" ") and not line.startswith("#") and ":" in line:
            break
        block_lines.append(line)

if not block_lines:
    sys.exit(0)
while block_lines and not block_lines[-1].strip():
    block_lines.pop()
with Path(target).open("a") as f:
    f.write("\n# === Appended by bstack repair ===\n")
    f.write("\n".join(block_lines))
    f.write("\n")
print(f"  [fix] appended {block_name}: block ({len(block_lines)} lines)")
PYEOF
    elif [ "$DRY_RUN" = "1" ]; then
        echo "  [dry-run] would append $block_name: block"
    else
        echo "  [skip] $block_name (declined)"
    fi
}

# ── Development Philosophy backfill (helper) ───────────────────────────────
# Inserts the `## Development Philosophy` section (templated since bstack 0.24.0)
# into an existing AGENTS.md / CLAUDE.md that predates it. The scaffold is
# idempotent-never-overwrite, so existing workspaces never receive newly-
# templated *content* — only freshly-created files. This closes that gap for
# this one section.
#
# Idempotent + non-destructive: skips if the section is already present; skips
# with a warning if the insertion anchor (`## Bstack Core Automation
# Primitives`) is absent (never guesses a location). Extracts the section
# verbatim from the template (heading → Primitives anchor, exclusive) via files
# only — no shell interpolation of the content — so backticks/quotes/pipes in
# the section survive intact.
backfill_philosophy_section() {
    local target="$1"        # AGENTS.md | CLAUDE.md
    local template="$2"      # AGENTS.md.template | CLAUDE.md.template
    local tgt="$WORKSPACE_DIR/$target"
    local tpl="$TEMPLATES_DIR/$template"
    local anchor="## Bstack Core Automation Primitives"
    # Anchor regex tolerant of trailing whitespace + CRLF (a realistic line-ending
    # variant must NOT become a silent no-op). The CR is stripped before matching.
    local anchor_re='^## Bstack Core Automation Primitives[[:space:]]*$'
    [ -f "$tgt" ] || return                                   # nothing to backfill into
    [ -f "$tpl" ] || { echo "  [skip] $target philosophy — template missing: $template"; return; }
    grep -qE "^## Development Philosophy" "$tgt" && return     # already present (idempotent)
    # BOTH source and destination must carry the anchor: the template needs it to
    # *bound* the extracted section (else awk runs heading→EOF and over-copies);
    # the target needs it to *locate* the insertion point. grep the files directly
    # — `[[:space:]]*$` absorbs trailing spaces AND a trailing CR, so this is
    # CRLF-tolerant without a `tr | grep -q` pipe (which under `set -o pipefail`
    # fails spuriously: grep -q closes the pipe early on a match → SIGPIPE on tr).
    if ! grep -qE "$anchor_re" "$tpl"; then
        echo "  [skip] $target philosophy — template lacks anchor; cannot bound section"
        return
    fi
    if ! grep -qE "$anchor_re" "$tgt"; then
        echo "  [skip] $target philosophy — anchor '$anchor' not found (insert manually)"
        return
    fi
    if [ "$DRY_RUN" = "1" ]; then
        echo "  [dry-run] would backfill Development Philosophy into $target"
        return
    fi
    if ! confirm "Backfill Development Philosophy section into $target?"; then
        echo "  [skip] $target philosophy (declined)"
        return
    fi
    local secfile tmp
    secfile="$(mktemp)" || { echo "  [skip] $target philosophy — mktemp failed"; return; }
    # Section block = template lines from the heading up to (excluding) the anchor.
    # CR is stripped first so the awk delimiters match on a CRLF template too.
    # The pipeline's exit status is checked (under `set -o pipefail` a tr/awk/write
    # failure surfaces here); it is SIGPIPE-safe because awk drains all of tr's
    # output (no early close). The content checks below catch a partial/empty write.
    if ! tr -d '\r' < "$tpl" | awk '
        /^## Development Philosophy$/                    { f=1 }
        /^## Bstack Core Automation Primitives[ \t]*$/   { f=0 }
        f { print }
    ' > "$secfile"; then
        echo "  [skip] $target philosophy — extraction pipeline failed"
        rm -f "$secfile"
        return
    fi
    if [ ! -s "$secfile" ] || ! grep -qE "^## Development Philosophy" "$secfile"; then
        echo "  [skip] $target philosophy — could not extract section from $template"
        rm -f "$secfile"
        return
    fi
    tmp="$(mktemp)" || { echo "  [skip] $target philosophy — mktemp failed"; rm -f "$secfile"; return; }
    # Insert the section immediately before the first anchor line (CR/space-tolerant).
    if ! awk -v secfile="$secfile" '
        function isanchor(s) { sub(/\r$/, "", s); sub(/[ \t]+$/, "", s); return (s == "## Bstack Core Automation Primitives") }
        isanchor($0) && !done {
            while ((getline line < secfile) > 0) print line
            close(secfile)
            done = 1
        }
        { print }
    ' "$tgt" > "$tmp"; then
        echo "  [skip] $target philosophy — insertion failed (awk)"
        rm -f "$secfile" "$tmp"
        return
    fi
    rm -f "$secfile"
    # Verify the section actually landed BEFORE committing the write. Guards the
    # probe-vs-inserter mismatch and any awk no-op — never report a false [fix].
    if ! grep -qE "^## Development Philosophy" "$tmp"; then
        echo "  [skip] $target philosophy — anchor not matched during insert (no change)"
        rm -f "$tmp"
        return
    fi
    if ! mv "$tmp" "$tgt"; then
        echo "  [skip] $target philosophy — could not write $target"
        rm -f "$tmp"
        return
    fi
    echo "  [fix] backfilled Development Philosophy into $target"
}

# Backfill the §P6 "Retrieval discipline (/kg for discovery, never substrate
# grep)" reflex paragraph into an existing AGENTS.md that predates it (templated
# in the §P6 section since the BRO-1426 reflex shipped). Same idempotent-never-
# overwrite gap as the philosophy section: scaffold only creates whole files, so
# existing workspaces never receive newly-templated *content* — this closes that
# gap for this one paragraph.
#
# Idempotent + non-destructive: skips if the marker phrase is already present;
# skips with a warning if the §P6→next-heading insertion anchor is absent (never
# guesses a location). Extracts the paragraph verbatim from the template (the
# `**Retrieval discipline` line → next `### ` heading, exclusive) via files only
# — no shell interpolation of the content — so backticks/quotes/pipes survive.
backfill_retrieval_discipline() {
    local target="$1"        # AGENTS.md
    local template="$2"      # AGENTS.md.template
    local tgt="$WORKSPACE_DIR/$target"
    local tpl="$TEMPLATES_DIR/$template"
    # Shared phrase marker with doctor §4c so detection and backfill agree on
    # "present". "substrate grep" is the coined phrase and appears only in this
    # reflex; it catches the verbatim template paragraph AND wording variants that
    # retain the phrase (e.g. a hand-authored "not a substrate grep").
    local marker="substrate grep"
    [ -f "$tgt" ] || return                                   # nothing to backfill into
    [ -f "$tpl" ] || { echo "  [skip] $target retrieval-discipline — template missing: $template"; return; }
    grep -qF "$marker" "$tgt" && return                       # already present (idempotent)
    # Semantic guard: also treat a reflex reworded to DROP the phrase as present
    # (an additive backfill of a second paragraph is worse than a missed one).
    # The `**Retrieval discipline` lead is the reflex's structural signature.
    grep -qE "^\*\*Retrieval discipline" "$tgt" && return     # present under different wording
    if ! grep -qF "$marker" "$tpl"; then
        echo "  [skip] $target retrieval-discipline — template lacks the paragraph"
        return
    fi
    # Anchor: the target must have a §P6 section followed by a later `### ` heading
    # (the paragraph lands at the END of §P6, before whatever heading follows it).
    if ! awk '
        function clean(s){ sub(/\r$/,"",s); return s }
        { l=clean($0); if (l ~ /^### P6([ :]|$)/) p6=1; else if (p6 && l ~ /^### /) {found=1; exit} }
        END { exit (found ? 0 : 1) }
    ' "$tgt"; then
        echo "  [skip] $target retrieval-discipline — no §P6→heading anchor (insert manually)"
        return
    fi
    if [ "$DRY_RUN" = "1" ]; then
        echo "  [dry-run] would backfill P6 retrieval-discipline into $target"
        return
    fi
    if ! confirm "Backfill P6 retrieval-discipline reflex into $target?"; then
        echo "  [skip] $target retrieval-discipline (declined)"
        return
    fi
    local secfile tmp
    secfile="$(mktemp)" || { echo "  [skip] $target retrieval-discipline — mktemp failed"; return; }
    # Extract the paragraph: from the `**Retrieval discipline` line up to (excluding)
    # the next `### ` heading. CR stripped first so awk delimiters match a CRLF template.
    # Flag-clear on the boundary (NOT `exit`): under `set -o pipefail` an early awk
    # exit closes the pipe and `tr` dies with SIGPIPE → the pipeline fails spuriously.
    # Draining all of tr's output keeps the pipeline status clean.
    if ! tr -d '\r' < "$tpl" | awk '
        /^\*\*Retrieval discipline/ { f=1 }
        f && /^### / { f=0 }
        f { print }
    ' > "$secfile"; then
        echo "  [skip] $target retrieval-discipline — extraction pipeline failed"
        rm -f "$secfile"
        return
    fi
    if [ ! -s "$secfile" ] || ! grep -qF "$marker" "$secfile"; then
        echo "  [skip] $target retrieval-discipline — could not extract from $template"
        rm -f "$secfile"
        return
    fi
    tmp="$(mktemp)" || { echo "  [skip] $target retrieval-discipline — mktemp failed"; rm -f "$secfile"; return; }
    # Insert at the END of §P6 — before the first `### ` heading after `### P6`.
    if ! awk -v secfile="$secfile" '
        function clean(s){ sub(/\r$/,"",s); return s }
        { line = clean($0) }
        seenP6 && line ~ /^### / && !done {
            while ((getline s < secfile) > 0) print s
            close(secfile)
            done = 1
        }
        { if (line ~ /^### P6([ :]|$)/) seenP6 = 1; print }
    ' "$tgt" > "$tmp"; then
        echo "  [skip] $target retrieval-discipline — insertion failed (awk)"
        rm -f "$secfile" "$tmp"
        return
    fi
    rm -f "$secfile"
    # Verify the paragraph actually landed before committing the write.
    if ! grep -qF "$marker" "$tmp"; then
        echo "  [skip] $target retrieval-discipline — anchor not matched during insert (no change)"
        rm -f "$tmp"
        return
    fi
    if ! mv "$tmp" "$tgt"; then
        echo "  [skip] $target retrieval-discipline — could not write $target"
        rm -f "$tmp"
        return
    fi
    echo "  [fix] backfilled P6 retrieval-discipline reflex into $target"
}

# ── Hook re-wire (helper) ──────────────────────────────────────────────────
# Idempotently merges every hook in assets/templates/settings.json.snippet
# into $WORKSPACE_DIR/.claude/settings.json. Existing entries are never
# overwritten or reordered — only missing entries are appended. This closes
# the upgrade gap where new hooks shipped in a snippet update would not
# reach existing installs without manually re-running `bstack bootstrap`.
#
# Run before doctor's early-exit so a fully-compliant workspace still picks
# up newly-templated hooks. Silent when everything is in sync.
merge_hooks_into_settings() {
    local snippet="$TEMPLATES_DIR/settings.json.snippet"
    local target="$WORKSPACE_DIR/.claude/settings.json"
    if [ ! -f "$snippet" ]; then
        echo "  [skip] hook merge — snippet not found at $snippet"
        return
    fi
    if ! command -v python3 >/dev/null 2>&1; then
        echo "  [skip] hook merge — python3 not available"
        echo "         manual: copy entries from $snippet into $target"
        return
    fi
    if [ ! -f "$target" ]; then
        if confirm "Scaffold .claude/settings.json from snippet?"; then
            mkdir -p "$(dirname "$target")"
            sed -e "s|\${BROOMVA_WORKSPACE}|$WORKSPACE_DIR|g" \
                -e "s|\${BROOMVA_HOME}|$HOME|g" \
                "$snippet" > "$target"
            echo "  [fix] scaffolded .claude/settings.json"
        elif [ "$DRY_RUN" = "1" ]; then
            echo "  [dry-run] would scaffold .claude/settings.json"
        else
            echo "  [skip] .claude/settings.json (declined)"
        fi
        return
    fi
    python3 - "$snippet" "$target" "$WORKSPACE_DIR" "$HOME" "$DRY_RUN" <<'PYEOF'
import json
import sys
from pathlib import Path

snippet_path, target_path, workspace, home, dry_run_str = sys.argv[1:6]
dry_run = dry_run_str == "1"

raw = Path(snippet_path).read_text()
raw = raw.replace("${BROOMVA_WORKSPACE}", workspace).replace("${BROOMVA_HOME}", home)
template = json.loads(raw)
template.pop("_comment", None)

target = json.loads(Path(target_path).read_text())
target.setdefault("hooks", {})

added = []
for event, blocks in template.get("hooks", {}).items():
    current_blocks = target["hooks"].setdefault(event, [])
    for block in blocks:
        for hook in block.get("hooks", []):
            cmd = hook.get("command", "")
            script_name = Path(cmd).name
            already = any(
                any(Path(h.get("command", "")).name == script_name
                    for h in cb.get("hooks", []))
                for cb in current_blocks
            )
            if already:
                continue
            matching = next(
                (cb for cb in current_blocks if cb.get("matcher") == block.get("matcher")),
                None,
            )
            if matching is None:
                new_block = {"hooks": [hook]}
                if "matcher" in block:
                    new_block["matcher"] = block["matcher"]
                current_blocks.append(new_block)
            else:
                matching.setdefault("hooks", []).append(hook)
            label = f"{event}"
            if block.get("matcher"):
                label += f"[{block['matcher']}]"
            added.append(f"{label}: {script_name}")

if not added:
    print("  ✓ all template hooks already wired")
    sys.exit(0)

if dry_run:
    print(f"  [dry-run] would add {len(added)} hook(s):")
    for line in added:
        print(f"    + {line}")
    sys.exit(0)

Path(target_path).write_text(json.dumps(target, indent=2) + "\n")
print(f"  [fix] added {len(added)} hook(s):")
for line in added:
    print(f"    + {line}")
PYEOF
}

# ── Workspace hook deploy (helper) ─────────────────────────────────────────
# Ships the P1/P2/P6/P7 hook scripts into $WORKSPACE_DIR/scripts/ so the hook
# references wired by settings.json.snippet resolve. Closes the dangling-hook
# safety gap (a wired-but-undelivered control-gate hook silently no-ops) on
# workspaces bootstrapped before these scripts shipped. Idempotent — never
# overwrites a workspace's existing hook script.
deploy_workspace_hooks() {
    local hooks=(control-gate-hook.sh skill-freshness-hook.sh conversation-bridge-hook.sh knowledge-catalog-refresh-hook.sh)
    local n=0
    for hook in "${hooks[@]}"; do
        local src="$SKILL_ROOT/scripts/$hook"
        local dst="$WORKSPACE_DIR/scripts/$hook"
        [ -f "$src" ] || continue
        [ -f "$dst" ] && continue
        if [ "$DRY_RUN" = "1" ]; then
            echo "  [dry-run] would deploy scripts/$hook"
        else
            mkdir -p "$WORKSPACE_DIR/scripts"
            if cp "$src" "$dst" 2>/dev/null && chmod +x "$dst" 2>/dev/null; then
                echo "  [fix] deployed scripts/$hook (control-gate=P2 safety shield)"
                n=$((n + 1))
            else
                echo "  [warn] could not deploy scripts/$hook (non-fatal)"
            fi
        fi
    done
    [ "$n" -gt 0 ] && echo "  [fix] deployed $n workspace hook script(s)"
    return 0
}

# ── Run doctor to identify gaps ────────────────────────────────────────────
echo "[bstack repair] running doctor to identify gaps..."
echo ""
GAPS_OUTPUT=$(BROOMVA_WORKSPACE="$WORKSPACE_DIR" bash "$DOCTOR" --quiet 2>&1 || true)

# Always attempt hook merge before the early-exit on "fully bstack-compliant".
# The merge is idempotent and prints nothing when every templated hook is
# already wired — so a compliant workspace still sees no extra noise.
if [ "$DRY_RUN" = "1" ] || confirm "Merge missing hooks from settings.json.snippet into .claude/settings.json?"; then
    merge_hooks_into_settings
fi

# Deploy the hook SCRIPTS the merge just wired references to (idempotent;
# unconditional like the philosophy backfill — closes the dangling-hook gap).
deploy_workspace_hooks

# Backfill templated-since-0.24.0 governance content that even a *compliant*
# (pre-0.24.0) workspace can lack — run BEFORE the compliance early-exit, like
# the hook merge above, because the Development Philosophy advisory is not a
# GAP (so doctor still reports "fully bstack-compliant" without it).
backfill_philosophy_section "AGENTS.md" "AGENTS.md.template"
backfill_philosophy_section "CLAUDE.md" "CLAUDE.md.template"
backfill_retrieval_discipline "AGENTS.md" "AGENTS.md.template"

if echo "$GAPS_OUTPUT" | grep -q "fully bstack-compliant"; then
    echo "  ✓ no other gaps — workspace already bstack-compliant"
    exit 0
fi

echo "$GAPS_OUTPUT"
echo ""
echo "[bstack repair] applying fixes..."
echo ""

# ── Governance files ───────────────────────────────────────────────────────
echo "$GAPS_OUTPUT" | grep -q "CLAUDE.md missing" && scaffold_if_missing "CLAUDE.md" "CLAUDE.md.template"
echo "$GAPS_OUTPUT" | grep -q "AGENTS.md missing" && scaffold_if_missing "AGENTS.md" "AGENTS.md.template"
echo "$GAPS_OUTPUT" | grep -q ".control/policy.yaml missing" && scaffold_if_missing ".control/policy.yaml" "policy.yaml.template"

# ── policy.yaml blocks ─────────────────────────────────────────────────────
echo "$GAPS_OUTPUT" | grep -q "ci_watch: block missing" && append_policy_block_if_missing "ci_watch"
echo "$GAPS_OUTPUT" | grep -q "ci_heal: block missing" && append_policy_block_if_missing "ci_heal"
echo "$GAPS_OUTPUT" | grep -q "auto_merge: block missing" && append_policy_block_if_missing "auto_merge"

# ── RCS multi-layer stability gate flow (v0.15.0+) ───────────────────────
# §14, §15 (L3), §16 (L0), §17 (L1), §18 (L2), §19 (composite) surface
# missing audit logs, missing hooks, missing parameters.toml. Run the
# unified installer if any are flagged. Installer is idempotent.
RCS_NEEDED=0
echo "$GAPS_OUTPUT" | grep -qE "G[012] (Claude Code|git pre-commit|GitHub Actions)" && RCS_NEEDED=1
echo "$GAPS_OUTPUT" | grep -q "no .control/rcs-parameters.toml" && RCS_NEEDED=1
echo "$GAPS_OUTPUT" | grep -q "RCS lambda <= 0" && RCS_NEEDED=1
echo "$GAPS_OUTPUT" | grep -q "no L0 audit log" && RCS_NEEDED=1
echo "$GAPS_OUTPUT" | grep -q "no L1 audit log" && RCS_NEEDED=1
if [ "$RCS_NEEDED" = "1" ]; then
    RCS_INSTALLER="$SKILL_ROOT/scripts/install-rcs-stability.sh"
    L3_INSTALLER="$SKILL_ROOT/scripts/install-l3-stability.sh"
    if [ -f "$RCS_INSTALLER" ]; then
        if [ "$DRY_RUN" = "1" ] || confirm "Run install-rcs-stability.sh to deploy missing multi-layer gate flow?"; then
            BROOMVA_WORKSPACE="$WORKSPACE_DIR" bash "$RCS_INSTALLER" 2>&1 | sed 's/^/  /'
        fi
    elif [ -f "$L3_INSTALLER" ]; then
        # Fallback to v0.14.0 installer if rcs one isn't present
        if [ "$DRY_RUN" = "1" ] || confirm "Run install-l3-stability.sh to deploy L3 gate flow (v0.14.0 fallback)?"; then
            BROOMVA_WORKSPACE="$WORKSPACE_DIR" bash "$L3_INSTALLER" 2>&1 | sed 's/^/  /'
        fi
    fi
fi

echo ""
echo "=== post-repair doctor pass ==="
BROOMVA_WORKSPACE="$WORKSPACE_DIR" bash "$DOCTOR" --quiet || true

exit 0
