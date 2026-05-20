# LLM Provider Standards (bench)

> **Audience**: bstack maintainers + anyone wiring a new live LLM backend
> into `bstack bench`. Agent-readable substrate (markdown, per P18).

## The contract bstack adopts

**OpenAI Chat Completions API v1** — the de facto LLM provider contract in 2026.

```
POST {base_url}/chat/completions
Authorization: Bearer <token>
Content-Type: application/json

{
  "model": "<model-id>",
  "messages": [
    {"role": "system", "content": "..."},
    {"role": "user",   "content": "..."}
  ],
  "max_tokens": 4096,
  "temperature": 0.0
}
```

Response:

```
{
  "id": "...",
  "model": "<resolved-model-id>",
  "choices": [
    {
      "index": 0,
      "message": { "role": "assistant", "content": "..." },
      "finish_reason": "stop"
    }
  ],
  "usage": {
    "prompt_tokens": 19,
    "completion_tokens": 6,
    "total_tokens": 25
  }
}
```

## Why this contract

1. **Industry alignment** — the same JSON shape is served by:
   - **Databricks Model Serving** (Anthropic Claude, Meta Llama, etc. behind one gateway)
   - **OpenAI** itself
   - **Together**, **Fireworks**, **Anyscale**, **Groq**, **Perplexity**
   - **vLLM**, **llama.cpp**, **TGI** (self-hosted)
   - **Anthropic via AWS Bedrock** (with a thin adapter)
   - **Vertex AI** model garden (with a thin adapter)
2. **Token semantics are uniform** — `usage.{prompt,completion,total}_tokens` is universal; bench's per-task cost accounting reads consistently.
3. **Anthropic's `messages.create` is a rotated version of the same shape** — providers that don't expose an OpenAI front-end (raw Anthropic SDK, Vertex) translate inside the provider class; bench code above stays clean.

## Provider abstraction (Python)

```python
from bench.providers import Provider, ChatMessage, ChatCompletion, get_provider

provider = get_provider("databricks")  # or "mock", future: "anthropic", "openai", ...

response: ChatCompletion = provider.chat(
    messages=[
        ChatMessage(role="system", content="..."),
        ChatMessage(role="user",   content="..."),
    ],
    model="databricks-claude-haiku-4-5",
    max_tokens=4096,
    temperature=0.0,
)

print(response.content)
print(response.usage.total_tokens)
print(response.model)         # resolved model ID (richer than the alias)
print(response.finish_reason)
```

Three types are public:

- `ChatMessage` — role-tagged message
- `Usage` — `prompt_tokens` + `completion_tokens` + `total_tokens`
- `ChatCompletion` — `content` + `model` + `usage` + `finish_reason` (+ `raw` for forward-compat)

Three exception types:

- `ProviderNotInstalled` — optional SDK missing (e.g. `pip install openai`)
- `ProviderNotConfigured` — required env vars missing (e.g. `DATABRICKS_TOKEN`)
- `ProviderError` — wraps upstream SDK errors uniformly

## Built-in providers (v0.11.0)

| Name | Backend | Required env | Models |
|---|---|---|---|
| `databricks` | Databricks Model Serving (OpenAI-compatible) | `DATABRICKS_HOST`, `DATABRICKS_TOKEN` | `databricks-claude-haiku-4-5`, `databricks-claude-sonnet-4`, `databricks-claude-opus-4-5`, `databricks-meta-llama-4-maverick` |
| `mock` | In-process deterministic stub | — | `mock-small`, `mock-large` |

Future providers (planned, not yet shipped):

| Name | Backend | Required env |
|---|---|---|
| `anthropic` | Anthropic API direct | `ANTHROPIC_API_KEY` |
| `openai` | OpenAI API direct | `OPENAI_API_KEY` |
| `openai-compat` | Generic OpenAI-compatible endpoint | `OPENAI_BASE_URL`, `OPENAI_API_KEY` |
| `bedrock` | Anthropic via AWS Bedrock | `AWS_*` |

## How to add a new provider

