# bstack workspace conventions

Conventions that apply to **scripts and skills running inside a bstack workspace**. Distinct from `references/primitives.md` (which defines the primitive contract) and `references/dogfood-patterns.md` (which defines per-stack validation patterns). This file documents cross-cutting workspace conventions — the things every script should follow but no single primitive owns.

Each convention crystallizes here only after **rule-of-three** (≥3 distinct callsites, concrete mechanism, stated invariant, stated failure mode) per the **Crystallize (P16)** discipline. Single-instance conventions live in the candidate ledger at the bottom of this file until they earn promotion.

---

## C1 — `BROOMVA_ROOT` env var as the workspace-root override

**Rule**: any script that needs to resolve "where is the workspace root?" MUST honor `BROOMVA_ROOT` env var with a fallback to `$HOME/broomva`.

**Why**: enables (a) CI runners where the workspace isn't at `~/broomva`, (b) co-developers with different host layouts, (c) isolated benchmark/test fixtures under `/tmp/`, (d) parallel worktrees that need to point at a different root than the default.

**Canonical patterns**

Python (top of module, single source of truth — read once at import time):

```python
import os
from pathlib import Path
BROOMVA_ROOT = Path(os.environ.get("BROOMVA_ROOT", Path.home() / "broomva"))
ENTITIES_DIR = BROOMVA_ROOT / "research" / "entities"
# …
```

Bash (top of script, with shell-default expansion):

```bash
BROOMVA_ROOT="${BROOMVA_ROOT:-${HOME}/broomva}"
POLICY_FILE="${BROOMVA_ROOT}/.control/policy.yaml"
# …
```

**Invariant**: every workspace path is derived from `BROOMVA_ROOT`; no script hardcodes `Path.home() / "broomva"` or `~/broomva` directly past the env-var resolution.

**Failure mode prevented**: scripts that hardcode `~/broomva` silently overwrite the live workspace when invoked with non-standard layouts. The benchmark suite (`scripts/bench-kg-haystack.py` in broomva/workspace) surfaced this concretely — `bookkeeping index` running with `BROOMVA_ROOT=/tmp/x` would silently write to `~/broomva/docs/knowledge-index.md` because it ignored the env var. Forbids that class of regression.

**Known callsites** (BRO-1223 follow-up — these are why this convention earned rule-of-three):

1. `~/.claude/skills/kg/scripts/kg.py` (and the published broomva/kg v0.2.x)
2. `~/broomva/skills/bookkeeping/scripts/bookkeeping.py`
3. `~/broomva/scripts/bench-kg.py`
4. `~/broomva/scripts/bench-kg-haystack.py`
5. `~/broomva/scripts/knowledge-catalog-refresh-hook.sh`
6. `~/broomva/skills/bookkeeping/tests/test_index.py`

**Historical synonym**: `BROOMVA_WORKSPACE` was used by `bstack/scripts/doctor.sh` (and downstream `scripts/compute-lambda.sh` invocations) before this convention was crystallized. Treat `BROOMVA_WORKSPACE` as **semantically equivalent** to `BROOMVA_ROOT` for backward compatibility, but new scripts SHOULD use `BROOMVA_ROOT` going forward. A defensive read:

```python
# accept either name, prefer BROOMVA_ROOT
BROOMVA_ROOT = Path(
    os.environ.get("BROOMVA_ROOT")
    or os.environ.get("BROOMVA_WORKSPACE")
    or (Path.home() / "broomva")
)
```

**`bstack doctor` check** (future, optional): could audit that workspace scripts read the env var rather than hardcoding the path. Today this is honor-system — agents reading this file enforce it.

---

## Candidate ledger — patterns observed once, awaiting rule-of-three

Per `research/entities/pattern/bstack-engine.md` (the meta-primitive ledger in the workspace substrate), patterns observed only once stay in the ledger until ≥2 more instances accumulate. Listed here so future agents working on bstack don't re-derive them blind.

### CL-1 — LLM-as-index architecture (substrate → catalog → loader)

The composition shipped in BRO-1223 as the bstack knowledge graph: substrate stays canonical markdown, one projection (the dense catalog at `docs/knowledge-index.md`) routes the loading agent, agent reads bodies on demand, inferences fold back into the substrate as commits.

**Status**: 1 substrate instance (the kg knowledge graph). Promotion requires 2 more substrate instances (e.g., a code-search index, a doc-search index) that follow the same shape.

### CL-2 — Auto-compact catalog at high entity counts

In `bookkeeping cmd_index` (now in broomva/bookkeeping main), per-entity caps automatically tighten (claim 220→100 chars, top-5→top-3 edges, top-4→top-2 tags) when entity count exceeds 5000 — keeps catalog tokens under the 1M context ceiling. Schema bumps `dense-catalog-v2-compact`; frontmatter carries `compact: true/false`.

**Status**: 1 implementation. Promotion requires 2 more "growing-substrate, single-projection" composers that follow the same density-degrades-gracefully pattern.

### CL-3 — Haystack benchmark pattern for retrieval skills

Synthetic substrate generator (`gen-kg-fixture.py`) + multi-scale harness (`bench-kg-haystack.py`) with seeded needles split into Class-A (catalog-vocab) + Class-B (body-only-vocab) for ground-truth recall measurement.

**Status**: 1 instance (KG). Promotion belongs in `references/dogfood-patterns.md` as Pattern G (or H) once 2 more retrieval skills adopt the same Class-A/B seeding methodology.

### CL-4 — Defensive post-conversion bounds check

The `STALE_WARN_SECONDS = int(value * 3600)` floor-to-zero edge case (P20 R5): `stale_warn_hours: 0.0001` survived the pre-multiplication `<= 0` guard but floored to 0 after `int()` truncation, re-triggering the failure the guard was meant to prevent. Fix: guard **both** before and after conversion.

**Status**: 1 instance. Promotion belongs in a future `references/defensive-coding.md` (or similar) once 2 more bounds-check classes accumulate.

---

## How to add a convention here

1. **Observe** the pattern in ≥3 distinct callsites across the workspace, with a concrete mechanism + stated invariant + stated failure mode.
2. **Write the entry** following C1's shape: rule, why, canonical patterns (one per language), invariant, failure mode prevented, known callsites.
3. **Reference the BRO ticket** that produced the rule-of-three so future maintainers can audit.
4. **Move it out of the candidate ledger** if it was there.
5. **Bump bstack VERSION** + add CHANGELOG entry citing this file.

Don't promote on single instances. Don't crystallize aesthetic preferences ("clean code", "elegant design"). The bar is: *recurring failure mode that a stated mechanism prevents, validated by ≥3 production callsites.*

---

## See also

- `references/primitives.md` — primitive contracts (P1–P20)
- `references/dogfood-patterns.md` — P11 per-stack validation patterns
- `research/entities/pattern/bstack-engine.md` (in workspace substrate) — full candidate ledger across all crystallization candidates
