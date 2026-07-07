"""Reusable testing helpers — importable from any test module.

These were previously declared inside ``tests/conftest.py``; moving them to a
real package lets package-level tests (e.g. ``packages/expert-work-persistence/tests/``)
type-hint and import them without sys.path gymnastics.

Pytest fixtures themselves still live in the root ``conftest.py``.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class FakeCompletion:
    """Pretend LLM response. Add fields as production LLM contract grows."""

    content: str
    tokens_used: int = 0
    cached: bool = False


@dataclass
class MockLLM:
    """Deterministic LLM stub.

    Default behavior: any prompt returns ``FakeCompletion(content="ok")``.
    Configure overrides via ``.expect(prompt_prefix, response)``.
    All prompts are recorded in ``.calls`` for assertion.
    """

    default: FakeCompletion = field(default_factory=lambda: FakeCompletion(content="ok"))
    expectations: list[tuple[str, FakeCompletion]] = field(default_factory=list)
    calls: list[str] = field(default_factory=list)

    def expect(self, prompt_prefix: str, response: FakeCompletion) -> None:
        """Register a response for prompts starting with ``prompt_prefix``."""
        self.expectations.append((prompt_prefix, response))

    async def complete(self, prompt: str) -> FakeCompletion:
        """Return the first matching expectation, or the default."""
        self.calls.append(prompt)
        for prefix, response in self.expectations:
            if prompt.startswith(prefix):
                return response
        return self.default


@dataclass
class InMemorySecretStore:
    """Dict-backed SecretStore for tests.

    When ``expert_work.runtime.secrets.SecretStore`` Protocol lands in
    Stream A.x (per ADR-0007), this class will explicitly implement it.
    """

    _store: dict[str, str] = field(default_factory=dict)

    async def get(self, name: str, *, version: str | None = None) -> str:
        del version  # not modelled in in-memory store
        if name not in self._store:
            raise KeyError(f"secret not found: {name}")
        return self._store[name]

    async def put(self, name: str, value: str) -> None:
        self._store[name] = value


__all__ = ["FakeCompletion", "InMemorySecretStore", "MockLLM"]
