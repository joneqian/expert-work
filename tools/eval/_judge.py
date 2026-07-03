"""LLM-judge provider for J.13a — Stream J.13a (Mini-ADR J-39).

Two implementations of :class:`JudgeProvider`:

* :class:`ScriptedJudge` — deterministic mock for CI. Looks up scripted
  scores by ``case_id``; returns ``default_score`` for cases not in the
  map. Calling this judge is free and does not hit the network.
* :class:`AnthropicHaikuJudge` — real Haiku 4.5 judge, used by the
  weekly baseline 周跑 (Mini-ADR J-39 fixed model =
  ``claude-haiku-4-5-20251001``, ``temperature=0.0``). One Anthropic
  Messages API call per ``score()``; the response is parsed for a
  single integer 1-5.

:func:`make_judge_from_env` picks the implementation based on
``ANTHROPIC_API_KEY``. CI runs with no key → ``ScriptedJudge``; the
weekly baseline job exports the key → ``AnthropicHaikuJudge``.

Failure modes are conservative — a malformed Anthropic reply or a 5xx
returns ``0``, which trips the threshold loud rather than silently
inflating the baseline.
"""

from __future__ import annotations

import logging
import os
import re
from collections.abc import Mapping
from typing import Protocol

import httpx
from pydantic import BaseModel, ValidationError

logger = logging.getLogger(__name__)

JUDGE_MODEL = "claude-haiku-4-5-20251001"
JUDGE_TEMPERATURE = 0.0
_JUDGE_MAX_TOKENS = 64
_ANTHROPIC_API = "https://api.anthropic.com/v1/messages"
_ANTHROPIC_VERSION = "2023-06-01"


class JudgeProvider(Protocol):
    """Returns an integer score in ``[1, 5]`` for one case."""

    async def score(self, *, case_id: str, prompt: str) -> int:
        """Score one case; integer in [1, 5]."""


class ScriptedJudge:
    """Deterministic mock — looks up ``case_id`` in a YAML-loaded map.

    Both ``score()`` calls and the resulting baseline numbers are
    fully reproducible across runs and across machines.
    """

    def __init__(self, scores_by_case_id: Mapping[str, int], *, default_score: int = 4) -> None:
        self._scores = dict(scores_by_case_id)
        self._default = default_score

    async def score(self, *, case_id: str, prompt: str) -> int:
        return self._scores.get(case_id, self._default)


class AnthropicHaikuJudge:
    """Real Haiku 4.5 judge — Mini-ADR J-39 weekly baseline path.

    One Anthropic Messages API call per case. The judge prompt asks for
    a single digit in ``[1, 5]``; any malformed reply is treated as a
    score of ``0`` so a misconfigured judge surfaces as a hard failure
    (the threshold ``≥ 4.0`` clips below the failure score).
    """

    def __init__(
        self,
        *,
        api_key: str,
        http_client: httpx.AsyncClient | None = None,
        model: str = JUDGE_MODEL,
    ) -> None:
        self._api_key = api_key
        self._http = http_client
        self._model = model

    async def score(self, *, case_id: str, prompt: str) -> int:
        client = self._http or httpx.AsyncClient(timeout=30.0)
        try:
            response = await client.post(
                _ANTHROPIC_API,
                headers={
                    "x-api-key": self._api_key,
                    "anthropic-version": _ANTHROPIC_VERSION,
                    "content-type": "application/json",
                },
                json={
                    "model": self._model,
                    "max_tokens": _JUDGE_MAX_TOKENS,
                    "temperature": JUDGE_TEMPERATURE,
                    "messages": [{"role": "user", "content": prompt}],
                },
            )
        except httpx.HTTPError as exc:
            logger.warning("judge.network_error case=%s err=%s", case_id, type(exc).__name__)
            return 0
        finally:
            if self._http is None:
                await client.aclose()

        if response.status_code != 200:
            logger.warning("judge.http_status case=%s status=%d", case_id, response.status_code)
            return 0

        try:
            text = _AnthropicMessagesReply.model_validate_json(response.text).first_text()
        except (ValidationError, ValueError) as exc:
            logger.warning("judge.parse_error case=%s err=%s", case_id, type(exc).__name__)
            return 0
        return _parse_score(text)


class _AnthropicContentBlock(BaseModel):
    """One content block of an Anthropic Messages response."""

    type: str
    text: str | None = None


class _AnthropicMessagesReply(BaseModel):
    """The slice of an Anthropic Messages response the judge reads.

    RT-1 PR-2 — pydantic envelope validation replacing the manual
    dict-walk; the failure rule is unchanged: anything that is not a
    response whose FIRST block is a text block scores ``0``.
    """

    content: list[_AnthropicContentBlock]

    def first_text(self) -> str:
        if not self.content:
            msg = "anthropic content is not a non-empty list"
            raise ValueError(msg)
        block = self.content[0]
        if block.type != "text" or block.text is None:
            msg = "first block is not a text block"
            raise ValueError(msg)
        return block.text


_DIGIT_RE = re.compile(r"[1-5]")


def _parse_score(text: str) -> int:
    """Find the first ``[1-5]`` digit in the judge's reply.

    The judge prompt asks for a bare digit; tolerating prose around it
    keeps a one-off chatty completion from collapsing the baseline.
    """
    match = _DIGIT_RE.search(text)
    if match is None:
        return 0
    return int(match.group(0))


def make_judge_from_env(
    scripted_scores: Mapping[str, int] | None = None,
    *,
    default_score: int = 4,
) -> JudgeProvider:
    """Pick a judge based on ``ANTHROPIC_API_KEY``.

    With the key set, the weekly path returns :class:`AnthropicHaikuJudge`.
    Without it, CI gets :class:`ScriptedJudge` with the supplied per-case
    scores plus a ``default_score`` fallback.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if api_key:
        return AnthropicHaikuJudge(api_key=api_key)
    return ScriptedJudge(scripted_scores or {}, default_score=default_score)


__all__ = [
    "JUDGE_MODEL",
    "JUDGE_TEMPERATURE",
    "AnthropicHaikuJudge",
    "JudgeProvider",
    "ScriptedJudge",
    "make_judge_from_env",
]
