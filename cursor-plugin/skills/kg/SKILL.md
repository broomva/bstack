---
name: kg
description: >-
  Use when loading broomva knowledge-graph context: /kg load, what do we know
  about, relevant entities, graph routing, or topical load from
  research/entities before answering from workspace knowledge.
---

# kg — knowledge graph loader

Load relevant entities for a topic from the broomva knowledge graph (the model is the index).

## Architecture

- **Substrate (canonical):** `research/entities/**/*.md` in `broomva/workspace`
- **Catalog (routing):** `docs/knowledge-index.md` (regenerate with `bookkeeping index` when stale, older than 24h)
- **The agent is the query engine** after bodies are loaded. No SQL, no embeddings.

## When to use

Knowledge claims, "what do we know about X", routing before planning, graph discovery. Not for session history, web research, or writing entities (use [bookkeeping](../bookkeeping/SKILL.md), or a direct edit plus commit, per P6).

## How to load (prefer in order)

### 1. Local Mac checkout (best — full two-tier kg.py)

Root: `BROOMVA_ROOT=/Volumes/Broomva/home/broomva`, or `~/broomva` when that symlink is the checkout.

```bash
export BROOMVA_ROOT=/Volumes/Broomva/home/broomva
# Prefer the installed skill copy when present, else the repo copy:
python3 "$BROOMVA_ROOT/docs/skills/kg/kg.py" load "TOPIC" --n 10 --terms "synonym1,synonym2"
# Optional: --expand 1  --explain  --type concept  --body-search  --json
```

If a catalog-age warning fires in a write-authorized session, regenerate:

```bash
python3 "$BROOMVA_ROOT/skills/bookkeeping/scripts/bookkeeping.py" index
```

Otherwise report stale and continue with what loaded.

### 2. GitHub-only fallback (no Mac checkout, or the volume is unmounted)

1. Read `broomva/workspace` path `docs/knowledge-index.md` with the host's GitHub file tools (check `generated:` in frontmatter).
2. Score catalog blocks the way kg.py tier-1 does: exact slug +10, alias +8, slug substring +5, tag exact +4, claim +3, links/sources +1. Take top-N.
3. For each hit, read `research/entities/{path from catalog}`.
4. If fewer than N hits, or the best score is under 18, widen with a code search over `repo:broomva/workspace path:research/entities` for the topic terms (tier-2 substitute).

### 3. After load

Reason over bodies. Cite disk paths. If you infer a durable relation that is not in the substrate and the session is write-authorized, persist it with an entity `related:` or body edit plus a commit (P6). Report the loaded count, catalog age, and misses.

## Defaults

- N=10. Always pass `--terms` synonyms when the topic has jargon or acronyms.
- Scale N to the task. Do not dump the whole graph.
- Privacy: never load or echo client or CRM private material into public artifacts.
