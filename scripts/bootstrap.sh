#!/usr/bin/env bash
# bstack bootstrap — install the companion-skills roster + scaffold governance + wire hooks
#
# Four phases + loop wiring:
#   1. Skill install: npx skills add for each ROSTER entry
#   2. Workspace scaffold: install missing CLAUDE.md / AGENTS.md / .control/policy.yaml
#      / .control/arcs.yaml from assets/templates/ (idempotent — never overwrites)
#   3. Hooks wire-up: merge bstack hooks into .claude/settings.json (additive only)
#   3.5 RCS loop wiring: install-rcs-stability.sh deploys the loop-sensor Stop hook +
#       audit dir + L3 gates (G0/G1/G2 + rcs-parameters.toml) so the control loop is
#       actually wired + connected, not just declared. Skip with BSTACK_SKIP_RCS=1
#       (governance-only bootstrap). Mirrors the wizard path (onboard.sh).
#   4. bstack doctor --quiet to verify the primitive contract + loop closure (§23).
#
# Env escapes: BSTACK_SKIP_SKILLS=1 (skip Phase 1), BSTACK_SKIP_RCS=1 (skip Phase 3.5).
set -e

AGENTS_DIR="${HOME}/.agents/skills"
CLAUDE_DIR="${HOME}/.claude/skills"
WORKSPACE_DIR="${BROOMVA_WORKSPACE:-$PWD}"

mkdir -p "$AGENTS_DIR" "$CLAUDE_DIR"

# Phase 1 — install the companion-skills roster.
#
# Single source of truth: references/companion-skills.yaml, installed via
# bin/bstack-skills, which resolves every entry to `npx skills add broomva/skills
# --skill <name> -g` (inherits the BRO-1588 stdin-drain + global-install fixes).
# There is deliberately NO hardcoded skill→repo map here anymore: the previous map
# drifted to deleted standalone repos (broomva/agentic-control-kernel, broomva/p9,
# broomva/finance-substrate, …) after the BRO-1602 consolidation and 404'd every
# `bstack bootstrap` on a fresh host (BRO-1632). The roster is the only source.
#
# BSTACK_SKIP_SKILLS=1 short-circuits (tests/onboard.test.sh sets it so CI doesn't
# fan out to the real registry; also a governance-only bootstrap escape hatch).
BOOTSTRAP_BIN_DIR="$(cd "$(dirname "$0")/../bin" && pwd)"

echo "=== bstack bootstrap ==="

if [ "${BSTACK_SKIP_SKILLS:-0}" = "1" ]; then
  echo "BSTACK_SKIP_SKILLS=1 — skipping skill installation."
  echo "(Run \`bstack skills install\` manually to install the roster.)"
  echo ""
else
  echo "Installing the companion-skills roster (references/companion-skills.yaml)..."
  echo ""
  # Delegate to the roster-driven installer (missing-only, idempotent). It reads
  # companion-skills.yaml and installs each broomva/skills entry with --skill -g.
  if bash "$BOOTSTRAP_BIN_DIR/bstack-skills" install; then
    echo ""
    echo "=== bstack skills install complete ==="
  else
    echo ""
    echo "=== bstack skills install reported failures ==="
    echo "  Run 'bstack skills status' to see what's missing."
  fi
fi

# ─── Phase 2: scaffold missing governance files ────────────────────────────
# Idempotent: never overwrites existing files. Only installs when absent.
echo ""
echo "=== bstack governance scaffold ==="
BOOTSTRAP_SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SKILL_ROOT="$(cd "$BOOTSTRAP_SCRIPT_DIR/.." && pwd)"
TEMPLATES_DIR="$SKILL_ROOT/assets/templates"
WORKSPACE_NAME="$(basename "$WORKSPACE_DIR")"

scaffolded=0
preserved=0

