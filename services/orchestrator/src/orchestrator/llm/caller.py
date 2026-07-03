"""LLM-caller protocol — Stream E.6.

The agent node delegates the actual LLM call through this protocol so
the ReAct graph stays decoupled from any specific provider SDK. E.11
:class:`orchestrator.llm.router.LLMRouter` is the production
implementation; tests inject a deterministic fake that returns a
scripted sequence of ``AIMessage`` values.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from langchain_core.messages import AIMessage, BaseMessage

from helix_agent.protocol import StructuredOutputSpec
from orchestrator.tools.registry import ToolSpec


@runtime_checkable
class LLMCaller(Protocol):
    """Async callable that takes the message history + available tool
    specs and returns the LLM's next response.

    The response's ``tool_calls`` (if any) drive the ReAct conditional
    edge — non-empty → branch to the tools node, empty → end.
    """

    async def __call__(
        self,
        *,
        messages: Sequence[BaseMessage],
        tools: Sequence[ToolSpec],
        output_schema: StructuredOutputSpec | None = None,
    ) -> AIMessage:
        """Call the LLM with the current message history and tool catalogue;
        return the next ``AIMessage`` (with ``tool_calls`` set if the model
        wants tools, otherwise text-only).

        ``output_schema`` (Stream RT-1) asks for a schema-enforced JSON
        response; on success the validated dict rides on
        ``additional_kwargs["parsed"]``. ``None`` (the default) is the
        plain unstructured call — implementations without structured
        support simply ignore the parameter."""
