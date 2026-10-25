# Install surfaces — Cursor, Claude Code, skills CLI

bstack ships one repository and three wrappers. They install different surfaces. None of them replaces the others.

| Wrapper | Where it lives | What you get | How to install |
|---|---|---|---|
| **Cursor** | `.cursor-plugin/plugin.json` | Portable Agent Skills plus an opt-in hard-gate rule | Local plugin directory below. Not listed on the Cursor Marketplace. |
| **Claude Code** | `.claude-plugin/plugin.json` | The bstack CLI plus governance hooks | Install this repo as a Claude Code plugin. Hooks stay in `hooks/hooks.json` and locate themselves with `${CLAUDE_PLUGIN_ROOT}`. |
| **Skills CLI** | `broomva/skills` (not this repo) | The script-bearing companion skills | `npx skills add broomva/skills --skill <name>` |

Codex has no Cursor plugin runtime. Use the skills CLI, same as the other agents that CLI supports.

The CLI itself is still clone plus bootstrap. `npx skills add broomva/bstack` copies only the repo-root `SKILL.md` and drops `bin/` and `scripts/` ([vercel-labs/skills#1523](https://github.com/vercel-labs/skills/issues/1523)).

```bash
git clone https://github.com/broomva/bstack.git
cd bstack
./bin/bstack bootstrap
```

## Cursor (local)

Cursor loads an unpacked plugin from `~/.cursor/plugins/local/<name>/` when `.cursor-plugin/plugin.json` is at that directory's root. This repo is that root.

```bash
git clone https://github.com/broomva/bstack.git
mkdir -p ~/.cursor/plugins/local
rm -rf ~/.cursor/plugins/local/bstack
cp -a bstack ~/.cursor/plugins/local/bstack
```

Reload the window. In Agent chat, `/` should list `broomva-workspace`, `kg`, `bookkeeping`, `dogfood`, `cross-review`, `autonomous`, and `setup-bstack`.

A symlink in `~/.cursor/plugins/local` is skipped unless its target resolves to a directory inside that folder. Copy the checkout. Do not point a link at a clone that lives somewhere else.

The Cursor CLI has no marketplace install. Point it at the checkout:

```bash
cursor-agent --plugin-dir /absolute/path/to/bstack
```

`/add-plugin bstack` works only after the plugin is listed on cursor.com/marketplace. Publishing is not part of this repo yet.

Teams and Enterprise can import `https://github.com/broomva/bstack` from the dashboard (Settings, Plugins, Team Marketplaces, Import). There is no `.cursor-plugin/marketplace.json`. One plugin, manifest at the repository root.

## Why the skills are not in `skills/`

The skills CLI treats a top-level `skills/` directory as installable skills. Putting these ports there would make `npx skills add broomva/bstack` offer them beside the root warning skill, as if they were the companion roster.

Cursor skills live at `cursor-plugin/skills/` because the manifest says so:

```json
"skills": "./cursor-plugin/skills/",
"rules": "./cursor-plugin/rules/"
```

A manifest path replaces default folder discovery. The script-bearing companions (the ones with `scripts/`) stay in [broomva/skills](https://github.com/broomva/skills). These files are portable instruction ports of the same disciplines (workspace route, P6, P11, P20, autonomous mode), written so any Agent Skills host can read them. They are not a second copy of `bin/` or of those scripts.

## Why Cursor does not load `hooks/hooks.json`

When `.cursor-plugin/plugin.json` omits `hooks`, Cursor discovers `hooks/hooks.json`. That file is the Claude Code hook map: `SessionStart`, `Stop`, `PreToolUse`, and commands that expand `${CLAUDE_PLUGIN_ROOT}`. Loading it in Cursor would either fail the plugin or run Claude hooks in the wrong host.

The manifest sets `"hooks": "./cursor-plugin/hooks.json"`. That file is an empty Cursor hooks document (`{ "hooks": {} }`). Specifying the field replaces discovery, so `hooks/` is left to Claude Code. Do not retarget the Cursor manifest at `hooks/hooks.json`, and do not reformat that file for Cursor.

## Hard gates

`cursor-plugin/rules/bstack-hard-gates.mdc` ships with `alwaysApply: false`. Installing the plugin does not inject the rule into every chat.

Run `/setup-bstack` to write `~/.cursor/rules/bstack.mdc` with `alwaysApply: true`. The reminders match the Claude plugin's mechanical gates:

- no force-push to `main` or `master` (`--force`, `-f`, `--force-with-lease`)
- no `git reset --hard`
- no merge of human-only paths: `.control/policy.yaml`, settings, merge-gate and L3 workflows, control hooks, nested `CLAUDE.md` / `AGENTS.md`, RCS parameters

## Skills in the Cursor plugin

| Skill | When it applies |
|---|---|
| `broomva-workspace` | Start of substantive `broomva/workspace` work. Snapshot, current work, lenses, policy. |
| `kg` | Load knowledge-graph entities for a topic. |
| `bookkeeping` | File graph-worthy knowledge (P6) without asking permission. |
| `dogfood` | Exercise user-visible work and keep a receipt (P11). |
| `cross-review` | Independent review before a substantive or governance merge (P20). |
| `autonomous` | The owner said go. Run the full discipline. |
| `setup-bstack` | Write the user-level hard-gate rule. |

`broomva-workspace` keeps that name so it stays the workspace startup route, distinct from `setup-bstack`.
