"""bench.providers.databricks — Databricks Model Serving Gateway provider.

Databricks Foundation Model APIs serve Anthropic Claude (haiku/sonnet/opus),
Meta Llama, and others via an **OpenAI-compatible** endpoint at:

    {DATABRICKS_HOST}/serving-endpoints

This module wraps the OpenAI Python SDK with that base URL so callers get
identical request/response semantics as direct OpenAI calls. The same
pattern Stimulus's `apps/api/src/utils/databricks_openai.py` ships with.

Credentials are read from environment variables (production canonical):

    DATABRICKS_HOST   workspace URL, e.g. https://adb-12345...azuredatabricks.net
    DATABRICKS_TOKEN  Databricks PAT (dapi*) or service principal token

`.env` files are NOT loaded here — that's the caller's responsibility.
Recommended invocation patterns:

    # Direct (when env vars are already exported)
    bstack bench run --runner live --provider databricks --model ...

    # Railway as credential broker (recommended for shared dev envs)
    railway run --service stimulus-api -- bstack bench run --runner live \\
        --provider databricks --model databricks-claude-haiku-4-5 ...

    # 1Password / sops / direnv / etc — any tool that exports env vars works.

Models available via Databricks Model Serving (as of v0.11.0 ship):

    databricks-claude-haiku-4-5      (small, fast, $1.00 / $5.00 per 1M)
    databricks-claude-sonnet-4       (medium, $3.00 / $15.00)
    databricks-claude-opus-4-5       (large, $15.00 / $75.00)
    databricks-meta-llama-4-maverick (Llama, $5.00 / $15.00)

`openai` package is a soft dependency — install with `pip install openai` to
enable this provider. CI doesn't install it; the `mock` provider is used
for offline tests.
"""

from __future__ import annotations

import os
from typing import Any

from bench.providers.base import (
    ChatCompletion,
    ChatMessage,
    Provider,
    ProviderError,
    ProviderNotConfigured,
    ProviderNotInstalled,
    Usage,
)


# Known Databricks-served models. Mirrors Stimulus's `DATABRICKS_CLAUDE_*`
# constants in apps/api/src/utils/databricks_openai.py.
KNOWN_MODELS: list[str] = [
    "databricks-claude-haiku-4-5",
    "databricks-claude-sonnet-4",
    "databricks-claude-opus-4-5",
    "databricks-meta-llama-4-maverick",
]


def _serving_endpoint_url(host: str) -> str:
    """Construct the OpenAI-compatible serving endpoint base URL.

    Matches Stimulus's `databricks_serving_endpoint_url()` in
    apps/api/src/utils/databricks_config.py — host can be bare hostname or
    full URL; trailing slash is stripped.
    """

    host = host.strip()
    if not host.startswith(("http://", "https://")):
        host = f"https://{host}"
    return f"{host.rstrip('/')}/serving-endpoints"


class DatabricksGatewayProvider(Provider):
    """OpenAI-compatible client targeting Databricks Model Serving."""

    name = "databricks"

    def __init__(
        self,
        host: str | None = None,
        token: str | None = None,
        *,
        timeout_seconds: float = 60.0,
    ) -> None:
        """Construct provider.

        Args:
            host: DATABRICKS_HOST override. Defaults to env var.
            token: DATABRICKS_TOKEN override. Defaults to env var.
            timeout_seconds: per-request timeout.

        Raises:
            ProviderNotInstalled: if `openai` package is not importable.
            ProviderNotConfigured: if host or token missing.
        """

        self._host = host or os.environ.get("DATABRICKS_HOST", "").strip() or None
        self._token = token or os.environ.get("DATABRICKS_TOKEN", "").strip() or None
        if not self._host:
            raise ProviderNotConfigured(
                "DATABRICKS_HOST not set. Export DATABRICKS_HOST=https://<workspace>... "
                "or pass via `railway run --service <service> -- bstack bench ...`."
            )
        if not self._token:
            raise ProviderNotConfigured(
                "DATABRICKS_TOKEN not set. Export DATABRICKS_TOKEN=dapi... "
                "(create via Databricks UI: User Settings → Developer → Access tokens)."
            )
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise ProviderNotInstalled(
                "openai SDK not installed. Install with `pip install openai` "
                "(or `pip install --break-system-packages openai` on macOS Homebrew). "
                "This is the only third-party dependency the live bench provider needs."
            ) from exc
        self._client = OpenAI(
            api_key=self._token,
            base_url=_serving_endpoint_url(self._host),
            timeout=timeout_seconds,
        )

    def configured(self) -> bool:
        return bool(self._host and self._token)

    def list_models(self) -> list[str]:
        # Static list. Live discovery via /api/2.0/serving-endpoints is
        # available but adds latency + a second API call; defer until needed.
        return list(KNOWN_MODELS)

    def chat(
        self,
        messages: list[ChatMessage],
        model: str,
        max_tokens: int = 4096,
        temperature: float = 0.0,
        **kwargs: Any,
    ) -> ChatCompletion:
        """Send messages to Databricks, return normalized completion.

        Raises:
            ProviderError: wrapping any upstream openai-SDK exception. The
                original is available via `__cause__`.
        """

        if not messages:
            raise ProviderError("chat(): messages list is empty")
        try:
            response = self._client.chat.completions.create(
                model=model,
                messages=[m.to_dict() for m in messages],
                max_tokens=max_tokens,
                temperature=temperature,
                **kwargs,
            )
        except Exception as exc:
            # openai SDK exposes APIError, RateLimitError, AuthenticationError,
            # etc. We unify those into ProviderError so the orchestrator can
            # handle them uniformly. Original is preserved via __cause__.
            raise ProviderError(
                f"Databricks chat completion failed for model={model!r}: "
                f"{type(exc).__name__}: {exc}"
            ) from exc
        if not response.choices:
            raise ProviderError(
                f"Databricks returned empty choices for model={model!r}"
            )
        choice = response.choices[0]
        usage = response.usage
        prompt_tokens = getattr(usage, "prompt_tokens", 0) or 0
        completion_tokens = getattr(usage, "completion_tokens", 0) or 0
        total_tokens = getattr(usage, "total_tokens", None)
        if total_tokens is None:
            total_tokens = prompt_tokens + completion_tokens
        return ChatCompletion(
            content=choice.message.content or "",
            # Use the response.model (Databricks returns a richer model ID
            # like "global.anthropic.claude-haiku-4-5-20251001-v1:0") so
            # callers can record the resolved model, not just the alias.
            model=response.model or model,
            usage=Usage(
                prompt_tokens=int(prompt_tokens),
                completion_tokens=int(completion_tokens),
                total_tokens=int(total_tokens),
            ),
            finish_reason=choice.finish_reason or "unknown",
            raw=response,
        )
