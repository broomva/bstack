#!/usr/bin/env bash
# bstack/scripts/l1-reflex-audit-hook.sh — Claude Code Stop hook for L1
# (autonomic) reflex-compliance monitoring.
#
# Fires on session end. Scans the session transcript for evidence of which
# /autonomous reflexes fired. Writes one entry per session to
# .control/audit/l1-reflexes.jsonl with a reflex-compliance bitmask + dogfood
# receipt presence + anti-rationalization-line evaluation.
#
# Claude Code Stop hook protocol (May 2026):
#   stdin: { "transcript_path", "session_id", ... }
#   exit 0 always (Stop hooks cannot block)
#
# Reflexes checked (mirrors /autonomous SKILL.md 21-reflex pipeline):
#   r01_mechanism      P19 mechanism selection mentioned
#   r02_lens           P17 lens intake fired (role-x hook surfaced)
#   r03_snapshot       P15 snapshot (git status + branch surfaced)
#   r04_depchain       P14 dep-chain (upstream + downstream enumerated)
#   r05_worktree       P10 worktree decision stated
#   r06_ticket         P3 Linear ticket or PR body has trace
#   r07_dogfood_plan   P11 reflex 7 — Dogfood Plan section produced
#   r08_validation     P11 reflex 1 — validation plan stated
#   r09_first_write    First Edit/Write tool call
#   r10_empirical      P11 rules 2-5 — log-tail / Interceptor / curl evidence
#   r11_pr_opened      gh pr create call
#   r12_watcher        p9 watch call
#   r13_healing        p9 auto-heal call (if applicable)
#   r14_cross_review   /cross-review / cross-model adversarial fired
#   r15_deploy_verify  P11 rule 4 — deploy verification
#   r16_receipt        P11 reflex 6 — Dogfood Receipt produced
#   r17_pr_comments    PR comment loop addressed
#   r18_auto_merge     p9 auto-merge call
#   r19_cleanup        P10 post-merge janitor
#   r20_bridge         Bridge auto-fires (always; check Stop hook chain)
#   r21_bookkeeping    P6 / P16 candidate ledger updated
#
# Anti-rationalization: looks for "anti-rationalization check: yes" or
# equivalent in the dogfood receipt.

set -uo pipefail

WORKSPACE="${BROOMVA_WORKSPACE:-$(git rev-parse --show-toplevel 2>/dev/null || echo "$PWD")}"
LOG_DIR="$WORKSPACE/.control/audit"
LOG_FILE="$LOG_DIR/l1-reflexes.jsonl"

mkdir -p "$LOG_DIR" 2>/dev/null || exit 0

INPUT="$(cat 2>/dev/null || echo '{}')"

if ! command -v python3 >/dev/null 2>&1; then
    exit 0
fi

python3 - "$INPUT" "$LOG_FILE" <<'PYEOF'
import sys, json, os, time
from pathlib import Path

raw = sys.argv[1] if len(sys.argv) > 1 else "{}"
log_file = sys.argv[2]

try:
    data = json.loads(raw)
except Exception:
    data = {}

transcript_path = data.get("transcript_path")
session_id = data.get("session_id", "unknown")

# If no transcript, write a minimal entry and exit
if not transcript_path or not Path(transcript_path).exists():
    entry = {
        "ts": int(time.time() * 1000),
        "session_id": session_id,
        "transcript_missing": True,
    }
    with open(log_file, "a") as f:
        f.write(json.dumps(entry, separators=(",", ":")) + "\n")
    sys.exit(0)

