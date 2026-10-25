---
name: bookkeeping
description: >-
  Use when capturing graph-worthy knowledge (P6), regenerating
  docs/knowledge-index.md, scoring/promoting entities, linting the entity graph,
  or closing a session that produced new names/decisions/relations — never ask
  permission to file.
---

# bookkeeping — P6 knowledge engine

Capture and compound durable knowledge into `research/entities/` without asking. Pairs with [kg](../kg/SKILL.md) (load) and [broomva-workspace](../broomva-workspace/SKILL.md).

## Reflex (binding)

File graph-worthy material as part of the work: decisions, tools, people, patterns, typed relations. Report in one line after. Never ask "should I create an entry?" — that is the permission-to-document anti-pattern. Bounds: a Nous score or confidence gate stops spam; a standing "do not record" note, or private or client material, overrides silently.

Prefer a small substrate edit (`related:`, a claim, the body) plus a commit over a full pipeline for one inference.

## Mac commands (`BROOMVA_ROOT=/Volumes/Broomva/home/broomva`)

```bash
export BROOMVA_ROOT=/Volumes/Broomva/home/broomva
BK="$BROOMVA_ROOT/skills/skills/knowledge/bookkeeping"
# or the python3 path from ~/.agents/skills/bookkeeping when that layout is current

python3 "$BK/scripts/bookkeeping.py" index          # regenerate docs/knowledge-index.md
python3 "$BK/scripts/bookkeeping.py" lint --all     # graph health
python3 "$BK/scripts/bookkeeping.py" replay         # consolidate against a frozen snapshot; review, then --commit
# use `run` only for small scoped extractions — replay for cross-source consolidation
```

If the scripts path differs, locate `bookkeeping.py` under `"$BROOMVA_ROOT/skills"` before running it. Python does not need `--no-optional-locks`.

## GitHub-only / no Mac checkout

The catalog cannot be regenerated without the tree. Load through [kg](../kg/SKILL.md)'s GitHub fallback. For a single inference, open a write on the checkout (or a cloud agent) with the entity edit and a PR. Do not invent provenance.

## Privacy

Never promote CRM, client, or private material into the public graph or into artifacts.
