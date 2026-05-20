"""bench.providers.base — Provider ABC + OpenAI-compatible types.

The contract bstack adopts is **OpenAI Chat Completions API v1**.
A `Provider` is anything that accepts an ordered list of role-tagged messages
and returns a single assistant message with token usage stats.

Three reasons OpenAI's shape is the right abstraction layer:

1. **Industry alignment** — Databricks, vLLM, Together, Fireworks, Anyscale,
   llama.cpp, Anthropic-via-Bedrock, Mistral La Plateforme, Cohere, Groq, and
   the OpenAI API itself all serve `/chat/completions` with identical request +
   response JSON. Choosing a different abstraction would force translation at
   every integration.
2. **Token semantics are uniform** — `usage.prompt_tokens` / `completion_tokens` /
   `total_tokens` is the universal shape; bench's per-task cost accounting reads
   from this consistently.
3. **Anthropic's `messages.create` is a rotated version of the same shape** —
   for providers that don't expose an OpenAI front-end (raw Anthropic SDK,
   Vertex AI), the implementation translates inside the provider class; the
   bench layer above stays clean.

Stdlib only; concrete providers (databricks, anthropic, …) soft-import their
SDKs and raise `ProviderNotInstalled` if missing.

Reference: references/provider-standards.md
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Literal


# ---------------------------------------------------------------------------
# Error types — each maps to a distinct CLI exit code in orchestrator.py.

class ProviderError(Exception):
    """Base for all provider-related failures."""


class ProviderNotConfigured(ProviderError):
    """Raised when required env vars / credentials are missing.

    Example: `DATABRICKS_TOKEN` not set for `DatabricksGatewayProvider`.
    """


class ProviderNotInstalled(ProviderError):
    """Raised when an optional SDK dependency is not installed.

    Example: `openai` package missing for `DatabricksGatewayProvider`.
    """


# ---------------------------------------------------------------------------
# OpenAI-compatible types.

Role = Literal["system", "user", "assistant"]


@dataclass(frozen=True)
class ChatMessage:
    """A single chat turn — mirrors OpenAI's request message shape."""

    role: Role
    content: str

    def to_dict(self) -> dict[str, str]:
        return {"role": self.role, "content": self.content}


@dataclass(frozen=True)
class Usage:
    """Token usage stats — mirrors OpenAI's `response.usage`.

    Providers MUST populate at minimum `prompt_tokens` + `completion_tokens`.
    `total_tokens` is computed as their sum if the upstream omits it.
    """

    prompt_tokens: int
    completion_tokens: int
    total_tokens: int

    def to_dict(self) -> dict[str, int]:
        return {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
        }


@dataclass(frozen=True)
class ChatCompletion:
    """The result of a single chat-completion call.

    Mirrors OpenAI's `response.choices[0].message.content` + `response.usage` +
    `response.model` + `response.choices[0].finish_reason`, flattened.

    `raw` carries the provider-native response object for callers that need
    access to fields the abstraction doesn't surface (logprobs, tool calls,
    structured-output fields, etc.). Bench code should NOT read `raw`; it
    exists for forward-compatibility.
    """

    content: str
    model: str
    usage: Usage
    finish_reason: str  # stop | length | content_filter | tool_calls | <provider-specific>
    raw: Any = field(default=None, repr=False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "content": self.content,
            "model": self.model,
            "usage": self.usage.to_dict(),
            "finish_reason": self.finish_reason,
        }


# ---------------------------------------------------------------------------
# Cost table — per-million-token pricing for cost estimation.
# These are list prices from each provider's public pricing page; treat as
# approximate. Providers may override or extend via `cost_per_million()`.

_COST_TABLE_USD_PER_MILLION: dict[str, tuple[float, float]] = {
    # (input_per_million, output_per_million)
    # Databricks Foundation Model APIs — pay-per-token pricing (Aug 2026):
    #   https://www.databricks.com/product/pricing/foundation-model-serving
    "databricks-claude-haiku-4-5": (1.00, 5.00),
    "databricks-claude-sonnet-4": (3.00, 15.00),
    "databricks-claude-opus-4-5": (15.00, 75.00),
    "databricks-meta-llama-4-maverick": (5.00, 15.00),
    # OpenAI list prices (for future OpenAI provider):
    "gpt-4o": (2.50, 10.00),
    "gpt-4o-mini": (0.15, 0.60),
    # Anthropic direct (for future AnthropicNativeProvider):
    "claude-haiku-4-5": (1.00, 5.00),
    "claude-sonnet-4-5": (3.00, 15.00),
    "claude-opus-4-5": (15.00, 75.00),
}


def cost_per_million(model: str) -> tuple[float, float] | None:
    """Return `(input_usd, output_usd)` per 1M tokens, or None if unknown.

    Callers should treat None as "cost not estimated" and surface that in
    reports — never default to 0.0 silently.
    """

    return _COST_TABLE_USD_PER_MILLION.get(model)


def estimate_cost_usd(model: str, usage: Usage) -> float | None:
    """Estimate USD cost for a single call. Returns None when model is unknown."""

    pricing = cost_per_million(model)
    if pricing is None:
        return None
    in_usd, out_usd = pricing
    return round(
        (usage.prompt_tokens / 1_000_000) * in_usd
        + (usage.completion_tokens / 1_000_000) * out_usd,
        6,
    )


# ---------------------------------------------------------------------------
# Provider ABC.

class Provider(ABC):
    """A provider is anything that satisfies the OpenAI Chat Completions contract.

    Three required methods:

      - `name`              short identifier (e.g. "databricks")
      - `configured()`      cheap check: are required env vars set?
      - `chat(...)`         the work — send messages, receive ChatCompletion
      - `list_models()`     known model names (best-effort, may be hardcoded)

    Implementations MUST:

      - Soft-import their SDK at module import time (catch `ImportError`).
      - Raise `ProviderNotInstalled` at instantiation if SDK is missing.
      - Raise `ProviderNotConfigured` at instantiation if creds are missing.
      - Map provider-native errors to `ProviderError` (or a subclass) on `chat()`.
      - Populate `ChatCompletion.usage` with at minimum `prompt_tokens` and
        `completion_tokens`. `total_tokens` defaults to the sum if absent.
    """

    name: str = "abstract"

    @abstractmethod
    def configured(self) -> bool:
        """Return True if required env vars / credentials are present.

        MUST be cheap (no network, no auth check). Use for fast gating of
        the live-vs-dry-run decision.
        """

    @abstractmethod
    def chat(
        self,
        messages: list[ChatMessage],
        model: str,
        max_tokens: int = 4096,
        temperature: float = 0.0,
        **kwargs: Any,
    ) -> ChatCompletion:
        """Send messages, receive a single completion. Synchronous."""

    @abstractmethod
    def list_models(self) -> list[str]:
        """Return known model names for this provider.

        Best-effort: a static list is acceptable; live discovery is optional.
        """
