# bstack Quickstart

Get the full Broomva Stack running in 5 minutes.

## 1. Install bstack

```bash
npx skills add broomva/bstack
```

## 2. Bootstrap all 27 skills

```bash
bash ~/.agents/skills/bstack/scripts/bootstrap.sh
```

This installs any missing skills, creates symlinks, and runs the postinstall harness.

## 3. Check status

Ask your agent: "bstack status"

Or run the validation directly:

```bash
bash ~/.agents/skills/bstack/scripts/validate.sh
```

This checks all 27 skills, PII redaction, and the regression testing gate.

## 4. Initialize a project (optional)

For a new project with Broomva conventions:

```bash
# Scaffold with symphony-forge (includes control metalayer)
npx symphony-forge init my-project

# Or manually add the control metalayer
# Ask your agent: "bootstrap control metalayer for this repo"
```

## 5. Configure regression testing

The regression gate automatically intercepts `git commit` and runs context-aware E2E tests
via `agent-browser` for affected features. To set up:

1. Populate `scripts/regression-test-map.json` with your feature → file-pattern → scenario mappings
2. The postinstall script wires `regression-gate-hook.sh` into `.claude/settings.json`
3. Gate G11 in `.control/policy.yaml` enforces the requirement

```bash
# Preview which features your staged changes affect
make regression-map

# Bypass for 10 minutes after tests pass
make regression-stamp

# Re-enable the gate
make regression-clear
```

## 6. Browse the roster

- Web: https://broomva.tech/skills
- CLI: Ask your agent "list bstack skills"
- Reference: `references/skills-roster.md`

## What each layer gives you

| Layer | What you get | First command to try |
|-------|-------------|---------------------|
| Foundation | Safety gates, harness commands, regression testing, AGENTS.md | "bootstrap control metalayer" |
| Memory | Cross-session context, prompt library (shared knowledge surface + eval engine) | "use the code-review-agent prompt on this diff" |
| Orchestration | Agent dispatch, self-improvement, hive mode | "symphony init" |
| Research | Deep analysis, competitive intel | "deep research on X" |
| Design | Glass UI, production templates | "create an arcan-glass component" |
| Platform | Decision tools, content pipeline, finance, SEO/LLMEO | "optimize this decision" |
| Strategy | Risk analysis, daily briefs, decision logs | "pre-mortem this project" |
