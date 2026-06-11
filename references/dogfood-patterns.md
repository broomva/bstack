# bstack — Dogfood Patterns Cookbook

**Status**: P11 (Empirical Feedback Loop) operationalization reference. Loaded on demand when the agent needs the concrete *how* of "validate by interacting" for a given tech stack. Lives at `bstack/references/dogfood-patterns.md`; SKILL.md surfaces it in the on-demand index.

**Audience**: agent-readable (LLM-loaded reference) — Markdown per P18 Format-Follows-Audience rule.

**Companion docs**: `references/primitives.md` §P11 (the discipline) · `~/.claude/skills/Interceptor/SKILL.md` (the primary surface) · `~/broomva/docs/reports/2026-05-22-houston-dogfood-pattern.html` (the worked example for Tauri).

---

## What this cookbook closes

P11 is **what to do** (interact with the deployed version, capture evidence). This cookbook is **how to do it** for whatever tech stack the workspace is currently instantiated from. Without this, the P11 reflex becomes ritual: agents acknowledge "validate by interacting" and then ship without ever clicking the app, because they don't know which surface to drive.

The cookbook is keyed on the **detected stack** — not the user's words. The `bstack doctor` §13 check auto-detects the stack from repo signals (Cargo.toml + src-tauri/ → Tauri; next.config.* → Next.js; app.json + Expo SDK → React Native; Cargo.toml solo → Rust CLI; openapi.* / FastAPI / Hono routes → REST API; SKILL.md with MCP frontmatter → MCP server; markdown-dominant with no code manifest → **Knowledge vault / non-code, Pattern H**) and maps to the matching pattern below.

---

## The Dogfood Plan contract (binding)

Before substantive work begins on any feature, the agent produces a **Dogfood Plan** keyed to the detected stack. The plan lives in the response (and the PR body / Linear ticket) — not the agent's head. P11's 6th reflex (the *dogfood receipt*) verifies the plan was executed before claiming complete.

Minimum Dogfood Plan shape:

```markdown
**Dogfood Plan** (stack: <detected>)

- **Entry surface**: what URL / window / CLI command exposes the change to a user
- **Driver**: which skill / tool drives the interaction (Interceptor, cliclick, curl, etc.)
- **Evidence**: what artifact proves it worked (screenshot path, response body, log line, recording)
- **Smoke**: the one-line "did it not break in the obvious way" check
- **End-to-end**: the multi-step user flow that would catch a regression a smoke test misses
- **Receipt anchor**: the file / line / message-id where the evidence lives
```

If the agent cannot fill a row, it states why ("backend requires real cloud creds; smoke-only this run") — but the plan is still produced. Silent omission is the failure mode this contract closes.

---

## Pattern A — Tauri + sidecar (e.g. Houston, mission-control, control)

**Signals**: `Cargo.toml` + `src-tauri/` + a separately-built engine binary; macOS WKWebView host; vite dev server on `:1420`.

**Why hybrid**: WKWebView is opaque to System Events (AppleScript accessibility returns `missing value` for React buttons). The app the user sees is a Tauri window — but the React DOM inside it is reachable from a regular Chrome session pointing at `:1420` once engine creds are injected.

| Surface | Use for | How |
|---|---|---|
| **Engine API** | State assertions (workspaces, sessions, providers, settings) | `curl -H "Authorization: Bearer $TOKEN" http://127.0.0.1:$PORT/v1/*`. Token from env at launch; port from `~/.dev-houston/engine.json` (note: dev mode uses isolated home). |
| **cliclick** | Onboarding screens, button clicks, typing into chat input | `osascript -e 'tell application "Houston" to activate'` then `cliclick c:X,Y t:"text" kp:return`. Find coords by `screencapture -R region` and mapping cropped-pixel → logical-screen y. |
| **screencapture** | Before/after visual evidence | `screencapture -x -t png -R x,y,w,h /tmp/shots/<label>.png`. Agent `Read`s the PNG back into context to verify. |
| **Interceptor** | Specific React DOM state, network log, replay-script recording | Open `http://localhost:1420` in a real Chrome session, inject `window.__HOUSTON_ENGINE__ = { baseUrl, token }` before reload, then `interceptor read / act / inspect / monitor`. |
| **vite HMR** | Frontend code change → re-render in ~200ms | Edit any `app/src/**`; vite pushes `hmr update`. Engine state survives. `main.tsx` can't fast-refresh → full reload expected. |
| **Tauri supervisor** | Engine code change → restart | Watches `engine/**/*.rs`. Kills/rebuilds/restarts sidecar at a NEW port. Re-read `engine.json`. Same token persists if `HOUSTON_ENGINE_TOKEN` was set at launch. |

