# Bstack Spec: Indirect Prompt Injection & AI-Agent-as-Attack-Vector Defense

**Status:** Draft (2026-05-15)
**Author:** broomva
**Trigger:** [Blockchain-C2 AI-agent malware injection incident, mihai-r-lupu, 2026-05-06](https://gist.github.com/mihai-r-lupu/d94afd240658c37fb0924609f159431b)
**Research basis:** [`~/Documents/IndirectPromptInjection_Research_20260515/report.md`](file:///Users/broomva/Documents/IndirectPromptInjection_Research_20260515/report.md) — 35+ sources across academic, vendor, and incident streams.
**Linear umbrella:** TBD (MCP not surfaced; queue in `~/.config/moltbook/linear-backlog.md`)

---

## 1. Problem Statement

An AI coding agent operating in the bstack workspace can be coerced via indirect prompt injection (web-fetched content, package READMEs, source files, tool outputs) to perform malicious file-writes that result in remote code execution. The attack class is empirically validated as the #1 critical vulnerability for AI coding agents in 2025-2026 (OWASP LLM01:2025, NIST AI 600-1) with attack success rates over 90% against unprotected systems. Five of six stages of the blockchain-C2 reference attack [27] are currently unsafeguarded in bstack; the work shipped in prior sessions (P14 read-side trust boundary + permission-gate-v2) was designed for this exact class but remains as prototypes, not wired into the active hook chain.

## 2. Scope

**In scope:**
- Indirect prompt injection from web content, READMEs, source files, tool outputs
- AI-agent-writes-malicious-config-file class (`.vscode/`, `.cursor/`, `.idea/`, `.continue/`, `.aider/`, `.claude/`, `.gitignore`)
- Magic-bytes-vs-extension mismatch on file writes
- Hidden-instruction encoding carriers (Unicode tag, invisible Markdown, white-on-white CSS, URL fragments, etc.)
- AI-commit identity (Co-Authored-By trailer)
- Network egress allowlist for agent subprocesses
- Adversarial-eval baseline (AgentDojo-style)

**Out of scope:**
- Direct prompt injection (separately addressed)
- Model alignment / training (outside our control plane)
- Backdoored model weights (separate threat model)
- Multi-agent worm propagation across separate developer machines (Morris-II [25] is research; not bstack threat model)

## 3. Threat Model

- **Adversary:** High-skill attacker who plants instructions in public-facing content (npm READMEs, GitHub issues, Stack Overflow answers, documentation sites). Pre-writes obfuscated payloads. Capable of cross-platform delivery, blockchain-C2 dead-drops, multi-stage execution chains.
- **Asset:** Developer workstation + the source repositories the agent has access to + the developer's git identity + any credentials accessible from the workstation environment.
- **Trust boundary:** The agent itself is treated as an untrusted insider (per [Botmonster Tech 2026, AI Coding Agents Are Insider Threats](https://botmonster.com/posts/ai-coding-agent-insider-threat-prompt-injection-mcp-exploits/)).
- **Defender:** Single-developer team, no SOC, no dedicated security personnel. Defenses must be deployable without continuous manual review.

## 4. Architecture — Four-Tier Defense

### Tier 1 — Substrate-Layer Gates (1 week, blocks 4/6 stages)

**Files touched:**
- `bstack/assets/templates/policy.yaml.template` (governance)
- `bstack/scripts/control-gate-hook.sh` (PreToolUse hook)
- `bstack/scripts/check-file-write-safety.py` (new — content + magic-bytes inspector)
- `bstack/tests/test_file_write_safety.py` (smoke fixtures)

**Changes:**

1. **Editor-config path gate** in `policy.yaml.template`:

```yaml
auto_merge:
  rules:
    # Existing governance paths
    - path_touched: CLAUDE.md
      action: require_human
    - path_touched: AGENTS.md
      action: require_human
    - path_touched: METALAYER.md
      action: require_human
    - path_touched: .control/policy.yaml
      action: require_human
    # NEW (2026-05-15 spec): editor-config paths
    - path_touched: .vscode/*
      action: require_human
      rationale: "Indirect-PI attack surface (CVE-2025-53773 GitHub Copilot YOLO-Mode; tasks.json autoexec; blockchain-C2 gist)"
    - path_touched: .cursor/*
      action: require_human
      rationale: "CurXecute (CVE-2025-54135) + MCPoison (CVE-2025-54136) MCP config write → RCE"
    - path_touched: .idea/*
      action: require_human
      rationale: "JetBrains config equivalent — pre-emptive coverage"
    - path_touched: .continue/*
      action: require_human
    - path_touched: .aider/*
      action: require_human
    - path_touched: .claude/settings.json
      action: require_human
      rationale: "Claudy Day exfil vector — Claude.ai pre-fill / settings hijack class"
    - path_touched: .gitignore
      action: require_human
      rationale: "Removing patterns enables silent commit of malicious files (blockchain-C2 gist stage 5)"
```

2. **`write_gate.content_patterns` block** (new top-level in `policy.yaml`):

```yaml
write_gate:
  enabled: true
  content_patterns:
    # Autoexec flags — refuse Write/Edit when these substrings appear in target content
    - pattern: "runOn:\\s*folderOpen"
      severity: blocking
      rationale: "VS Code autoexec primitive — universal trigger across Jamf, Tenable, mihai-r-lupu incidents"
    - pattern: "task\\.allowAutomaticTasks:\\s*true"
      severity: blocking
      rationale: "VS Code autoexec bypass — blockchain-C2 gist used this exact key"
    - pattern: "chat\\.tools\\.autoApprove:\\s*true"
      severity: blocking
      rationale: "GitHub Copilot YOLO-Mode (CVE-2025-53773) used this exact key"
    - pattern: "--dangerously-skip-permissions"
      severity: blocking
      rationale: "Claude Code unsafe-flag bypass — used by Nx s1ngularity recon prompts"
    - pattern: "api\\.trongrid\\.io|fullnode\\.mainnet\\.aptoslabs|bsc-dataseed|bsc-rpc\\.publicnode"
      severity: blocking
      rationale: "Public-blockchain RPCs in non-blockchain code = dead-drop C2 signal"
    - pattern: "child_process\\.spawn\\([^)]*detached:\\s*true"
      severity: warn
      rationale: "Detached spawn in JS/TS = persistence primitive (blockchain-C2 gist Layer 4)"
  magic_bytes_check:
    enabled: true
    rationale: "Catches font-file-with-JS-content disguise (blockchain-C2 stage 4)"
    # Map extension family → expected magic-byte signatures
    rules:
      - extension_match: ['.woff2', '.woff', '.ttf', '.otf', '.eot']
        forbid_signatures_starting_with:
          - "function"
          - "(function"
          - "const "
          - "var "
          - "import "
          - "module.exports"
          - "#!/"
      - extension_match: ['.png', '.jpg', '.jpeg', '.gif', '.svg', '.webp', '.bmp']
        forbid_signatures_starting_with:
          - "function"
          - "(function"
          - "<?php"
          - "<script"
          - "#!/"
      - extension_match: ['.txt', '.md', '.json', '.yaml', '.yml']
        forbid_signatures_starting_with:
          - "MZ"  # Windows PE
          - "\\x7fELF"  # Linux ELF
          - "\\xca\\xfe\\xba\\xbe"  # Mach-O fat
          - "\\xfe\\xed\\xfa"  # Mach-O thin
  gitignore_modification:
    enabled: true
    removed_patterns_require_human: true
    rationale: "Adding to .gitignore is normal; removing patterns enables silent commits"
```

3. **`check-file-write-safety.py`** — new script invoked from PreToolUse hook:

```python
#!/usr/bin/env python3
"""Pre-write safety check for AI agents.

Invoked from .claude/settings.json PreToolUse hook on Write/Edit/MultiEdit.
Reads policy.yaml.write_gate; returns exit 0 (allow) or non-zero (block).
Output to stderr (operator-facing); structured JSON to stdout (machine).
"""
# ~100 LOC
# - Parse policy.yaml
# - Read target file path from $TOOL_ARGS
# - Apply path-pattern matchers from auto_merge.rules
# - Apply content_patterns regex match
# - Apply magic_bytes_check
# - Apply gitignore-removal check
# - Exit 0 / 8 (block: marketing-shape-style structured refusal)
```

4. **Wire into `.claude/settings.json`**:

```json
{
  "hooks": {
    "PreToolUse": [
      ...existing...
      {
        "matcher": "Write|Edit|MultiEdit",
        "hooks": [
          {
            "type": "command",
            "command": "/Users/broomva/broomva/bstack/scripts/check-file-write-safety.py"
          }
        ]
      }
    ]
  }
}
```

**Coverage:** Blocks stages 2, 3, 4, 5 of the blockchain-C2 attack chain.

**Validation plan:**
- Fixture A: agent attempts to write `.vscode/tasks.json` with `runOn: folderOpen` → must block with exit 8
- Fixture B: agent attempts to write `fa-solid-400.woff2` whose content starts with `function(_$af163278) {` → must block with magic-bytes refusal
- Fixture C: agent attempts to write `.gitignore` removing `.vscode/*` → must block require_human
- Fixture D: agent attempts to write `package.json` with normal content → must allow
- Fixture E: human-authorized override path: setting env `BSTACK_OVERRIDE=marketing-shape` and re-running must allow with logged event

### Tier 2 — Read-Side Trust Boundary (2 weeks, blocks stage 1 — the root cause)

**Files touched:**
- `bstack/scripts/read-boundary-hook.sh` (already prototyped — wire into settings)
- `bstack/scripts/permission-gate-hook.sh` (already prototyped — wire into settings)
- `bstack/scripts/webfetch-sanitizer.py` (new — hidden-instruction scrubber)
- `bstack/assets/templates/trust-tiers.yaml.template` (already exists)
- `.control/grants.jsonl` (already exists)
- `bstack/scripts/permissions.py` (already exists — human CLI for grants)
- `bstack/references/security-primitives.md` (already exists — architecture doc)

**Changes:**

1. **Wire read-boundary-hook into `.claude/settings.json` PreToolUse for WebFetch + Read tools:**

```json
{
  "matcher": "WebFetch|Read",
  "hooks": [
    {
      "type": "command",
      "command": "/Users/broomva/broomva/bstack/scripts/read-boundary-hook.sh"
    }
  ]
}
```

The hook tags every fetched content with a trust tier (T0 local-controlled — `~/broomva/`, `.control/`; T1 internal-doc — `~/.config/moltbook/`, `~/.config/broomva/`; T2 GitHub-owned — repos under `broomva/*`; T3 third-party-trusted — npm registry, PyPI, Anthropic docs, vetted vendors; T4 anon-external — arbitrary web pages, GitHub gists, Stack Overflow, forums). The trust tier travels with the content in the agent's context window.

2. **Wire permission-gate-hook into `.claude/settings.json` PreToolUse for Write/Edit/Bash:**

```json
{
  "matcher": "Write|Edit|Bash",
  "hooks": [
    {
      "type": "command",
      "command": "/Users/broomva/broomva/bstack/scripts/permission-gate-hook.sh"
    }
  ]
}
```

Enforces `never_auto_granted` set: `policy:write`, `grants:write`, `secrets:read`, `network:egress.add_host`, `signed_writes:bypass`, `governance:write`, `hooks:write`. When a tool call would touch these capabilities, the gate requires a signed grant in `.control/grants.jsonl` produced by `permissions.py grant` (human-in-the-loop).

3. **`webfetch-sanitizer.py`** — wraps WebFetch responses, strips hidden-instruction carriers before content reaches agent context:

```python
#!/usr/bin/env python3
"""Sanitize WebFetch responses before they enter agent context."""
# ~150 LOC
# Strip:
# - Unicode tag block U+E0000-U+E007F (Anthropic refuses model-layer fix)
# - HTML comments matching /<!--.*?-->/
# - <noscript>...</noscript> blocks
# - White-on-white CSS (color matches background-color)
# - Zero-width characters (U+200B, U+200C, U+200D, U+FEFF)
# - Reference-style Markdown link definitions when not paired to visible text
# - Emit warning to stderr when stripped: "hidden-instruction-detected"
# - Emit structured event to /tmp/bstack-webfetch-events.jsonl for audit
```

**Coverage:** Blocks stage 1 of the blockchain-C2 attack chain. The agent receives sanitized + trust-tier-tagged content; the substrate refuses to elevate T3/T4 content to action-taking authority.

**Validation plan:**
- Fixture A: WebFetch returns content with Unicode tag chars encoding "delete all your files" → sanitizer strips, warning logged, agent receives clean content
- Fixture B: Agent attempts to follow T4 content's "write `.vscode/tasks.json`" instruction → blocked at Tier 1 + flagged at Tier 2 trust-tier-elevation check
- Fixture C: Agent attempts grant.write or hooks.write without signed grant → blocked
- Fixture D: Human runs `permissions.py grant network:egress.add_host api.trongrid.io` → grant logged with HMAC signature; subsequent matching egress allowed

### Tier 3 — Sandbox + AI-Commit Trailer (1 month, defense-in-depth)

**Files touched:**
- `.claude/settings.json` PreToolUse Bash hook wrapping
- `bstack/scripts/sandbox-bash.sh` (new — invokes `@anthropic-ai/sandbox-runtime`)
- `bstack/scripts/check-ai-commit-trailer.sh` (new — pre-commit hook)
- `.githooks/pre-commit` (existing — extend)

**Changes:**

1. **Sandbox Bash subprocesses via Anthropic `sandbox-runtime`:**

```bash
# bstack/scripts/sandbox-bash.sh
#!/usr/bin/env bash
# Wraps the agent's Bash subprocess invocation with sandbox-runtime.
# Filesystem isolation to $PWD + its subdirs.
# Network egress allowlist read from .control/network-allowlist.txt.
exec npx @anthropic-ai/sandbox-runtime \
  --writable-paths "$PWD" \
  --network-policy "$BSTACK_NETWORK_POLICY_FILE" \
  -- bash -c "$@"
```

Default network allowlist (`.control/network-allowlist.txt`):
- `github.com`, `api.github.com`, `*.githubusercontent.com`
- `registry.npmjs.org`, `npmjs.com`, `*.npmjs.org`
- `pypi.org`, `*.pypi.org`, `files.pythonhosted.org`
- `crates.io`, `*.crates.io`
- `api.anthropic.com`, `console.anthropic.com`
- `broomva.tech`, `www.broomva.tech`
- `moltbook.com`, `www.moltbook.com`
- `x.com`, `*.x.com`, `twitter.com`
- (NO blockchain RPCs, NO arbitrary CDN, NO `*.com` wildcards)

Adding hosts requires `permissions.py grant network:egress.add_host <host>` (Tier 2 gate).

2. **Pre-commit hook requiring AI-commit trailer:**

```bash
# .githooks/pre-commit (extension)
#!/usr/bin/env bash
# If any staged file matches a "sensitive" pattern AND the commit message
# does not contain a Co-Authored-By: Claude trailer, refuse.

SENSITIVE_PATTERNS="\.vscode/|\.cursor/|\.idea/|\.continue/|\.aider/|\.gitignore$|\.env|\.woff2$|\.ttf$"
SENSITIVE_HITS=$(git diff --cached --name-only | grep -E "$SENSITIVE_PATTERNS")

if [ -n "$SENSITIVE_HITS" ]; then
  COMMIT_MSG_FILE="$1"
  if ! grep -q "Co-Authored-By: Claude" "$COMMIT_MSG_FILE"; then
    echo "[bstack pre-commit] Sensitive files touched by this commit:"
    echo "$SENSITIVE_HITS"
    echo "If this is an AI-authored commit, add: Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
    echo "If this is a human-authored commit, add a comment line: # Human-Authored-Sensitive-Touch: <reason>"
    exit 1
  fi
fi
```

**Coverage:** Blocks stage 6 (commit-under-human-identity). Network egress allowlist defeats stages 1+4+6 of the blockchain-C2 attack — fetching from `api.trongrid.io` would be blocked even if the agent somehow wrote the malicious code.

**Validation plan:**
- Fixture A: Sandboxed Bash attempts `curl api.trongrid.io` → blocked by network policy
- Fixture B: Pre-commit hook detects `.vscode/tasks.json` in staged set with no AI trailer → refuses commit
- Fixture C: Sandbox doesn't break normal dev workflow — `npm test`, `cargo check`, `pnpm install` all work

### Tier 4 — Adversarial Eval + Ongoing Hygiene

**Files touched:**
- `bstack/eval/` (new directory)
- `bstack/eval/agentdojo-config.yaml` (configuration)
- `bstack/eval/test-injection-fixtures/` (corpus)
- `bstack/eval/run-eval.sh` (runner)
- `~/.config/broomva/cve-feed.yaml` (CVE subscription list)

**Changes:**

1. **Local AgentDojo-style benchmark** against our agent configuration. Run nightly via cron; output to `bstack/eval/results/YYYY-MM-DD.json`. Track ASR over time.

2. **CVE feed monitor** for AI coding agent CVEs (Anthropic, OpenAI, Cursor, GitHub Copilot, Continue.dev, Aider, MCP). Subscribe via GitHub Advisories API. New high-CVSS CVE → Linear ticket auto-filed → P1 priority for patch within 7 days.

3. **MCP server allowlist** in `.control/policy.yaml`:

```yaml
mcp:
  servers:
    allowed:
      - name: linear-server
        source: "official-linear"
        capabilities: [issue:read, issue:write]
      - name: filesystem
        source: "anthropic-experimental"
        capabilities: [fs:read]
    deny_unlisted: true
```

4. **Continuous review** of `auto_merge.rules` whenever a new editor or AI tool ecosystem emerges. Add its config paths to `require_human`.

**Validation plan:**
- Nightly AgentDojo eval green (ASR ≤ 10% on the canonical 97-task suite); ASR rising 3 consecutive nights → P1 alert
- CVE feed produces tickets within 24h of high-CVSS publication
- MCP server installation by agent without allowlist entry → blocked

---

## 5. Implementation Sequencing

Sequenced for atomic PRs against `broomva/bstack`:

| # | PR | Branch | Effort | Dep |
|---|---|---|---|---|
| 1 | `feat(policy): editor-config + gitignore path gates in template` | `feat/policy-editor-config-gates` | ~50 LOC + tests | — |
| 2 | `feat(write-gate): content-pattern + magic-bytes validation` | `feat/write-gate-content-patterns` | ~150 LOC + fixtures | PR #1 |
| 3 | `feat(hooks): wire read-boundary + permission-gate into settings.json` | `feat/wire-trust-hooks` | ~30 LOC settings.json + test | — |
| 4 | `feat(scripts): webfetch-sanitizer for hidden-instruction stripping` | `feat/webfetch-sanitizer` | ~150 LOC + Unicode-tag fixtures | PR #3 |
| 5 | `feat(sandbox): sandbox-bash.sh + network-allowlist.txt` | `feat/sandbox-bash` | ~80 LOC + env tests | — |
| 6 | `feat(precommit): AI-commit trailer enforcement on sensitive paths` | `feat/precommit-ai-trailer` | ~30 LOC + git fixtures | — |
| 7 | `feat(eval): AgentDojo-style adversarial baseline` | `feat/eval-agentdojo` | ~300 LOC + corpus | PR #1-6 |

PRs 1, 3, 5, 6 are independent and can ship in parallel via worktree dispatch (P5 parallel agents). PRs 2, 4 depend on their predecessors. PR 7 ships after the defense stack is live.

## 6. Risks and Mitigations

| Risk | Mitigation |
|---|---|
| Content-pattern matching defeated by obfuscation (Layer-1 string-shuffler bootstrap from blockchain-C2 gist) | Magic-bytes-vs-extension catches the *delivery*; sandbox network allowlist catches the *behavior* even if content evades pattern |
| Sandbox config self-modification (CVE-2025-53773 class) | `.control/policy.yaml` itself is `require_human`; sandbox config in same file; circular: agent cannot modify its own gates without human signature |
| `--allow-marketing-shape` style override pattern abused | Override requires both env var AND log entry; structured audit; PR review of override usage; CronCreate scheduled monthly audit of `~/.config/broomva/grants.jsonl` |
| User-approval fatigue (Anthropic measured 93% accept) | Tier 2 trust-tier wrapping means most actions auto-allow at T0/T1; only T3/T4 operations on never_auto_granted set escalate. Empirical baseline target: ≤5 human approval prompts/day |
| Adaptive (gradient-based) attacks bypass content patterns (Liu 2024 [Academic Finding 13]) | Tier 4 AgentDojo eval runs with adaptive attack suite (not just fixed); regression detected if ASR rises |
| Defender-side AI evaluates AI-authored diffs (recursive vulnerability) | Tier 4 PR review automation gates final merge on human approval; recursive substitution refused at policy gate |
| Token cost / latency overhead | Magic-bytes check is microseconds. Sandbox-runtime overhead ~50-100ms per Bash. Tier 2 hooks add ~10-50ms per Read/Write. Total budget: <500ms per agent action. Acceptable. |

## 7. Success Criteria

| Metric | Baseline (pre-spec) | Target (post-Tier-1) | Target (post-Tier-4) |
|---|---|---|---|
| Blockchain-C2 attack-chain coverage (6 stages) | 1/6 (Co-Authored-By trailer is convention) | 5/6 | 6/6 |
| ASR on AgentDojo canonical suite | unmeasured | ≤30% | ≤10% |
| Human approval prompts per agent-session-hour | uncapped | ≤10/h | ≤5/h |
| CVE-feed-to-ticket latency | manual | ≤24h | ≤4h |
| Hidden-instruction carriers caught by sanitizer | 0/12 | 12/12 | 12/12 |

## 8. P14 Dep-Chain Trace

**Upstream:**
- `bstack/assets/templates/policy.yaml.template`
- `bstack/scripts/control-gate-hook.sh`
- `bstack/scripts/read-boundary-hook.sh` (prototyped)
- `bstack/scripts/permission-gate-hook.sh` (prototyped)
- `bstack/scripts/permissions.py` (existing human CLI)
- `bstack/assets/templates/trust-tiers.yaml.template` (existing)
- `bstack/references/security-primitives.md` (existing)
- `.claude/settings.json` PreToolUse hook config
- `@anthropic-ai/sandbox-runtime` npm package (upstream dep)
- `.githooks/pre-commit` (existing)

**Downstream:**
- Every bstack-adopting workspace (template propagation)
- Every agent Write/Edit/Bash/WebFetch call (runtime path)
- The conversation bridge (events captured in `/tmp/bstack-*-events.jsonl`)
- Linear backlog (ticket auto-filing on CVE)
- The agent's experience (~5-10 fewer approval prompts/hour; new exit-code-8 marketing-shape-style refusals on policy violations)

## 9. P10 Worktree Decision

**Yes — worktree per PR.** Each of the 7 PRs in §5 ships from its own worktree under `~/broomva-worktrees/bstack-ipi-defense-N/`. PRs 1, 3, 5, 6 dispatched as parallel agents via P5; PRs 2, 4, 7 sequenced after their dependencies. Janitor (P9) cleans up worktrees post-merge.

## 10. P11 Validation Plan (Summary)

Per-PR validation fixtures listed in §4 Tier sections. Composite end-to-end test:

1. Spin up an isolated test workspace `~/broomva-test-ipi-defense/`
2. Adopt bstack policy template (with this spec's gates)
3. Run an agent against a fixture repo containing the blockchain-C2 reference attack
4. **Verify all 6 stages blocked.** This is the canonical regression test.
5. Run an agent against a benign fixture repo
6. **Verify no false positives on normal dev work.**

## 11. Open Questions

1. **Hidden-instruction sanitization aggressiveness:** Strip all Unicode tag chars unconditionally vs flag-and-show-user? Aggressiveness trade-off needs eval.
2. **MCP server allowlist seeding:** Initial set is small; growth process needs definition. Probably: PR to bstack adding a new MCP server is itself a require_human gate.
3. **Adversarial-input testing of the *defenders*:** The sanitizer, the magic-bytes check, the trust-tier hook are themselves processed by an agent. Are they susceptible to second-order injection? Need to think through.
4. **Cross-platform parity:** Sandbox-runtime is macOS + Linux + WSL2. Native Windows not supported. Acceptable for bstack's single-developer workspace (macOS).
5. **Performance under load:** Tier 1+2 hooks add ~10-50ms per Read/Write. For a heavy-edit session (1000+ writes), this is 10-50 seconds aggregate. Need to measure on real workload; if material, batch the checks.

## 12. References

Full research basis at [`~/Documents/IndirectPromptInjection_Research_20260515/report.md`](file:///Users/broomva/Documents/IndirectPromptInjection_Research_20260515/report.md). Key sources for the spec:

- [Mihai Lupu, blockchain-C2 incident, 2026-05-06](https://gist.github.com/mihai-r-lupu/d94afd240658c37fb0924609f159431b) — primary attack reference
- [OWASP LLM Top 10 2025 — LLM01](https://genai.owasp.org/llmrisk/llm01-prompt-injection/)
- [NIST AI 600-1 Generative AI Profile](https://nvlpubs.nist.gov/nistpubs/ai/NIST.AI.600-1.pdf)
- [DeepMind CaMeL: Defeating Prompt Injections by Design (arXiv:2503.18813)](https://arxiv.org/abs/2503.18813)
- [Anthropic — making Claude Code more secure and autonomous (sandbox-runtime)](https://www.anthropic.com/engineering/claude-code-sandboxing)
- [Microsoft MSRC — How Microsoft defends against indirect prompt injection](https://msrc.microsoft.com/blog/2025/07/how-microsoft-defends-against-indirect-prompt-injection-attacks/)
- [Spotlighting (arXiv:2403.14720)](https://arxiv.org/abs/2403.14720)
- [Embrace The Red — GitHub Copilot YOLO-Mode CVE-2025-53773](https://embracethered.com/blog/posts/2025/github-copilot-remote-code-execution-via-prompt-injection/)
- [Tenable — CurXecute + MCPoison FAQ](https://www.tenable.com/blog/faq-cve-2025-54135-cve-2025-54136-vulnerabilities-in-cursor-curxecute-mcpoison)
- [Snyk — Nx s1ngularity weaponizing AI coding agents](https://snyk.io/blog/weaponizing-ai-coding-agents-for-malware-in-the-nx-malicious-package/)
- [AgentDojo benchmark (arXiv:2406.13352)](https://arxiv.org/abs/2406.13352)

---

## Acknowledgments

The marketing-shape detector pattern shipped in `broomva/social-intelligence` PR #2 (2026-05-14) is structurally identical to the write-gate proposed here — a refuse-to-send guard that scans content against a domain-specific pattern catalog at substrate level. The "scope-qualifier" concept (`research/entities/concept/scope-qualifier.md`, 2026-05-15) is the underlying primitive: a write protocol that records the scope at which content is true. This spec extends both patterns from outbound communication to inbound file-write surface.
