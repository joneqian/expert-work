"""Tests for the ``ask_image`` tool — Stream J.6 Path B."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID, uuid4

import pytest
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage

from helix_agent.protocol.multimodal import ImageRef
from orchestrator.multimodal import IMAGE_REF_BLOCK_TYPE, InMemoryImageResolver, ResolvedImage
from orchestrator.tools.registry import ToolBlockedError, ToolContext
from orchestrator.tools.vision import AskImageTool

_TENANT = UUID("11111111-1111-1111-1111-111111111111")
_THREAD = UUID("22222222-2222-2222-2222-222222222222")


@dataclass
class _FakeVLCaller:
    """Records calls + returns a canned ``AIMessage``."""

    response: AIMessage = field(default_factory=lambda: AIMessage(content="ok"))
    calls: list[dict[str, Any]] = field(default_factory=list)

    async def __call__(self, *, messages: Sequence[BaseMessage], tools: Sequence[Any]) -> AIMessage:
        self.calls.append({"messages": list(messages), "tools": list(tools)})
        return self.response


def _ref(tenant: UUID = _TENANT, ext: str = ".png") -> str:
    return ImageRef(tenant_id=tenant, thread_id=_THREAD, image_id=uuid4(), ext=ext).to_uri()


def _resolver() -> InMemoryImageResolver:
    return InMemoryImageResolver(images={"any": ResolvedImage(media_type="image/png", data=b"PNG")})


def _ctx(tenant: UUID | None = _TENANT) -> ToolContext:
    return ToolContext(tenant_id=tenant)


@pytest.mark.asyncio
async def test_ask_image_happy_path() -> None:
    vl = _FakeVLCaller(response=AIMessage(content="a red apple on a desk"))
    tool = AskImageTool(vl_caller=vl, image_resolver=_resolver())
    ref = _ref()

    result = await tool.call({"image_ref": ref, "question": "what is this?"}, ctx=_ctx())

    assert result.content == "a red apple on a desk"
    assert result.meta == {"image_ref": ref}
    # The VL caller saw a system prompt + a human message with the image_ref block.
    sent = vl.calls[0]["messages"]
    assert isinstance(sent[0], SystemMessage)
    assert isinstance(sent[1], HumanMessage)
    content = sent[1].content
    assert isinstance(content, list)
    assert content[0] == {"type": "text", "text": "what is this?"}
    assert content[1] == {"type": IMAGE_REF_BLOCK_TYPE, "ref": ref}


@pytest.mark.asyncio
async def test_ask_image_meta_carries_vl_usage_when_present() -> None:
    # The separate VL round-trip's token usage rides in meta → ToolMessage
    # artifact, so the VL call's cost is observable (it's otherwise invisible —
    # the tool only returns text).
    usage = {"input_tokens": 800, "output_tokens": 40, "total_tokens": 840}
    vl = _FakeVLCaller(response=AIMessage(content="a desk", usage_metadata=usage))
    tool = AskImageTool(vl_caller=vl, image_resolver=_resolver())
    ref = _ref()

    result = await tool.call({"image_ref": ref, "question": "what is this?"}, ctx=_ctx())

    assert result.meta == {"image_ref": ref, "vl_usage": usage}


@pytest.mark.asyncio
async def test_ask_image_empty_text_response_falls_back() -> None:
    vl = _FakeVLCaller(response=AIMessage(content=""))
    tool = AskImageTool(vl_caller=vl, image_resolver=_resolver())
    result = await tool.call({"image_ref": _ref(), "question": "what is this?"}, ctx=_ctx())
    assert result.content == "[VL model returned no text]"


@pytest.mark.asyncio
async def test_ask_image_flattens_block_list_response() -> None:
    blocks = [{"type": "text", "text": "a "}, {"type": "text", "text": "cat"}]
    vl = _FakeVLCaller(response=AIMessage(content=blocks))
    tool = AskImageTool(vl_caller=vl, image_resolver=_resolver())
    result = await tool.call({"image_ref": _ref(), "question": "what is this?"}, ctx=_ctx())
    assert result.content == "a cat"


@pytest.mark.asyncio
async def test_ask_image_requires_tenant_in_ctx() -> None:
    tool = AskImageTool(vl_caller=_FakeVLCaller(), image_resolver=_resolver())
    with pytest.raises(ToolBlockedError, match="tenant"):
        await tool.call({"image_ref": _ref(), "question": "q"}, ctx=_ctx(tenant=None))


@pytest.mark.asyncio
async def test_ask_image_rejects_cross_tenant_ref() -> None:
    other_tenant = uuid4()
    tool = AskImageTool(vl_caller=_FakeVLCaller(), image_resolver=_resolver())
    with pytest.raises(ToolBlockedError, match="tenant"):
        await tool.call({"image_ref": _ref(tenant=other_tenant), "question": "q"}, ctx=_ctx())


@pytest.mark.asyncio
async def test_ask_image_rejects_empty_question() -> None:
    tool = AskImageTool(vl_caller=_FakeVLCaller(), image_resolver=_resolver())
    with pytest.raises(ValueError, match="'question'"):
        await tool.call({"image_ref": _ref(), "question": "   "}, ctx=_ctx())


@pytest.mark.asyncio
async def test_ask_image_rejects_missing_image_ref() -> None:
    tool = AskImageTool(vl_caller=_FakeVLCaller(), image_resolver=_resolver())
    with pytest.raises(ValueError, match="'image_ref'"):
        await tool.call({"question": "q"}, ctx=_ctx())


@pytest.mark.asyncio
async def test_ask_image_rejects_malformed_image_ref() -> None:
    tool = AskImageTool(vl_caller=_FakeVLCaller(), image_resolver=_resolver())
    with pytest.raises(ValueError, match="image ref"):
        await tool.call({"image_ref": "not-a-helix-ref", "question": "q"}, ctx=_ctx())


def test_ask_image_spec_shape() -> None:
    spec = AskImageTool(vl_caller=_FakeVLCaller(), image_resolver=_resolver()).spec
    assert spec.name == "ask_image"
    assert "image_ref" in spec.parameters["properties"]
    assert "question" in spec.parameters["properties"]
    assert spec.parameters["required"] == ["image_ref", "question"]