**Canonical arc** (lifted from `~/broomva/docs/reports/2026-05-22-houston-dogfood-pattern.html`):

1. `cargo build -p <engine-name>-server` (cold ~2-4 min; warm ~30s)
2. `<TOKEN_VAR>=<known> pnpm tauri dev` from `app/` — Tauri spawns vite on :1420 + builds Tauri host + spawns engine sidecar
3. `PORT=$(jq -r '.port' ~/.dev-<app>/engine.json)`; `curl -H "Authorization: Bearer $TOKEN" $BASE/v1/health`
4. Drive engine state via curl (workspaces, sessions, providers); persists in dev SQLite at `~/.dev-<app>/db/`
5. Drive UI flow via `cliclick`; capture region screenshots when coords are ambiguous
6. Iterate frontend: edit `app/src/`, vite HMRs in ~200ms; iterate engine: edit `engine/**/*.rs`, supervisor restarts at new port; re-read engine.json
7. Tear down: `kill $(cat /tmp/<app>-tauri-dev.pid)` cascades shutdown

**Gotchas**: dev home is `~/.dev-<app>/` not `~/.<app>/`; `cargo check --workspace` fails on CI without the engine sidecar prebuilt at `binaries/<name>-<triple>`; tab cycle lands on the wrong button (mic vs chat input) — verify with a zoomed region capture; engine port changes on every supervisor respawn but the token persists across.

---

## Pattern B — Next.js (App Router or Pages)

**Signals**: `next.config.{js,mjs,ts}`, `app/` or `pages/`, `next` in package.json.

| Surface | Use for | How |
|---|---|---|
| **Dev server logs** | Compile errors, server-component logs, API route logs | `bun dev` or `pnpm dev` in `run_in_background`; tail the output file |
| **Interceptor** | UI interaction in real Chrome (logged-in sessions, auth, real cookies) | `interceptor open http://localhost:3000/<route> ; interceptor read ; interceptor act --click "<role+name>"` |
| **gstack** | Fast headless E2E (no auth state needed) | One-shot scripts via Playwright/Puppeteer wrapper |
| **before-and-after** | Pre/post screenshot of a visible change | `before-and-after` skill — captures URL, edits, re-captures, diffs |
| **curl + jq** | API routes and server actions (POST / GET) | `curl -sS -X POST http://localhost:3000/api/<route> -H 'content-type: application/json' -d '{...}' \| jq` |
| **Vercel preview** | Deploy verification post-PR | `gh pr view <n> --json comments \| jq -r '...preview URL...'` then Interceptor on the preview URL |

**Canonical arc**:

1. `bun dev` or `pnpm dev` in `run_in_background` (capture stdout file for log-tail)
2. Smoke: `curl -sSI http://localhost:3000 \| head -1` → expect `200`
3. End-to-end via Interceptor: open the affected route, drive the user flow that the change targets, capture screenshot at each meaningful state
4. API changes: `curl` the endpoint with realistic payloads; `jq` the response; assert shape
5. On push: `p9 watch <pr> --background` for CI; on green, Vercel preview URL → Interceptor on the preview, capture deploy-verification screenshot before claiming shipped

**Gotchas**: server components don't surface client errors to terminal — open dev tools / Interceptor console; middleware redirects can hide real failures behind 200s; `next dev` HMR can mask stale type errors that `next build` catches.

---

## Pattern C — Expo / React Native (finkids, vlgym)

**Signals**: `app.json` with `expo` block, `package.json` with `expo`, presence of `metro.config.*`.

| Surface | Use for | How |
|---|---|---|
| **Expo Go on simulator** | Visual + interaction validation | `expo start --ios` (or `--android`); simulator boots; capture via `xcrun simctl io booted screenshot /tmp/<label>.png` |
| **Metro bundler logs** | JS errors, fast refresh status | `expo start` in `run_in_background`; tail output |
| **EAS preview build** | Real-device validation (when simulator is insufficient) | `eas build --profile preview` → install on TestFlight / Internal Distribution |
| **Detox** (when wired) | Automated E2E on simulator | `detox test` |
| **xcrun simctl** | Drive iOS simulator from CLI | `simctl io booted recordVideo` for flow capture; `simctl push` for notifications |

