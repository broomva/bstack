# Orchestrator tick: the shared base protocol

A standing orchestrator is a model session woken on a schedule (a Paseo heartbeat, usually `session: bound`) to drive a fleet of sessions, PRs and loops. Every orchestrator runs this base. Its **overlay** adds only what is specific to it.

| Layer | Where | Holds |
|---|---|---|
| Base | `bstack/references/orchestrator-tick.md` (this file) | The protocol: tick contract, tiers, census, session handling, close-out, guards, backlog, report |
| Overlay | `<repo>/.broomva/loops/prompts/<id>.md` in the repo that owns the loop | Scope, sources, tier assignments, overlay-only steps |
| Snapshot | `<repo>/.broomva/loops/checks/<id>.sh` (the loop row's `checks:` key) | One read-only script that gathers the tick's facts in one run |
| State | `~/.broomva/notes/loops/<id>/state.yaml` and `backlog.yaml` | The carry-forward. It belongs to the loop, not the session |

The overlay wins on scope, sources and tiers. The base wins on everything else. An overlay that needs to change a base rule changes it here, by PR, so every orchestrator gets the change.

Client content stays in the client's repo. This file names no client, and neither does any overlay outside the client's repo.

**Every overlay declares:**
- `id`: the loop row id, and the heartbeat's name;
- `repo` and `REF`: the checkout and the remote-tracking ref its overlay and snapshot are read from (`origin/main`, `origin/dev`, …);
- `scope`: the cwd prefixes in scope, the sub-prefixes excluded, and other orchestrators' agent ids;
- `channel`: where the owner is reached;
- the tier assignments (§2).

**Reading it.** The heartbeat carries only a pointer to the overlay. The overlay points here. Both are read fresh from git on every fire, because a remote-tracking ref is only as fresh as the last fetch:

```bash
git -C <bstack checkout> fetch -q origin main && git -C <bstack checkout> show origin/main:references/orchestrator-tick.md
```

## 1. The tick contract

**Open the tick before the first action.** `ticks.jsonl` is shared by every orchestrator, so the subject `loop:<id>` is what makes a pair yours:

```bash
T=~/.broomva/cache/loop-tools; mkdir -p "$T"
git -C ~/broomva show origin/main:scripts/broomva_home.py > "$T/broomva_home.py"   # the writer lives in broomva/workspace
S=$(python3 "$T/broomva_home.py" append loops/ticks loop.tick.started loop:<id> \
      --actor agent:<your 8-hex agent id> --data '{"fire":"<scheduledFor>","fetch":"<ok|failed>"}' \
      | python3 -c 'import json,sys;print(json.load(sys.stdin)["id"])')
```

- `data.fire`: the `scheduledFor` of this heartbeat's run that is `running` for this agent (`~/.paseo/schedules/*.json`). If there is none, use the latest cron slot ≤ now in the schedule's zone.
- `data.fetch`: `ok`, or `failed`. If the fetch fails, carry on from the last fetched ref.

**Close the tick after the last action:**

```bash
python3 "$T/broomva_home.py" append loops/ticks loop.tick.ended loop:<id> --actor agent:<8-hex> --cause "$S" \
  --data '{"outcome":"ok|partial|failed","summary":"<=300 chars","tiers":["every",...],"calls":<n>}'
```

- `summary` is the reseed source.
- `tiers` lists only the tiers that completed this fire.
- `calls` is the measured tool-call count, including this closing append.

One fire produces one pair. An unclosed pair is drift (TICK_UNCLOSED).

**Write state atomically, with a guard.** Write the temp file, then check it, then `mv` it over `state.yaml`:

```bash
python3 - "$TMP" <<'PY' || { echo "state guard failed: state.yaml left as it was"; exit 1; }
import sys, yaml
d = yaml.safe_load(open(sys.argv[1]))
assert isinstance(d, dict) and d.get("tick"), "state must be a mapping with tick"
PY
mv "$TMP" "$STATE"
```

An empty YAML file parses as `None`. A guard that only checked parsing passed it, and the `mv` that followed wiped `state.yaml` (measured, 2026-10-08). Require a parsed mapping with `tick` set. Never edit `state.yaml` in place.

**If state is lost** (missing, or the guard fails on read), reseed it from the `data.summary` of the last `loop.tick.ended` whose subject is `loop:<id>`. Never use another orchestrator's summary. Say so in this tick's summary (STATE_LOST).

### state.yaml: the base keys

```yaml
updated: <UTC>
tick: <loop.tick.started id>
last_outcome: ok | partial | failed
tiers: {every: <UTC>, hourly: <UTC>, 4h: <UTC>, daily: <UTC>}   # the last run of each tier
open:            # in flight. The next fire checks each one, and drops it when it closes
  - {what: "<one line>", ref: "<PR/ticket/agent>", since: <date>, next: "<the check to run>"}
owner_waiting:   # decisions or steps only the owner can take
  - {what: "<one line>", ref: "<PR/ask>", since: <date>}
watches:
  - {ref: "<agent/PR>", why: "<one line>"}
```

An overlay may add keys, such as usage or disk. A line that no future fire will act on doesn't belong here. File findings as tickets, not state lines.

## 2. Cadence tiers

Each step has a tier. A tier is due when its `last_run` in `state.tiers` is older than its period. Its `last_run` is updated only when all its steps complete. A tier cut short by the budget stays due, and runs first on the next fire.

| Tier | Period | Default steps |
|---|---|---|
| `every` | each fire | snapshot, census, driven sessions, owner answers, PR queue, backlog advance, report |
| `hourly` | 1 h | main health, loops drift |
| `4h` | 4 h | owner-session close-outs, disk and usage trends |
| `daily` | 24 h | self-improvement, KG and memory promotion, loop upkeep (§10) |

The overlay can move a step to another tier, or add its own steps to a tier. It cannot drop the tick contract or the census.

**The fire period is dynamic.** The tick sets its own heartbeat's period from the snapshot's `capacity.cadence` (§7): 2 h with headroom, 4 h when the burn forecast runs out before relief, 6 h when it is critical, and read-only at 95% of the active account's 5 h window. When the recommended cron differs from the heartbeat's, the tick updates the heartbeat it is bound to, by name. It changes the cadence only, never the prompt, and records the change in state and in the tick's economy event. The loop row's `cadence` says `dynamic`, so a changed cron is not drift. Tiers are periods, not fire counts, so they hold at any fire period.

**Budget: fewer than 15 tool calls a tick**, counted and recorded in `data.calls`. The snapshot script is what makes this possible. It reads, in one call, everything the `every` tier needs, including each driven session's final message (§5). Tool calls are spent on acting, not on reading.

When a tick would exceed the budget, carry the remainder into `open` for the next fire. Don't stretch the tick.

## 3. Snapshot first

The first tool call after opening the tick runs the overlay's snapshot script, at the overlay's `<REF>`:

```bash
git -C <repo> show <REF>:.broomva/loops/checks/<id>.sh | bash
```

The contract for the script:
- **Read-only.** It never prompts, archives, merges or writes outside its cache.
- It includes a `capacity` section (§7) and a `sessions` section (§11), described there.
- It includes a `census` section (§4), with the final assistant message of each `arc` agent and of each `owner` agent idle for more than 12 h. That message comes from the agent's transcript: `persistence.sessionId` in its registry file, and `~/.claude/projects/*/<sessionId>.jsonl`.
- It prints one JSON object with one key per section.
- A section that fails is `{"error": "..."}`, and the rest still print.
- It exits 0 unless it cannot run at all.
- It finishes in under 2 minutes.

Every decision this tick starts from that object. Re-query a source only to act on it, or when its section is an error.

## 4. Census

`list_agents` under-reports. On 2026-10-08 it showed 6 of 34 live agents. Enumerate from Paseo's registry instead:
- **Source:** `~/.paseo/agents/*/*.json`.
- **Live** means `archivedAt` is null.
- Drop agents outside this orchestrator's scope, as the overlay defines it: cwd prefixes in scope, minus the excluded sub-prefixes.

Classify each live agent in scope, testing the classes in this order. The first match wins:

| Class | Test | Handling |
|---|---|---|
| `self` | this agent's id | none |
| `other` | not a model session (`provider` is not `claude`, e.g. a Maestro or plugin agent), an orchestrator id the overlay lists, a descendant of one, or a Maestro run in another scope | never touched |
| `arc` | a worker this orchestrator launched: the label `paseo.parent-agent-id` = self or a predecessor, the label `dispatched-by` names it, or a Maestro card it filed | driven (§5) |
| `owner` | everything else | read at a >12 h idle; closed out on the owner rule (§6) |

A cleared parent label is stored as `""`, not removed (measured). So "no parent" means missing or empty. Never treat `""` as a parent.

## 5. Session handling

**Finish notices get lost.** An idle session's finish notice sometimes never reaches the orchestrator. So every tick re-reads the final message of each driven session, from the snapshot's census section, whether or not a notice arrived. Call `get_agent_activity` only on the session you are about to act on.

**Never send a prompt to a running session**, because that interrupts its turn. Leave it a note it reads at its next turn boundary (`SendMessage` to a subagent; for a Paseo session, the overlay names the channel). Otherwise wait for it to go idle.

On an idle or finished session, act on the final message:

| Final state | Action |
|---|---|
| Died on a usage limit | Resume it once the window clears. Usage gates the resume (§7) |
| Context above 600k (`sessions.successor_due`) | Don't resume it. Launch a fresh successor seeded by a handoff note (§11) |
| Died on an auth denial, or pinned to a disabled login | Start a fresh agent in the same worktree, seeded with the dead one's final message, ticket and branch. Clear the dead agent's children's parent labels (§6), then archive it |
| Waiting on a background job (CI, a watcher) | Nudge it to wait in the **foreground**. A background wait ends the turn, and the result arrives nowhere |
| Ended on a question | Answer it if the answer is a decision you can make with the evidence. Otherwise it goes to the owner as an ask (§8) |
| Finished | Close it out (§6) |

## 6. Close-out and archiving

**The rule:** drive and communicate. Archive only when fully finished. "Fully finished" means all of these hold:
1. **Merged.** The work's PRs are merged, or explicitly closed by the owner.
2. **Tree clean.** `git status` is clean in its worktree, and no unpushed commit is left on its branch.
3. **Follow-ups ticketed.** Every open item in its final message is a ticket, not a sentence.
4. **Decisions recorded.** Any owner decision it raised is an entry in a `.control/asks` ledger.
5. **Card in review or done.** If a Maestro card tracks the arc, the card is in `review` or `done` before the session is archived. Archiving a session whose card is still `running` lands the card in a false failure.

**Owner sessions** are archived only when they have been idle more than 12 h and are fully finished. Do at most 3 owner close-outs per tick, and only while usage allows it (§7).

**Archiving cascades.** Archiving agent X also archives every live agent labelled `paseo.parent-agent-id=X`. A worker or successor that X created carries that label. Before archiving X, clear the label on each live child that must survive, and re-read the child's registry file to confirm the label is now `""`:

```
update_agent <child> labels {"paseo.parent-agent-id": ""}
```

Your own successor is such a child, so clear it on the successor before archiving yourself in a rollover.

**Which call to use:**
- `archive_agent` when the workspace holds other agents, or holds the orchestrator.
- `archive_workspace` only for a worktree workspace whose sole agent is the finished one.
- Never `archive_workspace` on the shared main checkout.

**Archiving the orchestrator kills its loop.** Paseo completes every schedule that targets an archived agent, and restoring the agent revives none of them. The only way to retire the orchestrator is the rollover (§11).

## 7. Resource guards

**Disk sets the number of slots.** Free space on the system volume decides how many new sessions may be launched:

| Free | New sessions |
|---|---|
| ≥ 60 GiB | up to the Maestro concurrency cap |
| 30–60 GiB | 1 per tick |
| 15–30 GiB | 0, and run the overlay's disk-reclaim step |
| < 15 GiB | 0, plus an owner alert |

**Usage gates launches.** Read the 5 h and 7 d utilization of every subscription account (`~/.claude/skills/provider-manager/scripts/provider_manager.py --json usage`, `fiveHourUtil` and `sevenDayUtil`):
- **New launches**, including queuing a backlog item into Maestro (§9): only while the active account's 5 h window is under 80% and its 7 d window is under 95%.
- **Resumes and owner close-outs:** only while every account's 5 h window is under 90%.
- **When the active account's 5 h window is at 95% or above:** no launch and no resume. Ticks keep running read-only.

**The burn forecast gates launches before the windows do.** Present utilization says nothing about the run rate, so the snapshot's `capacity` section forecasts it:
- **Burn rate**, per account, in 7 d percentage points per hour. It is measured over the last 24 h of usage samples, which the snapshot appends to its cache on every run, counting only samples inside the current 7 d window. With fewer than an hour of samples, it falls back to the window's average: 7 d utilization over the hours since the window opened.
- **Runway**: the fleet's 7 d headroom, summed over the accounts that are not limited, divided by the summed rate of those accounts.
- **Relief**: the earliest upcoming 7 d reset of any account.
- **`crosses_relief`**: the runway ends before relief. That means a stretch with no capacity at all.

It recommends a `cadence`, taking the most severe rung that applies:

| Mode | When | Fire period | Launches | Driven sessions |
|---|---|---|---|---|
| `normal` | headroom | 2 h | per the gates above | no cap |
| `conserve` | `crosses_relief`, or usage is stale or unreadable (fails closed) | 4 h | held | at most 3 |
| `critical` | it crosses with under 12 h of runway, or every usable account is at 90% of 7 d or more | 6 h | held | at most 1 |
| `read_only` | the active account's 5 h window is at 95% or more | 6 h | none | none, and no resume |

`hold_launches` holds new launches and Maestro queuing. `max_driven` caps how many sessions the tick drives, resumes included. The forecast's runway, relief and mode go in the tick report. The overlay names the forecast's implementation and its fixture suite.

Each tick appends one **economy event** to the `loops/economy` stream. It holds the capacity numbers, the session count, the top context consumers, and what the tick did: launches, resumes, merges, archives, successors and any cadence change. The overlay names the recorder and the evaluator that turns the stream into tokens per merged PR and burn trends.

## 8. Launching work, and owner answers

**Launch through Maestro cards**, not a bare `create_agent`. The card gives the run a gate, a review state and a home in the owner's Decisions. Every brief carries:
- the ticket id, with its full spec read first;
- the repo and branch, and its own worktree;
- the scope boundary: what it must not touch;
- what done means, as a quoted check;
- the merge rule: pinned to `--match-head-commit`, on green gates, never `--admin`;
- a **model tier**, recorded on the launch (in the card and the backlog item):
  - **Haiku 4.5** for mechanical drives: rerun, update-branch, merge on green, cleanup, archiving;
  - **Sonnet 5** for standard implementation and research;
  - **Opus 5.5** (or Fable 5.1) only for design, governance-class changes and P20 reviewers;
- a last line of exactly `ARC-STATUS: CLOSED` or `ARC-STATUS: HANDBACK <the one question>`.

**Owner asks** go into a `.control/asks/*.yaml` ledger.
- Maestro's Decisions reads the asks from `origin/main` **and** from the working tree of every checkout and worktree of the control repo. So an ask written in a worktree shows before its PR merges.
- Raise each decision once, with the pinned command, the status quo, and a recommendation.

**Act on owner answers in the same tick** they are read:
1. Re-read the verbatim answer, not a summary.
2. Do the step.
3. Land the answer, so it is not stranded on a local branch.

## 9. Backlog

Ideas and pending work live in `~/.broomva/notes/loops/<id>/backlog.yaml`. It is written atomically, as `state.yaml` is, with its own guard: a mapping with `tick` and an `items` list. An empty or unparseable backlog is never written over.

```yaml
tick: <loop.tick.started id>
items:
  - {id: <slug>, state: idea|ready|active|blocked|done, what: "<one line>", ref: "<ticket>",
     session: "<agent id, when active>", ask: "<ledger#id>", blocker: "<one line>", since: <date>}
```

Moves:

| From | To | When |
|---|---|---|
| idea | ready | it has a ticket with a spec |
| ready | active | a session is launched for it (`session:` set) |
| active | done | its session closed with `ARC-STATUS: CLOSED` |
| active | blocked | its session handed back (`ARC-STATUS: HANDBACK`), or is waiting on the owner. `ask:` is set to the ask, or `blocker:` is named |
| active | ready | its session is gone, with no close and no ask |
| blocked | ready | the ask is answered or the blocker cleared |

The rules:
- **Move at least one item forward each tick** when a move is available. The automatic moves above don't count toward this. When no move is available, say why in the summary.
- **Nothing is `active` without a session**, and nothing is `blocked` without an ask or a blocker.
- A handed-back item is never relaunched while its ask is open.
- Items in `ready` become queued Maestro work items, but only while §7 permits a launch. Maestro's own tick then starts them under its cap.
- Prune items that have been `done` for 7 days.

## 10. Daily tier: upkeep

Once a day:
- **Self-improvement:** pick the worst measured gap from the leverage sensor and turn it into one ticket or one fix.
- **Knowledge:** promote the day's findings to the knowledge graph or to memory, through bookkeeping. Don't do it by hand.
- **Loop upkeep:** compare the loop ledger (`loop_ledger.py list` and `stale`) with what is running. File drift as tickets. Renew `review_by` only on a row you have actually checked.

## 11. Rollover

Every turn re-reads the whole context from cache, so a large context costs on every turn, not once. The snapshot's `sessions` section reports each live in-scope session's `context_k` (the last main-thread assistant usage: input + cache read + cache creation, in thousands) and its `cache_read_24h`, the orchestrator's own included (`sessions.self`). Sessions above 400k are `handoff_candidate`; driven sessions above 600k are `successor_due`.

**Driven sessions above 600k get a successor, not another resume.** Launch a fresh session in the same worktree, seeded with a handoff note: the ticket, the branch, the PRs and their heads, the session's final message, and the one next step. Then close out the old session (§6).

A successor replaces a session and adds none, so `hold_launches` does not hold it, but it counts toward `max_driven`. When the cap is full, or the mode is `read_only`, the successor is deferred. Until it launches, the old session stays unresumed and unarchived, and it is named in `open`. The orchestrator's own rollover, below, is never held by the cadence mode: it lowers the cost of every later tick.

**The orchestrator rolls over** when its own context passes 400k, or when it is otherwise past use (repeated compaction, or near its limit):
1. Write `state.yaml`.
2. Close the tick with `outcome: partial` and `data.handoff: true`.
3. Ask for the rollover through the overlay's channel.

The successor is seeded with the overlay, this base and `state.yaml`, and nothing else. It creates its own heartbeat under the same name. Then the old heartbeat is deleted, and the parent label is cleared on the successor (§6) before the predecessor is archived.

## 12. Report

Report to the owner only when something changed or a decision is needed. End the report with these two sections:

```markdown
## Decided
- <what this tick decided and did, one line each, with the PR/ticket/agent>

## Ask
- <the one decision only the owner can make, with the pinned command; or "none">
```
