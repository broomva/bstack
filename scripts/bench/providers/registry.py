"""bench.providers.registry — Provider factory + plugin point.

New providers register themselves via `register_provider("name", ProviderClass)`
or via the static `_BUILTIN_PROVIDERS` dict below. Callers ask the registry
for a provider by name; the registry instantiates with optional kwargs.

Lazy instantiation: providers are only constructed when asked for (so a user
who never touches `--provider databricks` never imports `openai`).
"""

from __future__ import annotations

from typing import Any

from bench.providers.base import (
    ChatCompletion,
    ChatMessage,
    Provider,
    ProviderError,
    Usage,
)


# ---------------------------------------------------------------------------
# MockProvider — for tests + CI (no network, deterministic).

class MockProvider(Provider):
    """Deterministic in-process provider for tests.

    Echoes the user's last message back with a canned prefix, fakes token
    usage. Useful for verifying the abstraction layer end-to-end without
    needing a network or credentials.
    """

    name = "mock"

    def __init__(
        self,
        *,
        canned_response: str | None = None,
        prompt_tokens: int = 12,
        completion_tokens: int = 8,
    ) -> None:
        self._canned = canned_response
        self._pt = prompt_tokens
        self._ct = completion_tokens

    def configured(self) -> bool:
        return True

    def list_models(self) -> list[str]:
        return ["mock-small", "mock-large"]

    def chat(
        self,
        messages: list[ChatMessage],
        model: str,
        max_tokens: int = 4096,
        temperature: float = 0.0,
        **kwargs: Any,
    ) -> ChatCompletion:
        if not messages:
            raise ProviderError("chat(): messages list is empty")
        if self._canned is not None:
            content = self._canned
        else:
            last = messages[-1].content
            content = f"[mock:{model}] {last[:200]}"
        return ChatCompletion(
            content=content,
            model=model,
            usage=Usage(
                prompt_tokens=self._pt,
                completion_tokens=self._ct,
                total_tokens=self._pt + self._ct,
            ),
            finish_reason="stop",
            raw=None,
        )


# ---------------------------------------------------------------------------
# Registry.

# Built-in providers. Concrete imports are deferred so that importing the
# registry doesn't pull in optional SDK deps for providers the caller never
# uses.
_BUILTIN_PROVIDERS: dict[str, str] = {
    "databricks": "bench.providers.databricks:DatabricksGatewayProvider",
    "mock": "bench.providers.registry:MockProvider",
}

# Runtime-registered providers (filled by register_provider).
_RUNTIME_PROVIDERS: dict[str, type[Provider]] = {}


def list_providers() -> list[str]:
    """Return sorted list of registered provider names."""

    return sorted(set(_BUILTIN_PROVIDERS) | set(_RUNTIME_PROVIDERS))


def register_provider(name: str, cls: type[Provider]) -> None:
    """Register an additional provider at runtime.

    Useful for tests and downstream consumers. Overrides built-in with the
    same name. NOT thread-safe; call at startup, not from request handlers.
    """

    if not issubclass(cls, Provider):
        raise TypeError(f"register_provider: {cls!r} is not a Provider subclass")
    _RUNTIME_PROVIDERS[name] = cls


def get_provider(name: str, **kwargs: Any) -> Provider:
    """Instantiate a provider by name.

    Args:
        name: provider name (e.g. "databricks", "mock", or any name passed to
            `register_provider`).
        **kwargs: passed through to the provider constructor.

    Raises:
        KeyError: if name is unknown.
        ProviderNotInstalled / ProviderNotConfigured: bubble up from provider
        constructor when SDK or env is missing.
    """

    # Runtime-registered providers take precedence (overrides for tests).
    cls = _RUNTIME_PROVIDERS.get(name)
    if cls is None:
        spec = _BUILTIN_PROVIDERS.get(name)
        if spec is None:
            available = ", ".join(list_providers())
            raise KeyError(
                f"Unknown provider {name!r}. Available: {available}."
            )
        # Lazy import: "module:ClassName" spec.
        module_path, _, class_name = spec.partition(":")
        import importlib

        module = importlib.import_module(module_path)
        cls = getattr(module, class_name)
    return cls(**kwargs)