**Canonical arc**:

1. `expo start --ios` in `run_in_background`
2. Wait for simulator boot; capture initial state with `xcrun simctl io booted screenshot /tmp/<label>-baseline.png`
3. Drive the user flow (taps, scrolls) — for non-Detox repos, narrate the manual flow in the PR body and capture per-state screenshots via `xcrun simctl io booted screenshot`
4. For state assertions: query Reactotron, async-storage CLI, or the app's debug menu
5. Tear down: `xcrun simctl shutdown booted`; metro stops via the background-process kill

**Gotchas**: simulator can be in a stale state between runs — `xcrun simctl erase booted` is the nuclear reset; Expo SDK upgrades can change which surface works; iOS-Android divergence means dogfood plan should pick the *primary* target platform per ticket.

---

## Pattern D — Rust CLI (microgpt, p9, bstack, persist)

**Signals**: `Cargo.toml` without a UI framework, `src/main.rs` or `src/bin/*.rs`, no `tauri.conf.*` or web assets.

| Surface | Use for | How |
|---|---|---|
| **Direct invocation** | The CLI IS the user surface | `cargo run -- <args>` for dev; `./target/release/<bin> <args>` for the binary the user sees |
| **trycmd / assert_cmd** | Repeatable CLI scenarios | Integration tests under `tests/` — these ARE the dogfood receipts when wired |
| **Snapshot via expect-test / insta** | Output format regressions | `insta` review for any output change |
| **Real-arg shell sessions** | Multi-step flows | Capture via `script -q /tmp/<label>.log <cmd>` or `asciinema rec` |
| **`--help` smoke** | Public-API surface check | `cargo run -- --help` → assert exit 0 + key flags listed |

**Canonical arc**:

1. `cargo build` (release mode if perf matters)
2. Smoke: `./target/release/<bin> --version && ./target/release/<bin> --help` → exit 0 both times
3. The user-flow case(s) that motivated the change: invoke the actual flag combination a user would type; capture stdout/stderr; verify exit code and output shape
4. For interactive CLIs (TUI / prompts): `script -q` or `asciinema rec` captures the session as a replayable artifact
5. If the CLI calls external services: `--dry-run` first (no side effects), then real call with redacted output captured in the receipt

**Gotchas**: cargo features matter — dogfood the feature flags the user will ship with, not just the default; rust-toolchain.toml pins matter for deploy-verification; CLI args that look like flags but mean filenames need explicit `--` separators when testing.

---

## Pattern E — REST API / FastAPI / Hono / Axum

**Signals**: route definitions in `routes/`, `api/`, or `src/handlers/`; `openapi.{yaml,json}` present; framework dep (fastapi, hono, axum, express).

| Surface | Use for | How |
|---|---|---|
| **curl + jq** | Endpoint behavior | `curl -sS -X <METHOD> $BASE/<route> -H 'authorization: Bearer ...' -d '{...}' \| jq` |
| **httpie** (if available) | Friendlier ad-hoc requests | `http POST $BASE/<route> field=value` |
| **OpenAPI schema diff** | Public-contract regression | Compare `openapi.{yaml,json}` before/after; alert on breaking changes |
| **hurl / bruno / postman runner** | Repeatable request collections | `hurl tests/*.hurl` returns pass/fail per scenario |
| **Server logs** | What the request actually did | `run_in_background` tailing the dev server stdout |
| **Database snapshot** | Side-effect verification | `sqlite3 dev.db 'select * from ...'` or psql equivalent before/after the request |

**Canonical arc**:

1. Start the dev server in `run_in_background` (uvicorn, hono, axum); capture stdout
2. Smoke: `curl -sSI $BASE/health` (or root) → 200
3. The endpoint being changed: hit it with realistic auth + payload; assert response shape with `jq` filters; verify side effects in the DB snapshot
4. OpenAPI: regenerate if needed; diff for breaking changes; if changed, surface in PR body
5. Concurrency: hit the endpoint with `xargs -P` parallelism if the change touches shared state or transactions

**Gotchas**: 200 with wrong body looks like success; auth middleware can mask real failures; OpenAPI drift = client breakage; database side effects don't show in the response — query the DB to confirm.

