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
  → Orchestration dispatches (symphony daemon, EGRI improvement)
  → Research gathers intelligence (deep dive, skills catalog)
  → Design renders output (Arcan Glass, Next.js templates)
  → Platform delivers value (decisions, content)
  → Memory captures episode (conversation bridge → Obsidian)
  → Foundation logs trace (control metalayer → setpoint check)
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
| Strategy → Memory | Decisions and reviews persist to vault | decision-log → knowledge-graph-memory |
| Strategy → Research | Critiques leverage deep research | strategy-critique → deep-dive-research |
| Strategy → Foundation | Drift checks feed governance loop | drift-check → control-metalayer setpoints |
