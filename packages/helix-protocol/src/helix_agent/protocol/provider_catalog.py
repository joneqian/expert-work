"""Platform-wide LLM provider + external tool catalog — Stream O.

Single source of truth for "which providers / tools does the platform know about".
Platform deployment then opts in a subset via ``settings.supported_providers``
(Stream O Mini-ADR O-1); tenants in ``credentials_mode='tenant'`` (Stream O
Mini-ADR O-2) can only configure credentials for items in that opted-in subset.

Adding a new provider here is a deliberate platform decision — the type
literal forms part of the wire contract and the audit log; every new entry
needs an LLMProvider adapter in ``orchestrator.llm.providers``.
"""

from __future__ import annotations

from typing import Literal

__all__ = [
    "PROVIDER_CATALOG",
    "TOOL_CATALOG",
    "Provider",
    "Tool",
]


#: Full catalog of LLM providers the platform's adapter layer knows about.
#: Mirrors the ``provider`` Literal on :class:`ModelSpec`. A provider here
#: is only *enableable* — the deployment still has to pick it in
#: ``settings.supported_providers`` and supply a platform secret_ref.
Provider = Literal[
    "anthropic",
    "openai",
    "azure",
    "self-hosted",
    "kimi",
    "glm",
    "deepseek",
    "qwen",
    "doubao",
]


#: Full catalog of external SaaS tools that consume an API key. Internal
#: tools (filesystem / exec_python / etc.) go through the sandbox
#: permission model, not credentials — they're intentionally absent here.
#: MCP servers have their own multi-field config (Stream O PR 3 will
#: extend them under the same mode model).
Tool = Literal["web_search"]


#: Runtime tuple form of :data:`Provider`. Used by startup validation
#: (subset check vs ``settings.supported_providers``) and by tests.
PROVIDER_CATALOG: tuple[Provider, ...] = (
    "anthropic",
    "openai",
    "azure",
    "self-hosted",
    "kimi",
    "glm",
    "deepseek",
    "qwen",
    "doubao",
)


#: Runtime tuple form of :data:`Tool`. Same role as
#: :data:`PROVIDER_CATALOG`.
TOOL_CATALOG: tuple[Tool, ...] = ("web_search",)