---

## Pattern F — MCP server / Tool provider

**Signals**: SKILL.md frontmatter with `tools:`, `mcp.json` / `mcp.yaml` config, `@modelcontextprotocol/sdk` dep.

| Surface | Use for | How |
|---|---|---|
| **mcp inspector / explorer** | Tool discovery + manual invocation | `npx @modelcontextprotocol/inspector <server-cmd>` |
| **Direct LLM invocation** | The agent IS the user — try it in a session | Have Claude / GPT invoke the tool with a realistic prompt; check the response |
| **Conversation log** | What the model actually did with your tool | P1 (Conversation Bridge) preserves this; grep the log for the tool's name |
| **mcp server logs** | What the tool saw | Background tail of the server's stdout/stderr |
| **Tool-call shape diff** | Regression | Capture before/after tool-call JSON; assert structural compatibility |

**Canonical arc**:

1. Launch the MCP server in `run_in_background` (or via inspector)
2. From a fresh agent context (or inspector UI), invoke each tool the change affects with realistic inputs
3. Capture the tool response, the server log lines that fired, and any side-effect state (e.g. files written)
4. If the change is to the tool *schema*: confirm the change is backwards-compatible OR explicitly versioned
5. If the change is to the tool *behavior*: at minimum, capture one successful invocation from a real LLM session as the receipt

**Gotchas**: MCP server contracts are stricter than REST — a missing required field is a hard fail; descriptions matter for tool selection by the model; transport layer (stdio vs SSE vs websocket) can hide failures differently.

---

## Pattern G — Trading bot (webhook receiver + multi-broker + Pine scripts)

**Signals**: a `services/tradingview-bridge/` (or similarly-named) subdir containing FastAPI/Hono routes + a `strategies/pine/*.pine` directory + a SQLite idempotency DB (`*.sqlite` referenced by code) + broker-client modules. Often combined with Pattern E signals (it IS a REST API at the receiver layer); the trading-bot variant adds the Pine-Script side and the broker-execution side.

| Surface | Use for | How |
|---|---|---|
| **curl + jq** | Webhook receiver behavior | `curl -sS -X POST $BASE/webhook -d '<TVAlert JSON>' \| jq` — verify accepted/duplicate/rejected status + order_id |
| **Pine alert simulator** | Fire a synthetic alert without TradingView | Construct the alert JSON from `services/tradingview-bridge/src/tradingview_bridge/schemas.py:TVAlert` shape; POST directly; assert the dispatcher routed to the expected broker |
| **SQLite idempotency inspector** | Verify alert_id dedup is working | `sqlite3 $TVBRIDGE_DB_PATH 'SELECT * FROM alert_idempotency ORDER BY created_at DESC LIMIT 10'` |
| **Broker mock state** | Verify the right broker received the order | In tests: `MockClient.placed_orders` list; in dev: structured log greps for `mock_order_placed broker=<...>` |
| **Interceptor** | Capture TradingView chart at alert moment | `services/tradingview-bridge/scripts/capture_chart.sh <chart_url> <out.png>` (drives Interceptor extension) |
| **Bookkeeping journal grep** | Cross-reference alerts that landed in the knowledge graph | `grep "alert_dispatched" research/entities/pattern/strategy-*.md` (workspace bookkeeping P6) |
| **Server logs** | Full pipeline trace | `run_in_background` tailing uvicorn stdout; filter for `alert_dispatched`, `dispatch_duplicate`, `broker_not_configured` events |
| **Structured-log curl loop** | Stress test (rate limiter + idempotency) | `for i in {1..120}; do curl ...; done; jq` against the log — verify ~60 accepted + ~60 429s with one source IP |

**Canonical arc** (paper-only; real-paper variant requires broker onboarding):

