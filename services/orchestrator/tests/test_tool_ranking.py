"""Unit tests for :mod:`orchestrator.tools.ranking` — Stream HX-12 (HX-I1/I2)."""

from __future__ import annotations

from orchestrator.tools.ranking import build_document, rank_tools, split_identifier


def test_split_identifier_handles_separators_and_camel_case() -> None:
    assert split_identifier("mcp:github.create_pull_request") == [
        "mcp",
        "github",
        "create",
        "pull",
        "request",
    ]
    assert split_identifier("createCalendarEvent") == ["create", "calendar", "event"]
    assert split_identifier("send-email") == ["send", "email"]


def test_build_document_includes_name_description_and_params() -> None:
    doc = build_document("calendar_create", "Create an event", ["event_title", "start_time"])
    assert "calendar" in doc
    assert "create" in doc
    assert "event" in doc
    assert "title" in doc
    assert "start" in doc


def _corpus() -> list[tuple[str, list[str]]]:
    return [
        (
            "calendar_create_event",
            build_document(
                "calendar_create_event", "Create a new event on the user's calendar", ["title"]
            ),
        ),
        ("calendar_list", build_document("calendar_list", "List upcoming calendar entries", [])),
        ("send_email", build_document("send_email", "Send an email message", ["to", "subject"])),
    ]


def test_rank_tools_orders_by_relevance() -> None:
    ranked = rank_tools("create a calendar event", _corpus())
    assert ranked[0] == "calendar_create_event"
    assert "send_email" not in ranked


def test_rank_tools_chinese_query() -> None:
    corpus = [
        ("doc_search", build_document("doc_search", "搜索公司内部文档并返回摘要", [])),
        ("send_email", build_document("send_email", "发送电子邮件", [])),
    ]
    ranked = rank_tools("搜索文档", corpus)
    assert ranked and ranked[0] == "doc_search"


def test_rank_tools_zero_overlap_returns_empty() -> None:
    # Zero lexical overlap → empty, so the caller falls back to substring.
    assert rank_tools("zzqx", _corpus()) == []


def test_rank_tools_empty_corpus_and_query() -> None:
    assert rank_tools("anything", []) == []
    assert rank_tools("   ", _corpus()) == []


def test_rank_tools_respects_top_k() -> None:
    corpus = [
        (f"tool_{i}", build_document(f"tool_{i}", "shared keyword target", [])) for i in range(20)
    ]
    ranked = rank_tools("target", corpus, top_k=5)
    assert len(ranked) == 5
