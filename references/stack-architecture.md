# bstack Architecture

## Layer Dependency Diagram

```
                    ┌───────────────────────────────────────┐
                    │    Strategy & Decision Intel (Layer 7) │
                    │  pre-mortem · braindump                │
                    │  morning-briefing · drift-check        │
                    │  strategy-critique · stakeholder-update│
                    │  decision-log · weekly-review          │
                    └──────────────┬────────────────────────-┘
                                   │ informed by
                    ┌──────────────┴──────────────────-┐
                    │         Platform (Layer 6)       │
                    │  alkosto-wait-optimizer           │
                    │  content-creation                 │
                    └──────────────┬──────────────────-┘
                                   │ uses
                    ┌──────────────┴──────────────────-┐
                    │     Design & Implementation (5)   │
                    │  arcan-glass · next-forge          │
                    └──────────────┬───────────────────-┘
                                   │ styled by / built with
                    ┌──────────────┴───────────────────-┐
                    │     Research & Intelligence (4)    │
                    │  deep-dive-research-orchestrator   │
                    │  skills · skills-showcase          │
                    └──────────────┬───────────────────-┘
                                   │ informed by
                    ┌──────────────┴───────────────────-┐
                    │       Orchestration (Layer 3)      │
                    │  symphony · symphony-forge         │
                    │  autoany (EGRI loops)              │
                    └──────────────┬───────────────────-┘
                                   │ dispatches via
                    ┌──────────────┴───────────────────-┐
                    │    Memory & Consciousness (2)      │
                    │  agent-consciousness               │
                    │  knowledge-graph-memory             │
                    │  prompt-library                     │
                    └──────────────┬───────────────────-┘
                                   │ persists to
                    ┌──────────────┴───────────────────-┐
                    │     Foundation (Layer 1)           │
                    │  agentic-control-kernel            │
                    │  control-metalayer-loop            │
                    │  harness-engineering-playbook      │
                    └───────────────────────────────────-┘
```

## Data Flow

```
User request
  → Foundation validates (safety shields, gates, harness)
  → Memory provides context (consciousness, knowledge graph, prompts)
  → Orchestration dispatches (symphony daemon, EGRI improvement, hive mode)
  → Research gathers intelligence (deep dive, skills catalog)
  → Design renders output (Arcan Glass, Next.js templates)
  → Platform delivers value (decisions, content, finance)
  → Memory captures episode (conversation bridge → Obsidian)
  → Foundation logs trace (control metalayer → setpoint check)

Commit flow (regression gate):
  git commit
  → regression-gate-hook.sh (PreToolUse)
  → analyze staged files against regression-test-map.json
  → identify affected features (auth, chat, models, payments, etc.)
  → agent-browser E2E tests for each affected feature
  → tests pass → stamp bypass → commit proceeds
  → tests fail → block commit → report failures
```

## Integration Points

| From | To | How |
|------|----|-----|
| Foundation → Memory | Control policy informs what to remember | `.control/policy.yaml` → consciousness substrate 1 |
| Memory → Orchestration | Knowledge graph feeds agent context | Obsidian wikilinks → symphony workspace |
| Orchestration → Research | Symphony dispatches research agents | `symphony dispatch` → deep-dive orchestrator |
| Research → Design | Research findings inform UI decisions | Analysis docs → arcan-glass components |
| Design → Platform | Styled outputs serve end users | Next.js pages → Vercel deployment |
| Platform → Foundation | Usage metrics feed control loop | Observability → setpoint adjustment |
| Foundation → Platform | Regression gate triggers E2E tests | regression-test-map.json → agent-browser |
| Strategy → Memory | Decisions and reviews persist to vault | decision-log → knowledge-graph-memory |
| Strategy → Research | Critiques leverage deep research | strategy-critique → deep-dive-research |
| Strategy → Foundation | Drift checks feed governance loop | drift-check → control-metalayer setpoints |

## Hooks Architecture

```
Claude Code Session
  │
  ├─ SessionStart ──→ spaces-context-hook.sh ──→ Read peer activity from Spaces
  │
  ├─ PreToolUse (Bash) ──┬→ control-gate-hook.sh ──→ Safety shields (G1-G6)
  │                      └→ regression-gate-hook.sh ──→ E2E regression gate (G11)
  │
  ├─ PreToolUse (Write/Edit) ──→ control-gate-hook.sh ──→ Secrets file protection (G4)
  │
  ├─ Stop ──→ conversation-bridge-hook.sh ──→ Session → knowledge graph
  │
  └─ Notification ──→ conversation-bridge-hook.sh ──→ Backup bridge trigger

Git Pre-Commit Hook
  ├─ Governance file check (CLAUDE.md, AGENTS.md, METALAYER.md, policy.yaml)
  ├─ Secrets staging check (.env, credentials.json)
  ├─ Conversation index auto-update
  └─ Affected feature reporting (regression-test-map.json)
```