1. Start uvicorn in `run_in_background`; capture stdout to a log file
2. Smoke: `curl -sSI $BASE/health` → 200; body shows `mode=paper`, `broker_mode=mock`, `brokers={ibkr:true,kraken:true,polymarket:true}`
3. Asset-class routing: for each `asset_class` in `[stock, etf, bond, fx, crypto, prediction]`, POST a synthetic TVAlert with a unique `alert_id`; assert the response's `broker` field matches the expected route (stock→ibkr, crypto→kraken, prediction→polymarket)
4. Idempotency: re-fire one of the alerts with the same `alert_id`; assert `status="duplicate"` + same `order_id` as the original
5. Auth gates: bad secret → 401; bad source IP → 403; bad action → 422; bad JSON → 400; all four in one rapid sequence to verify error ordering
6. Rate limit: with `TVBRIDGE_RATE_LIMIT_PER_MINUTE=2`, fire 3 alerts rapidly with distinct `alert_id` (to bypass idempotency); assert the third returns 429
7. Bookkeeping evidence: confirm `research/entities/pattern/strategy-<name>.md` got a journal line (or, in test env, that `TVBRIDGE_BOOKKEEPING_CLI=/nonexistent` and the no-op log fires)
8. Chart receipt (operator-driven): for one alert, invoke `capture_chart.sh` to attach a PNG of the TradingView chart at the alert moment

**Gotchas**:
- **Paper-only is enforced at startup**, not runtime — a `TVBRIDGE_TRADING_MODE=live` env var crashes the process before serving any traffic. P11-relevant: the receipt must include "process started in paper mode" as binary evidence, not just "tests pass."
- **Mock-default broker mode** means absence of `TVBRIDGE_BROKER_MODE=real-paper` → no broker contact. Operators flipping to real-paper without TWS / sandbox creds get `NotConfiguredError` → `rejected` DispatchResult (not a crash).
- **Idempotency can mask bugs** — if the dispatcher is broken AND a duplicate fires, the second call returns the (broken) first call's order_id. Always test idempotency with a *new* alert_id, not by re-firing.
- **Rate limit per-IP** — distributed tests using one source IP can self-trip the limiter; either bump the env var temporarily OR rotate the X-Forwarded-For header.
- **Pine Script syntax errors** only surface in TradingView's Pine Editor — there's no local linter. Operator-driven syntax check is part of the dogfood plan for any Pine change.
- **CFDs forbidden** by policy.yaml `cfd-broker-blocked` (deferred to PR 4 — receipt should confirm no executor path points at a CFD endpoint when policy.yaml is read).

**Receipt template**:

```markdown
**Dogfood Plan** (stack: trading-bot)

- **Entry surface**: webhook receiver at $BASE/webhook + Pine alerts from TradingView (or synthetic via curl)
- **Driver**: curl + jq + sqlite3 + Interceptor (chart capture) + uvicorn server logs
- **Evidence**: log file path + idempotency DB snapshot + (operator-driven) chart PNG
- **Smoke**: GET /health → 200 with mode=paper, broker_mode=mock, brokers all true
- **End-to-end**: 7+ POST cases — 6 asset-class routes + 1 idempotency hit + 4 error paths + 1 rate-limit trip
- **Receipt anchor**: PR body / commit message / strategy-{name}.md bookkeeping entry
```

**Where this sits in the workspace** (reference instance):
- Service: `github.com/broomva/investment-management services/tradingview-bridge/` (PR 1 ships the receiver, PR 2 the executor, PR 3 the Pine library)
- ADR: `github.com/broomva/workspace docs/specs/2026-05-22-broker-selection-cross-asset.html`
- Linear ticket: `github.com/broomva/workspace tasks/bro-167-cross-asset-trading-platform.md`

---

## Pattern H — Knowledge vault / Document repo (NON-CODE)

The case bstack governs but the other patterns miss: a repo with **no code** — a research vault, an Obsidian knowledge graph, an ADR/decisions folder, a spec library, a book manuscript, a contracts/policy repo. The governance + control plane install identically (they are markdown/YAML/TOML/bash — language-agnostic; the P2 gate, audit telemetry, L3 rate gate, and knowledge graph all govern *agent actions on files*, not a language). The one thing that has no default here is the **validation predicate**: there is no build, no test suite. `make check` defaults assume code.

The move is the universal-reduction discipline made concrete: **author a domain predicate and wire it as `make check`.** "Done" is not "tests pass" — it is "the documents are well-formed and internally consistent." That predicate must be *agent-independent* (`h ⟂ U`): a separate checker the writing agent cannot fake, exactly like a test suite is independent of the code that satisfies it.

**Reference predicate — vault integrity** (the non-code analog of a test suite). Every entity has required frontmatter; every `[[wikilink]]` resolves to a real file. Drop this at `vault-check.py` and wire `make check` to it:

