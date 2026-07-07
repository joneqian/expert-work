"""PII redaction middleware — Stream E.5 / D.2 cross-stream anchor registration.

Walks ``ctx.payload["messages"]`` before the LLM call and rewrites each
message's string ``content`` through a ``redact_text`` callable. The
callable is injected at orchestrator startup so that the same
:class:`TenantAwareRedactor` instance that protects the audit-write
path (D.2) also covers prompts on the way to the LLM — sharing the
configuration without making the middleware itself depend on D.2
internals.

Registered on the ``before_llm_call`` anchor with
``after=("dynamic_context",)`` so the trimmed message view (E.3) is
already in place when we redact — we don't waste cycles redacting
turns that will be dropped.

Per-tenant PII rules from ``TenantConfigService.pii_fields`` apply to
dict-shaped audit details (D.2); they don't directly map to free-text
LLM messages, so this middleware only relies on the global regex
patterns inside the redactor. The callable returns a redacted string
unchanged on no-match; we preserve message identity in that case to
keep prefix caching stable.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import cast
from uuid import UUID

from langchain_core.messages import BaseMessage

from expert_work.runtime.middleware.base import CallNext, MiddlewareContext

logger = logging.getLogger(__name__)


#: Signature of the text-level redactor the orchestrator hands in.
#:
#: ``tenant_id`` may be ``None`` in dev / pre-tenant-binding tests; the
#: callable should fall back to the global pattern set in that case.
RedactText = Callable[[str, UUID | None], str]


def _noop_redact_text(text: str, _tenant_id: UUID | None) -> str:
    """Default callable — returns input unchanged.

    Lets the middleware be constructed in unit tests without wiring a
    real redactor; orchestrator startup overrides with a closure over
    :class:`TenantAwareRedactor`.
    """
    return text


@dataclass
class PIIRedactorMiddleware:
    """Redact secret / PII patterns in LLM messages before the model sees them.

    Iterates over ``ctx.payload["messages"]`` (the trimmed view from E.3)
    and replaces each :class:`BaseMessage` whose ``content`` changed
    after redaction with a Pydantic ``model_copy(update=...)`` — message
    identity is preserved when nothing matched, so prefix cache stays
    valid for the unchanged tail.

    Messages with non-string ``content`` (e.g., multimodal content
    blocks) pass through unchanged in M0; multimodal redaction lands
    with M2 / M3 multimodal support.
    """

    redact_text: RedactText = field(default=_noop_redact_text)

    name: str = "pii_redact"
    anchor: str = "before_llm_call"
    #: Run after dynamic_context (E.3) so we redact the final trimmed
    #: view, not turns that will be dropped.
    after: tuple[str, ...] = field(default_factory=lambda: ("dynamic_context",))
    before: tuple[str, ...] = field(default_factory=tuple)

    async def __call__(self, ctx: MiddlewareContext, call_next: CallNext) -> None:
        raw = ctx.payload.get("messages")
        if not raw:
            await call_next(ctx)
            return

        tenant_id = self._coerce_tenant_id(ctx.payload.get("tenant_id"))
        messages = cast(list[BaseMessage], raw)
        redacted: list[BaseMessage] = []
        changed = False
        for msg in messages:
            new_msg = self._redact_message(msg, tenant_id)
            if new_msg is not msg:
                changed = True
            redacted.append(new_msg)

        if changed:
            ctx.payload["messages"] = redacted
        await call_next(ctx)

    # ------------------------------------------------------------------

    def _redact_message(self, msg: BaseMessage, tenant_id: UUID | None) -> BaseMessage:
        content = msg.content
        if not isinstance(content, str):
            # Multimodal content (list of blocks) — M2/M3 scope.
            return msg
        try:
            new_content = self.redact_text(content, tenant_id)
        except Exception:
            logger.warning("pii_redact.text_redactor_failed", exc_info=True)
            return msg
        if new_content == content:
            return msg
        return msg.model_copy(update={"content": new_content})

    @staticmethod
    def _coerce_tenant_id(raw: object) -> UUID | None:
        if isinstance(raw, UUID):
            return raw
        if isinstance(raw, str):
            try:
                return UUID(raw)
            except ValueError:
                logger.debug("pii_redact.invalid_tenant_id_string")
                return None
        return None
