# bstack Quickstart

Get the full Broomva Stack running in 5 minutes.

## 1. Install bstack

```bash
npx skills add broomva/bstack
```

## 2. Bootstrap all 16 skills

```bash
bash ~/.agents/skills/bstack/scripts/bootstrap.sh
```

This installs any missing skills and creates the necessary symlinks.

## 3. Check status

Ask your agent: "bstack status"

Or run the preamble directly:

```bash
bash ~/.agents/skills/bstack/scripts/validate.sh
```

## 4. Initialize a project (optional)

For a new project with Broomva conventions:

```bash
# Scaffold with symphony-forge (includes control metalayer)
npx symphony-forge init my-project

# Or manually add the control metalayer
# Ask your agent: "bootstrap control metalayer for this repo"
```

## 5. Browse the roster

- Web: https://broomva.tech/skills
- CLI: Ask your agent "list bstack skills"
- Reference: `references/skills-roster.md`

## What each layer gives you

| Layer | What you get | First command to try |
|-------|-------------|---------------------|
| Foundation | Safety gates, harness commands, AGENTS.md | "bootstrap control metalayer" |
| Memory | Cross-session context, prompt library | "save this as a prompt" |
| Orchestration | Agent dispatch, self-improvement | "symphony init" |
| Research | Deep analysis, competitive intel | "deep research on X" |
| Design | Glass UI, production templates | "create an arcan-glass component" |
| Platform | Decision tools, content pipeline | "optimize this decision" |