```python
#!/usr/bin/env python3
"""Vault integrity predicate: required frontmatter + every [[wikilink]] resolves."""
import sys, re, pathlib
REQUIRED = {"slug", "type", "status"}
root = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else ".")
ents = sorted((root / "entities").glob("*.md"))
slugs = {p.stem for p in ents}
errs = []
for p in ents:
    t = p.read_text()
    fm = re.match(r"^---\n(.*?)\n---", t, re.S)
    keys = set(re.findall(r"^(\w+):", fm.group(1), re.M)) if fm else set()
    if REQUIRED - keys:
        errs.append(f"{p.name}: missing frontmatter {sorted(REQUIRED - keys)}")
    for link in re.findall(r"\[\[([^\]]+)\]\]", t):
        tgt = link.split("|")[0].split("#")[0].strip()
        if tgt not in slugs:
            errs.append(f"{p.name}: broken wikilink [[{tgt}]]")
if errs:
    print("VAULT CHECK FAILED:"); [print("  -", e) for e in errs]; sys.exit(1)
print(f"VAULT CHECK OK: {len(ents)} entities, frontmatter complete, all wikilinks resolve.")
```

```makefile
# The bstack validation gate, tailored to a non-code repo:
check:
	@python3 vault-check.py
```

**The predicate menu** (pick by what "well-formed" means for *your* documents — strongest first):
- **Schema / structure** (hard, `h ⟂ U`): required frontmatter present, link-graph resolves (no broken `[[wikilinks]]` / relative links), no orphan pages, IDs unique. The reference above.
- **Cross-reference integrity** (hard): every citation/reference resolves to a real source (a DOI/URL that returns 200, a `references.bib` key that exists). Mirrors the systematic-review citation-verification predicate.
- **Checklist coverage** (hard): every required section present (an RFP compliance matrix, an ADR template's mandatory fields, a contract playbook's clauses).
- **Rubric / judge** (soft, LLM-as-judge ≥ threshold): prose quality, argument completeness — needs a judge the writer doesn't control; pairs with P20 cross-review.
- **Human sign-off** (gate): the irreducible-judgment residual (legal review, editorial approval) — wire as a `require_human` merge gate in `.control/policy.yaml`.

**Dogfood Plan** (stack: knowledge-vault)

```markdown
**Dogfood Plan** (stack: knowledge-vault)

- **Entry surface**: the documents as consumed — rendered HTML/PDF, the Obsidian graph view, or a `/kg load <topic>` query
- **Driver**: the domain predicate (`vault-check.py` / a schema validator / a citation-resolver / an LLM-judge)
- **Evidence**: predicate exit 0 + a sample render of a changed page (PNG/PDF) showing it reads correctly
- **Smoke**: `make check` green (frontmatter + links resolve)
- **End-to-end**: render/query one changed document end-to-end (the link a human/agent actually follows), capture it
- **Receipt anchor**: PR body / commit message / a bookkeeping entry for the vault
```

**Gotchas**:
- A green predicate proves *well-formed*, not *correct/true* — schema validation can't judge whether a claim is right (same gap as "tests pass ≠ behavior correct"). Use the rubric/judge tier for substance, and keep the hard predicate for structure.
- Keep the predicate **independent of the authoring agent** (`h ⟂ U`). A predicate the writer can trivially satisfy by restating its own output is theater, not verification — the EGRI evaluator-immutability principle applies to documents too.
- Markdown is the agent-read substrate (Audience / P18): the *predicate* and *plan* are markdown; a human-facing render (the PDF/HTML the reader consumes) is the Category-B/C projection.

**Where this sits**: the canonical reference instance is the workspace's own `research/entities/` knowledge graph — entity pages with frontmatter + `[[wikilinks]]`, validated by `bookkeeping lint` (the production-grade version of the predicate above).

---

## Skills inventory (when to reach for which)

Ranked by P11 utility for dogfooding-from-client-POV. All are existing skills; the cookbook composes them per stack.

