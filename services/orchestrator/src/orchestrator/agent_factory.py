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
- **Tools — assembled.** The manifest's ``tools`` field is a
  ``type``-discriminated union (Mini-ADR E-14); :func:`build_tool_registry`
  maps each entry to a concrete adapter. Platform runtime deps (Tavily
  client / allowlist provider / MCP pool) are injected via
  :class:`~orchestrator.tools.ToolEnv` — the default empty ``ToolEnv``
  still builds a pure-LLM agent; a declared tool whose dep is missing
  raises :class:`AgentFactoryError`.
- **Middleware chains — assembled.** :func:`build_middleware_chains`
  (Mini-ADR E-15) wires the three always-on middlewares plus any
  env-gated ones (PII / cache / Langfuse) into the graph's anchor
  chains and the router's ``around_llm_call`` chain.

``ModelSpec.temperature`` is plumbed through ``_build_provider`` into
each adapter and onto the request body.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph.state import CompiledStateGraph

from helix_agent.protocol import AgentSpec, ModelSpec
from helix_agent.runtime.middleware import MiddlewareChain
from helix_agent.runtime.secret_store import SecretStore, parse_secret_ref
from orchestrator.errors import AgentFactoryError
from orchestrator.graph_builder import build_react_graph, make_planner_node
from orchestrator.llm import (
    AnthropicProvider,
    HTTPAnthropicClient,
    HTTPOpenAIClient,
    LLMProvider,
    LLMRouter,
    OpenAIProvider,
    ProviderHandle,
    RateLimitedProvider,
    make_azure_client,
    make_deepseek_client,
    make_doubao_client,
    make_glm_client,
    make_kimi_client,
    make_qwen_client,
    make_self_hosted_client,
)
from orchestrator.middleware_assembly import MiddlewareEnv, build_middleware_chains
from orchestrator.runner import GraphRunner
from orchestrator.tools import ToolEnv, build_tool_registry


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
    tool_env: ToolEnv | None = None,
    middleware_env: MiddlewareEnv | None = None,
) -> BuiltAgent:
    """Assemble a :class:`BuiltAgent` from a validated :class:`AgentSpec`.

    ``tool_env`` injects the platform runtime deps the manifest's
    ``tools:`` entries need (Tavily client / allowlist provider / MCP
    pool). It defaults to an empty :class:`ToolEnv` — fine for a
    pure-LLM agent; an agent that declares a tool whose dep is absent
    raises :class:`AgentFactoryError`.

    ``middleware_env`` injects the deps the env-gated middleware need
    (redactor / cache / Langfuse client — Mini-ADR E-15). An empty
    :class:`MiddlewareEnv` still wires the three always-on middlewares
    (dynamic context / circuit breaker / loop detection).

    Raises :class:`AgentFactoryError` for an un-buildable manifest
    (missing ``api_key_ref``, an unsupported provider, an
    un-assemblable ``tools:`` entry, …).
    """
    chains = build_middleware_chains(spec, env=middleware_env)
    router = await build_llm_router(
        spec.spec.model,
        secret_store=secret_store,
        around_llm_chain=chains.around_llm_call,
    )
    registry = await build_tool_registry(spec.spec.tools, tool_env=tool_env or ToolEnv())
    # Stream J.1 — a ``plan_execute`` manifest front-loads a planner node
    # that decomposes the task before the ReAct loop runs.
    planner_node = make_planner_node(router) if spec.spec.workflow.type == "plan_execute" else None
    graph = build_react_graph(
        llm_caller=router,
        tool_registry=registry,
        planner_node=planner_node,
        before_llm_chain=chains.before_llm_call,
        after_llm_chain=chains.after_llm_call,
        before_tool_dispatch_chain=chains.before_tool_dispatch,
    )
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
    around_llm_chain: MiddlewareChain | None = None,
) -> LLMRouter:
    """Build an :class:`LLMRouter` from a ``ModelSpec`` + its fallback tree.

    The tree is flattened pre-order — primary first, then each fallback
    (and its own fallbacks) in declaration order — into the router's
    ordered provider chain. Each model's ``api_key_ref`` is resolved
    through ``secret_store``; the provider adapter is wrapped in E.12's
    :class:`RateLimitedProvider` at the model's ``rate_limit_rpm``.

    ``around_llm_chain`` is the ``around_llm_call`` anchor chain — the
    router wraps each provider attempt with it (Mini-ADR E-13).
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
    return LLMRouter(providers=handles, around_llm_chain=around_llm_chain)


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
    client (E.11.5). ``azure`` and ``self-hosted`` reuse it too — both
    speak the OpenAI wire format and only differ at the HTTP layer
    (Mini-ADR E-16).
    """
    # Widen to ``str`` so the exhaustive Literal still leaves the
    # trailing "unsupported" raise reachable to mypy.
    provider: str = model.provider
    if provider == "anthropic":
        return AnthropicProvider(
            client=HTTPAnthropicClient(api_key=api_key),
            model=model.name,
            max_tokens=model.max_tokens,
            temperature=model.temperature,
        )
    if provider == "openai":
        return OpenAIProvider(
            client=HTTPOpenAIClient(api_key=api_key),
            model=model.name,
            temperature=model.temperature,
        )

    openai_compatible = {
        "kimi": make_kimi_client,
        "glm": make_glm_client,
        "deepseek": make_deepseek_client,
        "qwen": make_qwen_client,
        "doubao": make_doubao_client,
    }
    make_client = openai_compatible.get(provider)
    if make_client is not None:
        return OpenAIProvider(
            client=make_client(api_key=api_key),
            model=model.name,
            temperature=model.temperature,
        )

    if provider == "self-hosted":
        if not model.base_url:
            raise AgentFactoryError(f"self-hosted model {model.name!r} requires a base_url")
        return OpenAIProvider(
            client=make_self_hosted_client(api_key, base_url=model.base_url),
            model=model.name,
            temperature=model.temperature,
        )

    if provider == "azure":
        if not (model.base_url and model.azure_deployment and model.azure_api_version):
            raise AgentFactoryError(
                f"azure model {model.name!r} requires base_url + "
                f"azure_deployment + azure_api_version"
            )
        return OpenAIProvider(
            client=make_azure_client(
                api_key,
                endpoint=model.base_url,
                deployment=model.azure_deployment,
                api_version=model.azure_api_version,
            ),
            model=model.name,
            temperature=model.temperature,
        )

    raise AgentFactoryError(f"provider {provider!r} has no adapter")
