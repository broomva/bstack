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

**Reading it.** The heartbeat carries only a pointer to the overlay. The overlay points here. Both are read fresh from git on every fire, because `origin/main` is only as fresh as the last fetch:

```bash
git -C <bstack checkout> fetch -q origin main && git -C <bstack checkout> show origin/main:references/orchestrator-tick.md
```

## 1. The tick contract

**Open the tick before the first action.** Append `loop.tick.started` to `~/.broomva/ledger/loops/ticks.jsonl` through `broomva_home.py append loops/ticks`.
- `data.fire`: the `scheduledFor` of this heartbeat's run that is `running` for this agent (`~/.paseo/schedules/*.json`). If there is none, use the latest cron slot ≤ now in the schedule's zone.
- `data.fetch`: `ok`, or `failed`. If the fetch fails, carry on from the last fetched ref.
- Keep the returned id as `S`.

**Close the tick after the last action.** Append `loop.tick.ended` with `--cause "$S"` and `data`:
- `outcome`: `ok`, `partial` or `failed`;
- `summary`: at most 300 chars. This is the reseed source;
- `tiers`: the tiers that ran this fire;
- `calls`: the tool-call count, measured, not estimated.

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

**If state is lost** (missing, or the guard fails on read), reseed it from the last `loop.tick.ended`'s `data.summary` and say so in this tick's summary (STATE_LOST).

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

Each step has a tier. A tier runs when its `last_run` in `state.tiers` is older than its period. Then record the new `last_run`.

| Tier | Period | Default steps |
|---|---|---|
| `every` | each fire | snapshot, census, driven sessions, owner answers, PR queue, backlog advance, report |
| `hourly` | 1 h | main health, loops drift |
| `4h` | 4 h | owner-session close-outs, disk and usage trends |
| `daily` | 24 h | self-improvement, KG and memory promotion, loop upkeep (§9) |

The overlay can move a step to another tier, or add its own steps to a tier. It cannot drop the tick contract or the census.

**Budget: fewer than 15 tool calls a tick**, counted and recorded in `data.calls`. The snapshot script is what makes this possible. When a tick would exceed the budget, carry the remainder into `open` for the next fire. Don't stretch the tick.

## 3. Snapshot first

The first tool call after opening the tick runs the overlay's snapshot script, from the overlay repo's `origin/main`:

```bash
git -C <repo> show origin/main:.broomva/loops/checks/<id>.sh | bash
```

The contract for the script:
- **Read-only.** It never prompts, archives, merges or writes outside its cache.
- It prints one JSON object with one key per section.
- A section that fails is `{"error": "..."}`, and the rest still print.
- It exits 0 unless it cannot run at all.
- It finishes in under 2 minutes.

Every decision this tick starts from that object. Re-query a source only to act on it, or when its section is an error.

## 4. Census

`list_agents` under-reports. On 2026-10-08 it showed 6 of 34 live agents. Enumerate from Paseo's registry instead:
- **Source:** `~/.paseo/agents/*/*.json`.
- **Live** means `archivedAt` is null.
- Drop agents outside this orchestrator's scope. The overlay defines the scope, by cwd prefix.

Classify each live agent:

| Class | Test | Handling |
|---|---|---|
| `self` | this agent's id | none |
| `arc` | a worker this orchestrator launched: the label `paseo.parent-agent-id` = self or a predecessor, or a Maestro card it filed | driven (§5) |
| `owner` | the owner started it: no parent label, not a Maestro run | read at a >12 h idle; closed out on the owner rule (§6) |
| `other` | another orchestrator's, or a Maestro run in another scope | never touched |

## 5. Session handling

**Finish notices get lost.** An idle session's finish notice sometimes never reaches the orchestrator. So every tick re-reads the final message of each driven session: `get_agent_activity`, or the last assistant turn in the agent's transcript. Do this whether or not a notice arrived.

**Never send a prompt to a running session**, because that interrupts its turn. Use `SendMessage` with its session name instead.

On an idle or finished session, act on the final message:

| Final state | Action |
|---|---|
| Died on a usage limit | Resume it once the window clears. Usage gates the resume (§7) |
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

**Archiving cascades.** Archiving agent X also archives every live agent labelled `paseo.parent-agent-id=X`. A worker or successor that X created carries that label. Before archiving X, clear the label on each live child that must survive:

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
| 15–30 GiB | 0, and run the disk janitor |
| < 15 GiB | 0, plus an owner alert |

**Usage gates launches.** Read the 5 h and 7 d utilization of every subscription account (`provider_manager.py --json usage`):
- **New launches:** only while the active account's 5 h window is under 80%.
- **Resumes and owner close-outs:** only while both 5 h windows are under 90%.
- **At 95% or above:** no launch and no resume. Ticks keep running read-only.

## 8. Launching work, and owner answers

**Launch through Maestro cards**, not a bare `create_agent`. The card gives the run a gate, a review state and a home in the owner's Decisions. Every brief carries:
- the ticket id, with its full spec read first;
- the repo and branch, and its own worktree;
- the scope boundary: what it must not touch;
- what done means, as a quoted check;
- the merge rule: pinned to `--match-head-commit`, on green gates, never `--admin`;
- a last line of exactly `ARC-STATUS: CLOSED` or `ARC-STATUS: HANDBACK <the one question>`.

**Owner asks** go into a `.control/asks/*.yaml` ledger.
- Maestro's Decisions reads the asks from `origin/main` **and** from the working tree of every checkout and worktree of the control repo. So an ask written in a worktree shows before its PR merges.
- Raise each decision once, with the pinned command, the status quo, and a recommendation.

**Act on owner answers in the same tick** they are read:
1. Re-read the verbatim answer, not a summary.
2. Do the step.
3. Land the answer, so it is not stranded on a local branch.

## 9. Backlog

Ideas and pending work live in `~/.broomva/notes/loops/<id>/backlog.yaml`, written atomically under the same guard as `state.yaml`:

```yaml
items:
  - {id: <slug>, state: idea|ready|active|blocked|done, what: "<one line>", ref: "<ticket>",
     session: "<agent id, when active>", ask: "<ledger#id, when blocked on the owner>", since: <date>}
```

- **Move one item forward each tick.** Valid moves are idea → ready, ready → active, blocked → ready, and active → done.
- **Nothing is `active` without a session**, and nothing is `blocked` without an ask or a named blocker. An active item whose session is gone goes back to `ready`.
- Items in `ready` become queued Maestro work items, so that Maestro's own tick starts them under its cap.
- Prune items that have been `done` for 7 days.

## 10. Daily tier: upkeep

Once a day:
- **Self-improvement:** pick the worst measured gap from the leverage sensor and turn it into one ticket or one fix.
- **Knowledge:** promote the day's findings to the knowledge graph or to memory, through bookkeeping. Don't do it by hand.
- **Loop upkeep:** compare the loop ledger (`loop_ledger.py list` and `stale`) with what is running. File drift as tickets. Renew `review_by` only on a row you have actually checked.

## 11. Rollover

When the session's context is past use (repeated compaction, or near its limit):
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