| Skill | Stealth | OS-level input | Replay | Network log | Best for |
|---|---|---|---|---|---|
| **Interceptor** | ✅ zero CDP fingerprint | ✅ via macOS bridge | ✅ monitor/replay | ✅ auto | Authenticated Chrome flows, real-session validation, deploy verification (MANDATORY per workspace rules) |
| **gstack** | partial | ✗ | ✗ | partial | Fast headless smoke, CI-friendly E2E, no auth state |
| **Browser** | ✗ CDP-detectable | ✗ | ✗ | ✓ | Batch headless automation |
| **agent-browser** | ✗ deprecated for visual verification | ✗ | ✗ | ✓ | Legacy; superseded by Interceptor |
| **before-and-after** | n/a | n/a | n/a | n/a | Pre/post visual diff (any stack with a URL) |
| **cliclick + screencapture** | n/a (real input) | ✅ native macOS | ✗ | ✗ | Tauri / native-app windows where WebView accessibility is opaque |
| **xcrun simctl** | n/a | ✅ simulator | ✓ recordVideo | partial | iOS simulator drive (Expo / React Native) |
| **curl + jq** | n/a | n/a | n/a | n/a | API state assertions, smoke checks |
| **/p9** | n/a | n/a | n/a | n/a | Productive-wait while CI / deploy runs |
| **/persist** | n/a | n/a | n/a | n/a | Long-horizon dogfood loops (>1h, across sessions) |
| **BrightData** | ✓ residential proxy | ✗ | ✗ | ✗ | Scale crawling (not typical dogfood, but for production-traffic-class validation) |

**Two rules** the inventory enforces:

1. **Interceptor is mandatory for visual deploy verification.** agent-browser / Browser skill are not substitutes when the question is *did this actually render correctly in a real session*.
2. **The skill list is composition, not exclusion.** A Tauri dogfood plan typically uses curl + cliclick + screencapture + Interceptor + vite logs together. The cookbook prescribes which combination for which signal.

---

## Detection algorithm (used by `bstack doctor` §13)

```text
if   exists(Cargo.toml) and exists(src-tauri/)         → Pattern A — Tauri + sidecar
elif exists(next.config.*) or has_dep("next")          → Pattern B — Next.js
elif exists(app.json) and json_has("expo")            → Pattern C — Expo / RN
elif exists(Cargo.toml) and not exists(src-tauri/)    → Pattern D — Rust CLI
elif exists("services/tradingview-bridge/") or
     glob("**/strategies/pine/*.pine") or
     has_dep(["ib-async","ccxt","py-clob-client"])     → Pattern G — Trading bot
elif exists(openapi.*) or has_dep(["fastapi","hono","axum","express"])
                                                       → Pattern E — REST API
elif frontmatter_has("tools:") or exists(mcp.{json,yaml})
                                                       → Pattern F — MCP server
elif no_code_manifest and (exists(entities/) or exists(.obsidian/) or
     exists(vault/) or count("**/*.md") >= 5)          → Pattern H — Knowledge vault / Document repo (NON-CODE)
else                                                   → Pattern Z — Unknown; agent declares stack in plan
```

where `no_code_manifest` = none of {Cargo.toml, package.json, go.mod, pyproject.toml, setup.py, pom.xml, build.gradle, Gemfile, composer.json} exist.

Trading-bot is checked *before* REST API because a trading-bot repo also matches the REST API signals (it has FastAPI / Hono routes). The more-specific match wins. Pattern H (non-code) is checked *last before the fallback*: it is the most-general match (any markdown-dominant repo), so every code pattern gets first refusal — `no_code_manifest` keeps it from ever firing on a real codebase.

Multi-stack repos (e.g. Next.js + REST API combined; Tauri + MCP-server-as-tool) produce *multiple* dogfood plans — one per surface the user perceives.

---

## Where Dogfood Plans live

Once a stack is detected, the Dogfood Plan section anchors at one of:

1. **Repo `AGENTS.md`** under `## Dogfood Plan (Stack: <pattern>)` — preferred for repos with substantial dogfood discipline already
2. **`docs/dogfood-plan.md`** — preferred for repos where AGENTS.md is governance-only
3. **PR body** — minimum-viable: every substantive PR includes the plan in the body, even if there's no canonical doc location yet

`bstack doctor` §13 looks for any of the three on a substantive feature branch. Missing all three = informational nudge (rule-of-three not yet hit; not blocking until promoted).

---

## Composition with other primitives

