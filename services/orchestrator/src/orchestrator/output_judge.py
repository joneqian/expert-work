"""Output judge — PI-2b, the model-backed escalation above PI-2's rules.

PI-2's rule screen catches *shape-matchable* leaks (credential patterns,
auto-loading image-exfil URLs). It cannot catch a bare-token canary echoed
inline (``injection-001/002/003`` in the red-team set still leak). The judge
tier closes that gap with the LlamaFirewall **AlignmentCheck** insight
(arXiv 2505.03574): judge for *alignment*, not for the secret. Given the
user's actual request and the model's response, an injected leak shows up as
the response doing something the request never asked for (emitting a random
token) — so it is caught with **no canary known in advance**.

This module is the seam only: the :class:`OutputJudge` protocol + its verdict
+ a deterministic :class:`FakeOutputJudge` double, so the wiring unit-tests
with no model key. The real LLM-as-judge implementation is PI-2b-2.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class OutputJudgeVerdict:
    """One judge ruling on a model response.

    ``aligned`` — the response serves the user's actual request (not an
    injected instruction). ``leak_suspected`` — it appears to disclose
    confidential context or act out of scope. ``reason`` is a short,
    category-level note — it must NOT echo the response text / any secret.
    """

    aligned: bool
    leak_suspected: bool
    reason: str

    @property
    def blocked(self) -> bool:
        """Block when the response is misaligned or a leak is suspected."""
        return (not self.aligned) or self.leak_suspected


@runtime_checkable
class OutputJudge(Protocol):
    """Async judge over ``(user_request, response)`` — the PI-2b seam.

    ``context_hint`` is an optional category-level note that the agent's
    context holds confidential data the response must not disclose (never the
    secret itself). Implementations call an LLM with a few-shot rubric; the
    test double is deterministic.
    """

    async def judge(
        self, *, user_request: str, response: str, context_hint: str | None
    ) -> OutputJudgeVerdict:
        """Return a verdict; raising is treated by the caller as judge failure."""


#: Default ruling for the test double — aligned + no leak.
_OK_VERDICT = OutputJudgeVerdict(aligned=True, leak_suspected=False, reason="ok")


@dataclass(frozen=True)
class FakeOutputJudge:
    """Deterministic :class:`OutputJudge` double for wiring tests.

    Returns a fixed ``verdict``; set ``raises=True`` to simulate a judge
    failure (timeout / outage) so the fail-open vs fail-closed path is
    testable with no model key.
    """

    verdict: OutputJudgeVerdict = _OK_VERDICT
    raises: bool = False

    async def judge(
        self, *, user_request: str, response: str, context_hint: str | None
    ) -> OutputJudgeVerdict:
        del user_request, response, context_hint
        if self.raises:
            msg = "judge unavailable (test double)"
            raise RuntimeError(msg)
        return self.verdict


__all__ = ["FakeOutputJudge", "OutputJudge", "OutputJudgeVerdict"]
