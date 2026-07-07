"""Unit tests for Stream Y-MK HTTP-status → LLMError classification.

The shared classifier distinguishes *account/key-level* failures (balance /
quota / revoked key) — which the router recovers from by trying a sibling key —
from plain rate-limits, malformed requests, and server faults.
"""

from __future__ import annotations

import pytest

from expert_work.runtime.middleware import (
    LLMClientError,
    LLMKeyUnavailableError,
    LLMRateLimitError,
    LLMServerError,
    LLMUnauthorizedError,
)
from orchestrator.llm.providers._errors import classify_http_error


@pytest.mark.parametrize(
    ("status", "body", "expected"),
    [
        # MK-9 — account/key dead → LLMKeyUnavailableError (try sibling key)
        (402, "Insufficient Balance", LLMKeyUnavailableError),  # deepseek 欠费
        (429, '{"error":{"code":"insufficient_quota"}}', LLMKeyUnavailableError),  # openai 欠费
        (429, "You exceeded your current quota, please check billing", LLMKeyUnavailableError),
        (403, "Your account has been deactivated due to billing", LLMKeyUnavailableError),
        (403, "billing_hard_limit_reached", LLMKeyUnavailableError),
        # MK-10 — plain transient / request / server → unchanged classes
        (429, '{"error":{"type":"rate_limit_exceeded"}}', LLMRateLimitError),  # 纯限流
        (429, "Rate limit reached for requests", LLMRateLimitError),
        (400, "invalid 'tools[0].function' schema", LLMClientError),  # malformed
        (403, "model not allowed for this org", LLMClientError),  # non-billing 403
        (404, "model not found", LLMClientError),
        (401, "invalid api key", LLMUnauthorizedError),  # revoked key (router → key-level)
        (500, "internal server error", LLMServerError),
        (503, "overloaded", LLMServerError),
    ],
)
def test_classify_http_error(status: int, body: str, expected: type[Exception]) -> None:
    err = classify_http_error("openai", status, body)
    assert isinstance(err, expected)
    # The originating provider + status are preserved in the message for logs.
    assert "openai" in str(err)


def test_classify_is_case_insensitive_on_quota_markers() -> None:
    assert isinstance(
        classify_http_error("deepseek", 402, "INSUFFICIENT BALANCE"),
        LLMKeyUnavailableError,
    )
