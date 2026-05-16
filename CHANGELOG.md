# Changelog

## 0.2.1 — 2026-05-16

### Drop legacy fallback shims from the 0.2.0 renumber

The legacy env vars (`BROOMVA_P8_HOME`, `BROOMVA_P9_JANITOR_HOME`,
`BROOMVA_P8_THRESHOLD_DAYS`) and config-dir fallbacks (`~/.config/broomva/p8/`,
`~/.config/broomva/p9-janitor/`) that 0.2.0 added "for in-place upgrades" are
removed. The canonical paths (`p7/`, `p8-janitor/`) and env vars
(`BROOMVA_P7_HOME`, `BROOMVA_P8_JANITOR_HOME`, `BROOMVA_P7_THRESHOLD_DAYS`) are
the only paths/vars the scripts honor. The legacy-callout prose is stripped
from every governance and substrate document.

**Migration for users upgrading from < 0.2.1**: one-time `mv` of any existing
state into the canonical location:

```bash
[ -d ~/.config/broomva/p8 ] && mv ~/.config/broomva/p8 ~/.config/broomva/p7
[ -d ~/.config/broomva/p9-janitor ] && mv ~/.config/broomva/p9-janitor ~/.config/broomva/p8-janitor
```

After migration: the freshness stamp + janitor `protected.txt` are read from
the canonical paths. No shims in the codebase.

## 0.2.0 — 2026-05-16

### Primitive renumber: Wait moves back to P9 (skill-name↔primitive-number alignment)

The productive-wait primitive (`broomva/p9` skill) is now **P9 (Wait)** again,
matching the skill repo name. Freshness moves to **P7**, Janitor moves to **P8**.

| Before | After |
|---|---|
| P7 = Wait (`broomva/p9` skill — historical name) | P7 = Skill Freshness |
| P8 = Skill Freshness | P8 = Branch + Worktree Janitor |
| P9 = Branch + Worktree Janitor | P9 = Wait (`broomva/p9` skill — name matches number) |

**Rationale**: the interim P8-Freshness / P9-Janitor numbering broke the Name (Pn)
recall key for Wait — the `broomva/p9` skill name no longer matched any primitive
number, producing the exact "numeric soup" failure mode the naming rule (added
in 0.1.x) was designed to prevent. Memory feedback file `feedback_p9_reflexive.md`
became ambiguous (was it about the *skill* or the *primitive number*?). The
2026-05-16 renumber restores alignment so Wait = P9 = `broomva/p9` — primitive
number, primitive name, and skill repo name all agree.

**Migration**: 0.2.0 shipped with legacy fallbacks for in-place upgrades from
the interim numbering. **0.2.1 removes those fallbacks** — see the 0.2.1 entry
above for the one-time `mv` users on the interim numbering should run.

### Naming convention rule propagated to bstack-loaded surfaces

The "use `Name (Pn)` form in agent prose, never bare `Pn`" rule (already in
workspace CLAUDE.md + AGENTS.md) is now restated in `SKILL.md` (naming-convention
subsection after the primitives table) and `references/primitives.md` (top of
file, before TOC). When `/bstack` fires and agents load these surfaces, the rule
is visible at the entry point — closing the gap where agents reverted to bare
`Pn` because the rule was buried only in workspace governance files.

### Doctor extensions

- **New Section 9 — Naming convention propagation**: `scripts/doctor.sh` now lints
  that CLAUDE.md + AGENTS.md contain (a) the `Name (Pn)` naming rule in prose,
  and (b) a **Short-name index** line with exactly 20 entries. Closes the
  silent-drift failure mode where governance edits could leave the index
  mismatched with the primitive count.
- **Primitive-number checks updated**: Section 3 (AGENTS.md primitive sections),
  Section 4 (reflexive trigger rules), Section 6 (hook wiring labels), and
  Section 7 (script paths + labels) all reflect the new canonical ordering.

## 0.1.0

- Initial versioned release with auto-update mechanism
- Added `bin/bstack-update-check` — periodic version check with caching, snooze, and auto-upgrade support
- Added `bin/bstack-config` — read/write `~/.bstack/config.yaml` for persistent preferences
- Added `bstack-upgrade/SKILL.md` — inline upgrade flow with 4 user options
- Preamble now checks for updates before running skill detection
- 27 skills across 7 layers: Foundation, Memory, Orchestration, Research, Design, Platform, Strategy