| Primitive | Composition |
|---|---|
| **P1** Bridge | Dogfood receipts captured at session end → JSONL → Obsidian; future agents can grep for "how did we dogfood X" |
| **P4** Pipeline | Dogfood Plan goes in the PR body; reviewers verify the plan was executed before approving |
| **P5** Fanout | Multi-stack repos fan out one dogfood agent per pattern; each agent uses its own surfaces |
| **P9** Wait | While `gh pr checks --watch` runs, the agent runs dogfood interactions on the deploy preview |
| **P11** Empirical | This cookbook IS P11's how. The plan + receipt is the discipline's concrete output. |
| **P14** Dep-Chain | The Dogfood Plan's *Entry surface* row IS the downstream-consumers entry into the dep-chain enumeration |
| **P15** Snapshot | Dogfood Plan references the deploy state surfaced in the snapshot (preview URL, dev server port) |
| **P18** Audience | Plan is markdown (agent-loaded); receipt screenshots are PNGs (binary, sidecar `.meta.yaml` per P18 Category C) |
| **P19** Orchestrate | Long dogfood loops (>1h) → `persist iterate` (P12 mechanism); in-session multi-flow → P5 fanout |
| **P20** Cross-Review | Dogfood receipt is one of the inputs cross-review evaluates: "did the writer actually exercise this, or did they ship blind?" |

---

## Anti-patterns (don't ship without addressing)

| Anti-pattern | Why it fails | Correct |
|---|---|---|
| "It compiles, shipping" | P11 invariant — compile-time success is not deploy-time correctness | Exercise the change end-to-end against the deployed version |
| "CI is green, shipping" | CI tests what CI was told to test; the user-perceived flow is broader | Dogfood receipt that exercises the user-perceived flow, not just the unit test |
| "I'll dogfood after merge" | Post-merge dogfood = bug-found-by-user, not bug-caught-by-discipline | Dogfood plan executed BEFORE merge, evidence in PR body |
| "Same as last time, didn't re-check" | Stack changes silently (port shifted, token rotated, route renamed) | Each substantive PR fires the plan fresh; never trust stale receipts |
| "Screenshot from local, same as prod" | Local ≠ deploy preview ≠ production | Capture the deployed surface, not local — Vercel preview / EAS build / production endpoint |
| Agent-browser for deploy verification | Bot detection + missed visual state | Interceptor — mandatory per workspace rules |
| Ritual-style "did I exercise it" without evidence | Ritual; P11 #6 requires the *receipt* | Every "yes" in the receipt anchors to a file path, log line, or response body |

---

## Receipt template

The dogfood receipt is the artifact that closes the loop. It goes in the PR body or `docs/dogfood-receipts/<date>-<ticket>.md` for repos with substantial history.

```markdown
**Dogfood Receipt** — <ticket> · <date>

| Plan row | Executed | Evidence |
|---|---|---|
| Smoke | ✅ | `curl ... \| head -1` → 200; log line at /tmp/dev.log:42 |
| End-to-end | ✅ | Screenshots: /tmp/shots/{baseline,after,final}.png |
| API contract | ✅ | curl response captured in PR comment #3 |
| Side-effect | ✅ | DB query before/after: count went 0 → 1 |
| Deploy preview | ✅ | Vercel preview <url> — Interceptor screenshot /tmp/preview.png |
| <row that wasn't applicable> | ⊘ | Reason: <e.g. backend mocked in PR; real cloud creds not available> |

**Anti-rationalization check**: did I actually click the app like a user would? <yes/no>
**Surfaces driven**: <Interceptor / cliclick / curl / etc.>
**Time-to-receipt**: <duration> from first write to receipt complete.
```

The "anti-rationalization check" is the test against P11's failure mode (ritual acknowledgment without substance). If the answer is no, the PR is not ready — go back to the plan.

---

## Promotion gating (rule-of-three for elevation to gate)

The §13 check ships as **informational** (warn-only) in bstack v0.13.0. Promotion to a `policy.yaml` blocking gate requires:

1. **≥3 documented incidents** where a missing Dogfood Plan caused a user-visible regression (logged in `research/entities/pattern/bstack-engine.md` candidate ledger with file citations)
2. **Concrete blocking criterion** ("PR has no Dogfood Plan in body" is the unambiguous mechanical check)
3. **Failure mode named** (which it is: P11 ritual without substance)
4. **L3 stability budget available** (λ₃ ≈ 0.006 — governance changes must be rare; promotion to blocking is one such change)

Until promoted, the §13 check is the warning surface; the cookbook is the agent's how-to; the receipt template is the artifact that proves discipline was applied.
