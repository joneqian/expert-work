"""Tests for ``tokenize_for_search`` — J.5 hybrid-search tokenization."""

from __future__ import annotations

from expert_work.persistence.knowledge.text_search import tokenize_for_search


def test_segments_chinese_into_multiple_tokens() -> None:
    result = tokenize_for_search("知识检索系统")
    # jieba splits CJK text on word boundaries — more than one token.
    assert len(result.split()) > 1
    # No characters are lost in segmentation.
    assert "".join(result.split()) == "知识检索系统"


def test_english_tokens_lowercased() -> None:
    result = tokenize_for_search("Hello World")
    tokens = result.split()
    assert "hello" in tokens
    assert "world" in tokens
    assert "Hello" not in tokens


def test_mixed_language() -> None:
    result = tokenize_for_search("查询 PostgreSQL 数据库")
    assert "postgresql" in result.split()


def test_empty_and_whitespace_yield_empty() -> None:
    assert tokenize_for_search("") == ""
    assert tokenize_for_search("   ") == ""