scaffold_governance_file() {
    local target="$1"
    local template="$2"
    if [ -f "$WORKSPACE_DIR/$target" ]; then
        echo "  [keep] $target (existing — preserved)"
        preserved=$((preserved + 1))
        return
    fi
    if [ ! -f "$TEMPLATES_DIR/$template" ]; then
        echo "  [skip] $target (template missing in skill: $template)"
        return
    fi
    mkdir -p "$WORKSPACE_DIR/$(dirname "$target")"
    sed "s/{{WORKSPACE_NAME}}/$WORKSPACE_NAME/g" \
        "$TEMPLATES_DIR/$template" > "$WORKSPACE_DIR/$target"
    echo "  [scaffold] $target ← assets/templates/$template"
    scaffolded=$((scaffolded + 1))
}

scaffold_governance_file "CLAUDE.md" "CLAUDE.md.template"
scaffold_governance_file "AGENTS.md" "AGENTS.md.template"
scaffold_governance_file ".control/policy.yaml" "policy.yaml.template"
# Closure-contract arcs (the loop DEFINITIONS — the workspace's own editable
# copy; compute-arc-status.sh otherwise falls back to the bundled template).
scaffold_governance_file ".control/arcs.yaml" "arcs.yaml.template"
# Leverage setpoints — the reference signal r0 the self-improvement loop measures
# against. Without it, leverage-sensor.py runs referenceless (empty metrics, no r);
# doctor §23 can never certify closure. Idempotent — never overwrites, and
# `authored_by: bstack-default` stays until the human reviews + signs r0.
scaffold_governance_file ".control/leverage-setpoints.yaml" "leverage-setpoints.yaml"
# Control-systems manifest (plant/controller/shield/feedback formalization).
scaffold_governance_file "METALAYER.md" "METALAYER.md.template"
# Typed interface schemas (state/action/trace/evaluator/egri-event) — the typed
# contract the control loop validates against.
for _schema in state action trace evaluator egri-event; do
    scaffold_governance_file "schemas/${_schema}.schema.json" "schemas/${_schema}.schema.json"
done

echo "  scaffolded: $scaffolded | preserved: $preserved"

# ─── Phase 2.6: gitignore reconciliation + public-repo advisory ────────────
# The control loop splits into two file classes (the committable-vs-machine-local
# manifest). This phase reconciles .gitignore against it:
#   - machine-local (paths/telemetry) → MUST be ignored; add if missing.
#   - committable (team-wide, no secrets) → must NOT be ignored; WARN (don't
#     auto-un-ignore — un-ignoring a deliberately-private file is the human's call).
# Plus a public-repo advisory: on a public remote, committable governance becomes
# public — surface that, don't decide it silently. Never blocks.
echo ""
echo "=== bstack gitignore reconciliation ==="
GITIGNORE="$WORKSPACE_DIR/.gitignore"
# Machine-local globs that should always be ignored (absolute paths / runtime telemetry).
LOCAL_GLOBS=(".control/audit/*.jsonl")
# Committable substrate the loop needs — warn if a repo ignores these.
COMMITTABLE_FILES=(".control/arcs.yaml" ".control/rcs-parameters.toml" ".control/policy.yaml")