1. Create `scripts/bench/providers/<name>.py` with a class that subclasses `Provider` and implements `configured()`, `list_models()`, and `chat()`.
2. Soft-import the SDK in `chat()` (or `__init__`) and raise `ProviderNotInstalled` if missing — never make the SDK a hard dep.
3. Read credentials from env vars in `__init__`; raise `ProviderNotConfigured` if missing.
4. Map upstream errors to `ProviderError` (preserve original via `raise ... from exc`).
5. Add an entry in `scripts/bench/providers/registry.py:_BUILTIN_PROVIDERS` of the form `"<name>": "bench.providers.<name>:<ClassName>"`.
6. Add a row to the table above + the cost table in `base.py:_COST_TABLE_USD_PER_MILLION`.
7. Add provider-specific tests in `tests/bench-providers.test.sh`.

## Recommended invocation patterns

### Direct (env already exported)

```bash
export DATABRICKS_HOST=https://...azuredatabricks.net
export DATABRICKS_TOKEN=dapi...
bstack bench run --runner live --provider databricks \
    --model databricks-claude-haiku-4-5 \
    --judge-model databricks-claude-opus-4-5 \
    --phase 1
```

### Railway as credential broker (recommended for shared dev envs)

When credentials live in Railway (the bstack-broomva-stimulus convention), use `railway run` to inject env vars without writing them to disk:

```bash
railway run --service stimulus-api -- bstack bench run \
    --runner live --provider databricks \
    --model databricks-claude-haiku-4-5 \
    --judge-model databricks-claude-opus-4-5 \
    --phase 1
```

### 1Password / sops / direnv / vault

Any tool that exports env vars works. The provider class never sees the credential storage system — it only reads `os.environ`.

## P20 model-isolation enforcement

Bench enforces **Cross-Review (P20)** at the layer where it matters most: the LLM judge.

**Rule:** when `--evaluator llm-judge` is selected, the judge model MUST differ from the agent model. Same model judging itself is exactly the single-model-echo-chamber failure P20 exists to prevent.

```bash
# ❌ Rejected — same model agent + judge
bstack bench run --runner live --provider databricks \
    --model databricks-claude-haiku-4-5 \
    --evaluator llm-judge --judge-model databricks-claude-haiku-4-5
# → exit 8: "judge model equals agent model"

# ✅ Accepted — distinct models
bstack bench run --runner live --provider databricks \
    --model databricks-claude-haiku-4-5 \
    --evaluator llm-judge --judge-model databricks-claude-opus-4-5

# ⚠️  Override only with --allow-same-judge-model (must pass rationale)
bstack bench run --runner live --provider databricks \
    --model databricks-claude-haiku-4-5 \
    --evaluator llm-judge --judge-model databricks-claude-haiku-4-5 \
    --allow-same-judge-model "smoke test only — not a quality measurement"
# → warning logged + rationale captured in run config.json
```

Same-provider, different-model is the cheapest path to compliance. Cross-provider (e.g. agent on Databricks Claude, judge on OpenAI GPT-4o) is the strongest. Bench logs both paths.

## Anti-patterns

- **Don't** hardcode credentials anywhere in bstack. The provider class reads `os.environ`; how those env vars get there is the caller's concern.
- **Don't** make any SDK a hard dependency of bstack. Soft-import only.
- **Don't** bypass the registry — always call `get_provider(name)`, never instantiate `DatabricksGatewayProvider(...)` directly from bench code (tests are the exception).
- **Don't** treat `raw` as part of the public contract. It exists for one-off forward-compat reads (logprobs, structured outputs) and may be `None` for stub/mock providers.
- **Don't** allow same-model judge silently. P20 violation requires explicit `--allow-same-judge-model` opt-out with rationale.

## References

- OpenAI Chat Completions API: https://platform.openai.com/docs/api-reference/chat
- Databricks Foundation Model APIs: https://docs.databricks.com/en/machine-learning/foundation-models/index.html
- Stimulus reference implementation: `apps/api/src/utils/databricks_openai.py` (in the stimulus repo)
- bstack bench spec: `specs/bench-skill-evolution.md`
- P20 Cross-Review primitive: `SKILL.md` § Bstack Core Automation Primitives