# Read transcript (best-effort; JSONL of events from Claude Code)
try:
    with open(transcript_path) as f:
        # Collect text content + tool calls across all events
        text_blobs = []
        tool_calls = []
        for line in f:
            try:
                ev = json.loads(line)
            except Exception:
                continue
            # Heuristic: capture message text (multiple schema shapes possible)
            for k in ("text", "content", "message"):
                v = ev.get(k)
                if isinstance(v, str):
                    text_blobs.append(v)
                elif isinstance(v, list):
                    for item in v:
                        if isinstance(item, dict) and isinstance(item.get("text"), str):
                            text_blobs.append(item["text"])
            # Capture tool calls
            tool = ev.get("tool") or (ev.get("tool_use") if isinstance(ev.get("tool_use"), dict) else None)
            if isinstance(tool, dict):
                tool_calls.append(tool.get("name", "unknown"))
            elif isinstance(tool, str):
                tool_calls.append(tool)
except Exception:
    text_blobs = []
    tool_calls = []

corpus = "\n".join(text_blobs).lower()
calls = [c.lower() for c in tool_calls]

# Reflex detection (heuristic, intentionally permissive — looking for evidence
# that the reflex's discipline showed up in the session, not perfect parsing)
def saw(*needles):
    return any(n in corpus for n in needles)

def called(*names):
    return any(n.lower() in calls for n in names)

reflexes = {
    "r01_mechanism":     saw("p19 mechanism", "mechanism cube", "autonomous-continuation"),
    "r02_lens":          saw("role-x intake", "p17 reflex", "lens(es):"),
    "r03_snapshot":      saw("p15 state snapshot", "git status", "snapshot (p15)"),
    "r04_depchain":      saw("p14 dep-chain", "dep-chain trace", "upstream", "downstream"),
    "r05_worktree":      saw("p10 worktree", "worktree decision"),
    "r06_ticket":        saw("linear ticket", "bro-", "ticket"),
    "r07_dogfood_plan":  saw("dogfood plan", "**dogfood plan**"),
    "r08_validation":    saw("validation plan", "p11 validation"),
    "r09_first_write":   called("Edit", "Write", "MultiEdit"),
    "r10_empirical":     saw("interceptor", "screencapture", "curl", "log-tail"),
    "r11_pr_opened":     "gh pr create" in corpus or "pr created" in corpus or "github.com" in corpus,
    "r12_watcher":       saw("p9 watch", "gh pr checks", "watcher"),
    "r13_healing":       saw("p9 auto-heal", "ci heal"),
    "r14_cross_review":  saw("cross-review", "cross-model adversarial", "strata a", "strata b", "strata c"),
    "r15_deploy_verify": saw("deploy verification", "vercel preview", "preview url"),
    "r16_receipt":       saw("dogfood receipt", "**dogfood receipt**"),
    "r17_pr_comments":   saw("pr comment", "gh pr comment", "reviewer comment"),
    "r18_auto_merge":    saw("auto-merge", "p9 auto-merge", "auto_merge"),
    "r19_cleanup":       saw("post-merge cleanup", "p8 janitor", "branch janitor"),
    "r20_bridge":        True,  # Stop hook itself implies Bridge fires
    "r21_bookkeeping":   saw("bookkeeping", "candidate ledger", "crystallize"),
}

# Anti-rationalization check (P11 rule 7 — receipt's binary check)
anti_rat_yes = "anti-rationalization check: yes" in corpus or "anti-rationalization check:** yes" in corpus
anti_rat_no  = "anti-rationalization check: no"  in corpus or "anti-rationalization check:** no"  in corpus
anti_rat_present = anti_rat_yes or anti_rat_no

# Compliance summary
fired_count = sum(1 for v in reflexes.values() if v)
total = len(reflexes)
compliance_rate = fired_count / total if total else 0.0

entry = {
    "ts": int(time.time() * 1000),
    "session_id": session_id,
    "reflexes": reflexes,
    "compliance_rate": round(compliance_rate, 3),
    "fired_count": fired_count,
    "total_reflexes": total,
    "anti_rationalization": {
        "present": anti_rat_present,
        "value": "yes" if anti_rat_yes else ("no" if anti_rat_no else None),
    },
    "tool_call_count": len(tool_calls),
}

with open(log_file, "a") as f:
    f.write(json.dumps(entry, separators=(",", ":")) + "\n")
PYEOF

exit 0