if command -v git >/dev/null 2>&1 && git -C "$WORKSPACE_DIR" rev-parse --git-dir >/dev/null 2>&1; then
    # Ensure machine-local globs are ignored (append the missing ones).
    _added_ignores=()
    for glob in "${LOCAL_GLOBS[@]}"; do
        if ! git -C "$WORKSPACE_DIR" check-ignore -q "${glob/\*/x}" 2>/dev/null; then
            _added_ignores+=("$glob")
        fi
    done
    if [ ${#_added_ignores[@]} -gt 0 ]; then
        {
            echo ""
            echo "# bstack RCS control-loop machine-local telemetry (not committed)"
            for g in "${_added_ignores[@]}"; do echo "$g"; done
        } >> "$GITIGNORE"
        echo "  [ignore] added ${#_added_ignores[@]} machine-local glob(s) to .gitignore: ${_added_ignores[*]}"
    else
        echo "  [ok] machine-local telemetry already ignored"
    fi

    # Warn if committable substrate is ignored (coverage gap — the loop needs these committed).
    for f in "${COMMITTABLE_FILES[@]}"; do
        if git -C "$WORKSPACE_DIR" check-ignore -q "$f" 2>/dev/null; then
            echo "  [warn] $f is gitignored but the loop needs it committed."
            echo "         → un-ignore it (after confirming it carries no secrets) so the control loop survives a fresh clone."
        fi
    done

    # Public-repo advisory (graceful if gh missing or not a GitHub remote).
    if command -v gh >/dev/null 2>&1; then
        _vis=$(gh repo view --json visibility --jq .visibility 2>/dev/null || true)
        if [ "$_vis" = "PUBLIC" ]; then
            echo "  [advisory] PUBLIC repo — scaffolded governance (CLAUDE.md/AGENTS.md/METALAYER.md/.control/*)"
            echo "             is committable and will be PUBLIC. Review for secrets before pushing; keep"
            echo "             machine-local files (.claude/settings.json, .control/audit/*.jsonl) ignored."
        fi
    fi
else
    echo "  [skip] not a git repo — gitignore reconciliation skipped"
fi

# ─── Phase 3: wire missing hooks into .claude/settings.json ────────────────
# Idempotent: never overwrites existing hook entries. Only adds missing ones.
echo ""
echo "=== bstack hooks wire-up ==="

# ── Plugin preference (BRO-1929) ───────────────────────────────────────────
# If bstack is installed as a skills-dir plugin (.claude-plugin/plugin.json at a
# Claude-scanned skills dir), PREFER it: enable bstack@skills-dir host-scope and
# skip hand-wiring the six hooks it already provides. Hand-wiring alongside the
# enabled plugin double-fires (two leverage-sensor writers; l3 double-count), and
# the plugin path is installer-immune. Legacy path (no manifest / no python3 /
# BSTACK_NO_PLUGIN=1) hand-wires everything as before.
# shellcheck source=scripts/lib/plugin-preference.sh
. "$SKILL_ROOT/scripts/lib/plugin-preference.sh"
PLUGIN_PREFERRED=0
if [ "${BSTACK_NO_PLUGIN:-0}" = "1" ]; then
    # Legacy forced: turn the real plugin OFF so hand-wiring its hooks below
    # won't double-fire alongside a still-enabled plugin.
    bstack_disable_plugin
elif bstack_plugin_preferred && command -v python3 >/dev/null 2>&1; then
    if bstack_enable_plugin; then
        PLUGIN_PREFERRED=1
        export BSTACK_PLUGIN_PREFERRED=1
        echo "  [plugin] preferring bstack@skills-dir — plugin-provided hooks will not be hand-wired"
    fi
fi

SETTINGS_FILE="$WORKSPACE_DIR/.claude/settings.json"
if command -v python3 >/dev/null 2>&1; then
    # python3 handles create-or-merge uniformly, applying the plugin filter +
    # double-wire migration. (The legacy sed-scaffold below is the no-python3
    # fallback only — where the plugin can't be enabled either, so hand-wiring
    # everything is correct: no plugin, no double-fire.)
    PLUGIN_PREFERRED="$PLUGIN_PREFERRED" \
    BSTACK_PLUGIN_HOOK_BASENAMES="$BSTACK_PLUGIN_HOOK_BASENAMES" \
    python3 - <<PYEOF
import json, os, re
from pathlib import Path

settings_path = Path("$SETTINGS_FILE")
template_path = Path("$TEMPLATES_DIR/settings.json.snippet")
workspace = "$WORKSPACE_DIR"
home = "$HOME"
bstack_repo = "$SKILL_ROOT"
plugin_preferred = os.environ.get("PLUGIN_PREFERRED") == "1"
plugin_hooks = set(os.environ.get("BSTACK_PLUGIN_HOOK_BASENAMES", "").split())

raw = template_path.read_text()
raw = raw.replace("\${BROOMVA_WORKSPACE}", workspace)
raw = raw.replace("\${BROOMVA_HOME}", home)
# bstack ships its own hook scripts from the clone (never a global skill) — see
# settings.json.snippet _comment + install-rcs-stability.sh's identical \$BSTACK_REPO wire.
raw = raw.replace("\$BSTACK_REPO", bstack_repo)

template = json.loads(raw)
template.pop("_comment", None)

if settings_path.exists():
    current = json.loads(settings_path.read_text())
else:
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    current = {}
current.setdefault("hooks", {})

def base(cmd):
    # Basename of the script a hook runs. Strip a trailing flag-args tail
    # (" --throttle 21600") so args don't leak into the name, while preserving
    # spaces inside the directory path (only leverage-sensor.py carries args).
    if not cmd:
        return ""
    head = re.split(r"\s+-", cmd, maxsplit=1)[0].rstrip()
    return Path(head).name

# Migration: with the plugin preferred, strip any hand-wired copy of a
# plugin-provided hook so it does not double-fire alongside the plugin.
removed = 0
if plugin_preferred:
    for event, blocks in list(current["hooks"].items()):
        for block in blocks:
            kept = [h for h in block.get("hooks", []) if base(h.get("command")) not in plugin_hooks]
            removed += len(block.get("hooks", [])) - len(kept)
            block["hooks"] = kept
        current["hooks"][event] = [b for b in blocks if b.get("hooks")]
        if not current["hooks"][event]:
            del current["hooks"][event]

added = 0
skipped = 0
for event, blocks in template.get("hooks", {}).items():
    current_blocks = current["hooks"].setdefault(event, [])
    for block in blocks:
        for hook in block.get("hooks", []):
            cmd = hook.get("command")
            if plugin_preferred and base(cmd) in plugin_hooks:
                skipped += 1
                continue
            # Dedup is MATCHER-SCOPED: the same script wired under a different
            # PreToolUse matcher is a distinct hook. control-gate is wired under
            # Bash / Write / Edit — a basename-only check would collapse all three
            # to one (dropping the Write/Edit gates on a fresh install).
            matcher = block.get("matcher")
            already = any(
                cb.get("matcher") == matcher
                and any(base(h.get("command")) == base(cmd) for h in cb.get("hooks", []))
                for cb in current_blocks
            )
            if already:
                print(f"  [keep] {event}: {base(cmd)} (already wired)")
            else:
                # Append a new block for this hook
                new_block = {"hooks": [hook]}
                if "matcher" in block:
                    new_block["matcher"] = block["matcher"]
                current_blocks.append(new_block)
                print(f"  [wire] {event}: {base(cmd)} ({hook.get('_bstack_primitive', 'P?')})")
                added += 1

settings_path.write_text(json.dumps(current, indent=2) + "\n")
_extra = f"; migrated out {removed} plugin copy(ies); skipped {skipped} plugin hook(s)" if plugin_preferred else ""
print(f"  added: {added} new hook(s){_extra}")
PYEOF
elif [ ! -f "$SETTINGS_FILE" ]; then
    echo "  [scaffold] .claude/settings.json ← assets/templates/settings.json.snippet (no python3; legacy hand-wire)"
    mkdir -p "$WORKSPACE_DIR/.claude"
    sed -e "s|\${BROOMVA_WORKSPACE}|$WORKSPACE_DIR|g" \
        -e "s|\${BROOMVA_HOME}|$HOME|g" \
        -e "s|\$BSTACK_REPO|$SKILL_ROOT|g" \
        "$TEMPLATES_DIR/settings.json.snippet" > "$SETTINGS_FILE"
else
    echo "  [skip] python3 not available; cannot merge into existing settings.json"
    echo "  manual: see assets/templates/settings.json.snippet"
fi

# ─── Phase 3.1: deploy workspace-resolved hook scripts ─────────────────────
# settings.json.snippet wires P1/P2/P6/P7 hooks at ${BROOMVA_WORKSPACE}/scripts/*.sh.
# Ship + deploy those scripts so the references resolve. Closes the dangling-hook
# safety gap: a wired-but-undelivered control-gate hook (P2) silently no-ops,
# leaving the safety shield non-functional on every workspace but the bstack
# origin. Idempotent: never overwrites a workspace's existing hook script.
echo ""
echo "=== bstack workspace hook deploy ==="
mkdir -p "$WORKSPACE_DIR/scripts"
WORKSPACE_HOOKS=(control-gate-hook.sh skill-freshness-hook.sh conversation-bridge-hook.sh knowledge-catalog-refresh-hook.sh)
deployed_hooks=0
for hook in "${WORKSPACE_HOOKS[@]}"; do
    src="$SKILL_ROOT/scripts/$hook"
    dst="$WORKSPACE_DIR/scripts/$hook"
    if [ ! -f "$src" ]; then
        echo "  [skip] $hook (not shipped in this bstack version)"
        continue
    fi
    if [ -f "$dst" ]; then
        echo "  [keep] scripts/$hook (existing — preserved)"
    elif cp "$src" "$dst" 2>/dev/null && chmod +x "$dst" 2>/dev/null; then
        echo "  [deploy] scripts/$hook ← bstack/scripts/$hook"
        deployed_hooks=$((deployed_hooks + 1))
    else
        echo "  [warn] could not deploy scripts/$hook (non-fatal)"
    fi
done
echo "  deployed: $deployed_hooks workspace hook script(s) (P1/P2/P6/P7)"

# ─── Phase 3.5: wire the RCS control loop (L0/L1 audit + L3 gates) ─────────
# Closes the split-brain: onboard.sh (the wizard) wired the loop here; the
# bootstrap command did not, leaving freshly-bootstrapped workspaces with
# governance files but an OPEN loop (no sensor, no audit dir). This
# deploys the loop-sensor (leverage-sensor) Stop hook + .control/audit/ + L3 gates
# via the same idempotent installer the wizard uses. Skip with BSTACK_SKIP_RCS=1.
if [ "${BSTACK_SKIP_RCS:-0}" = "1" ]; then
  echo ""
  echo "=== bstack RCS loop wiring ==="
  echo "BSTACK_SKIP_RCS=1 — skipping loop wiring (governance-only bootstrap)."
  echo "(Run \`bash scripts/install-rcs-stability.sh\` later to wire the loop.)"
else
  RCS_INSTALLER="${BOOTSTRAP_SCRIPT_DIR}/install-rcs-stability.sh"
  if [ -f "$RCS_INSTALLER" ]; then
    echo ""
    echo "=== bstack RCS loop wiring ==="
    # Non-blocking contract (matches onboard.sh): loop wiring must never abort
    # the bootstrap. bootstrap runs `set -e` but NOT `pipefail`, so the
    # pipeline's status is sed's (≈always 0) and the installer's exit is already
    # discarded; `|| true` is defensive belt-and-suspenders. The installer's
    # diagnostics still print through the pipe, and a silently-failed wiring is
    # caught downstream by doctor §23 + the canary's audit-dir/marker assertions.
    BROOMVA_WORKSPACE="$WORKSPACE_DIR" bash "$RCS_INSTALLER" 2>&1 | sed 's/^/  /' || true
  fi
fi

# ─── Phase 4: bstack doctor verification ───────────────────────────────────
# Always-active step; never blocks. Surfaces gaps in AGENTS.md / CLAUDE.md /
# .control/policy.yaml compliance with the bstack primitive contract.
DOCTOR_SCRIPT="${BOOTSTRAP_SCRIPT_DIR}/doctor.sh"
if [ -f "$DOCTOR_SCRIPT" ]; then
  echo ""
  echo "=== bstack doctor (primitive contract) ==="
  BROOMVA_WORKSPACE="$WORKSPACE_DIR" bash "$DOCTOR_SCRIPT" --quiet || true
fi

# --- Arcan skill sync ---
# If .arcan/ exists (Arcan agent is initialized), sync skills into .arcan/skills/
ARCAN_DIR="${PWD}/.arcan"
if [ -d "$ARCAN_DIR" ]; then
  SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
  if [ -f "${SCRIPT_DIR}/arcan-skills-sync.sh" ]; then
    echo ""
    bash "${SCRIPT_DIR}/arcan-skills-sync.sh" "$ARCAN_DIR"
  fi
elif [ -d "${HOME}/.agents/skills/bstack/scripts" ]; then
  SYNC_SCRIPT="${HOME}/.agents/skills/bstack/scripts/arcan-skills-sync.sh"
  if [ -f "$SYNC_SCRIPT" ] && [ -d "$ARCAN_DIR" ]; then
    echo ""
    bash "$SYNC_SCRIPT" "$ARCAN_DIR"
  fi
fi
