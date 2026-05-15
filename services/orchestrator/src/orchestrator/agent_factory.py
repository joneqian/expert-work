"""Agent factory — assemble a runnable agent from an :class:`AgentSpec`.

Closes the Stream E loop: turns a manifest into something the E.14
``run_agent`` worker can stream. The keystone it depends on is F.6's
:class:`SecretStore` — provider API keys live behind ``secret://``
references, never in the manifest.

M0 v1 scope:

- **LLM routing — real.** :func:`build_llm_router` walks the
  ``ModelSpec`` fallback tree, resolves each ``api_key_ref`` through the
  SecretStore, builds the matching provider adapter, wraps it in E.12's
  rate limiter, and assembles an :class:`LLMRouter`.
- **Tools — empty.** The manifest's ``tools`` field is still untyped
  ``list[dict]`` (no tool-spec schema / dispatcher yet); the factory
  builds an empty :class:`ToolRegistry`. A pure-LLM agent runs;
  tool-using agents wait for the tool-spec follow-up.
- **Middleware chains — not wired.** ``build_react_graph`` is called
  without chains. Wiring E.3/E.4/E.5/E.10/E.10.5/E.13 from the
  manifest's ``policies`` / ``dynamic_context`` blocks is a follow-up.

Known M0 limitation: ``ModelSpec.temperature`` is not plumbed into the
provider request body — the E.11 provider adapters are minimal and do
not send sampling params. Tracked for an E.11 hardening pass.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph.state import CompiledStateGraph

from helix_agent.protocol import AgentSpec, ModelSpec
from helix_agent.runtime.secret_store import SecretStore, parse_secret_ref
from orchestrator.errors import OrchestratorError
from orchestrator.graph_builder import build_react_graph
from orchestrator.llm import (
    AnthropicProvider,
    HTTPAnthropicClient,
    HTTPOpenAIClient,
    LLMProvider,
    LLMRouter,
    OpenAIProvider,
    ProviderHandle,
    RateLimitedProvider,
    make_deepseek_client,
    make_doubao_client,
    make_glm_client,
    make_kimi_client,
    make_qwen_client,
)
from orchestrator.runner import GraphRunner
from orchestrator.tools import ToolRegistry


class AgentFactoryError(OrchestratorError):
    """Raised when an :class:`AgentSpec` cannot be assembled into an agent."""


@dataclass(frozen=True)
class BuiltAgent:
    """The runnable artefacts the worker / control-plane needs.

    ``graph`` is invoked via ``astream``; ``system_prompt`` and
    ``max_steps`` seed the initial ``AgentState`` (the factory builds
    the graph, the caller builds each run's input).
    """

    graph: CompiledStateGraph[Any, Any, Any, Any]
    system_prompt: str
    max_steps: int


async def build_agent(
    spec: AgentSpec,
    *,
    secret_store: SecretStore,
    checkpointer: BaseCheckpointSaver[Any],
) -> BuiltAgent:
    """Assemble a :class:`BuiltAgent` from a validated :class:`AgentSpec`.

    Raises :class:`AgentFactoryError` for an un-buildable manifest
    (missing ``api_key_ref``, an unsupported provider, …).
    """
    router = await build_llm_router(spec.spec.model, secret_store=secret_store)
    # M0 v1: no tools, no middleware chains — see module docstring.
    registry = ToolRegistry()
    graph = build_react_graph(llm_caller=router, tool_registry=registry)
    compiled = GraphRunner(checkpointer=checkpointer).compile(graph)
    return BuiltAgent(
        graph=compiled,
        system_prompt=spec.spec.system_prompt.template,
        max_steps=spec.spec.workflow.max_iterations,
    )


async def build_llm_router(
    model: ModelSpec,
    *,
    secret_store: SecretStore,
) -> LLMRouter:
    """Build an :class:`LLMRouter` from a ``ModelSpec`` + its fallback tree.

    The tree is flattened pre-order — primary first, then each fallback
    (and its own fallbacks) in declaration order — into the router's
    ordered provider chain. Each model's ``api_key_ref`` is resolved
    through ``secret_store``; the provider adapter is wrapped in E.12's
    :class:`RateLimitedProvider` at the model's ``rate_limit_rpm``.
    """
    handles: list[ProviderHandle] = []
    for entry in _flatten_chain(model):
        if entry.api_key_ref is None:
            raise AgentFactoryError(
                f"model {entry.provider}:{entry.name} has no api_key_ref — "
                f"cannot resolve a provider API key"
            )
        api_key = await secret_store.get(parse_secret_ref(entry.api_key_ref))
        provider = _build_provider(entry, api_key)
        rate_limited = RateLimitedProvider.with_rpm(provider, rate_limit_rpm=entry.rate_limit_rpm)
        handles.append(ProviderHandle(provider=rate_limited, key=f"{entry.provider}:{entry.name}"))
    return LLMRouter(providers=handles)


def _flatten_chain(model: ModelSpec) -> list[ModelSpec]:
    """Pre-order flatten of the fallback tree (primary first).

    The :class:`AgentSpec` validator already rejects cycles, so a plain
    recursive walk terminates.
    """
    flat: list[ModelSpec] = []

    def _walk(node: ModelSpec) -> None:
        flat.append(node)
        for child in node.fallback:
            _walk(child)

    _walk(model)
    return flat


def _build_provider(model: ModelSpec, api_key: str) -> LLMProvider:
    """Map a ``ModelSpec`` to a concrete :class:`LLMProvider` adapter.

    OpenAI-compatible regional vendors (kimi / glm / deepseek / qwen /
    doubao) reuse :class:`OpenAIProvider` over a vendor-configured HTTP
    client (E.11.5). ``azure`` / ``self-hosted`` have no adapter yet.
    """
    provider = model.provider
    if provider == "anthropic":
        return AnthropicProvider(
            client=HTTPAnthropicClient(api_key=api_key),
            model=model.name,
            max_tokens=model.max_tokens,
        )
    if provider == "openai":
        return OpenAIProvider(client=HTTPOpenAIClient(api_key=api_key), model=model.name)

    openai_compatible = {
        "kimi": make_kimi_client,
        "glm": make_glm_client,
        "deepseek": make_deepseek_client,
        "qwen": make_qwen_client,
        "doubao": make_doubao_client,
    }
    make_client = openai_compatible.get(provider)
    if make_client is not None:
        return OpenAIProvider(client=make_client(api_key=api_key), model=model.name)

    raise AgentFactoryError(
        f"provider {provider!r} has no adapter yet "
        f"(supported: anthropic, openai, {', '.join(openai_compatible)})"
    )
