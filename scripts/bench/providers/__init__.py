"""bench.providers — Industry-standard LLM provider abstraction.

The de facto LLM contract in 2026 is the OpenAI Chat Completions API.
Databricks Model Serving, OpenAI, Anthropic-via-Bedrock, Together,
Fireworks, Anyscale, vLLM, llama.cpp all expose OpenAI-compatible
endpoints. The bstack abstraction is designed around that contract;
new providers plug in by implementing `Provider.chat()`.

Public surface:

  from bench.providers import Provider, ChatMessage, ChatCompletion, Usage
  from bench.providers import get_provider, list_providers
  from bench.providers import ProviderNotConfigured, ProviderNotInstalled

Concrete providers shipping in v0.11.0:

  - "databricks"   Databricks Model Serving (Anthropic Claude via gateway,
                   OpenAI-compatible). Requires DATABRICKS_HOST + DATABRICKS_TOKEN.
  - "mock"         In-process deterministic provider for tests.

Reference: references/provider-standards.md
"""

from __future__ import annotations

from bench.providers.base import (  # noqa: F401
    ChatCompletion,
    ChatMessage,
    Provider,
    ProviderError,
    ProviderNotConfigured,
    ProviderNotInstalled,
    Usage,
)
from bench.providers.registry import (  # noqa: F401
    get_provider,
    list_providers,
    register_provider,
)

__all__ = [
    "ChatCompletion",
    "ChatMessage",
    "Provider",
    "ProviderError",
    "ProviderNotConfigured",
    "ProviderNotInstalled",
    "Usage",
    "get_provider",
    "list_providers",
    "register_provider",
]
