"""Structured-output contract — Stream RT-1.

:class:`StructuredOutputSpec` is the per-call opt-in carried through
``LLMProvider.complete(output_schema=...)`` (STREAM-RT-DESIGN § 7.2): a
JSON Schema the LLM response must satisfy, plus a name for the wire
artifacts that carry it (OpenAI ``response_format.json_schema.name`` /
the forced Anthropic tool name). How a provider enforces the schema is
an adapter concern (RT-ADR-2 native / tool_call / prompt paths); the
spec itself is transport-neutral.

A frozen dataclass rather than a pydantic model (precedent:
:class:`~helix_agent.protocol.multimodal.ImageRef`): the spec never
crosses an HTTP boundary in RT-1 PR-1, and a pydantic field named
``schema`` would shadow the deprecated-but-present ``BaseModel.schema``
attribute, tripping pydantic's shadow warning.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class StructuredOutputSpec:
    """A JSON Schema the LLM response must validate against.

    ``schema`` is a JSON Schema dict (draft 2020-12). ``name`` labels
    the schema on the wire; keep it a short identifier — it becomes an
    OpenAI ``json_schema`` name / an Anthropic tool name. ``strict``
    maps to OpenAI's ``json_schema.strict`` flag; the tool_call and
    prompt paths always validate locally regardless (RT-ADR-1).

    ``fence_nonce`` (Stream RT-1 PR-3, design § 7.5) marks the schema
    as **tenant-origin** (the Tier3 ``AgentSpecBody.output_schema``):
    when set, the prompt path wraps the schema text — the only path
    where the schema enters the prompt rather than an API parameter —
    in an unguessable «UNTRUSTED nonce=…» fence so instructions embedded
    in the schema read as data (the PI-1 spotlight delimiting technique).
    ``None`` (internal, code-defined schemas) keeps the prompt-path
    instruction byte-identical to RT-1 PR-1. Excluded from the E.13
    cache-key fingerprint: the semantic call is (messages, schema); the
    fence is per-build wire shaping, like cache_control markers.
    """

    schema: dict[str, Any]
    name: str
    strict: bool = True
    fence_nonce: str | None = None
