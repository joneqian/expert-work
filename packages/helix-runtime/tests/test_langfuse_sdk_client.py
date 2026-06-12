"""Unit tests for the Langfuse SDK adapter — Stream HX-7 (§ 8.4-PR1).

A fake SDK object is injected (no real Langfuse instance; CI has no
credentials) — these prove the protocol→SDK mapping and the factory's
degrade-to-recording behaviour.
"""

from __future__ import annotations

import sys
from typing import Any

import pytest

from helix_agent.runtime.middleware import (
    LangfuseClient,
    LangfuseSdkClient,
    RecordingLangfuseClient,
    make_langfuse_client,
)

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakeGeneration:
    def __init__(self) -> None:
        self.updates: list[dict[str, Any]] = []
        self.ended = False

    def update(self, **kwargs: Any) -> _FakeGeneration:
        self.updates.append(kwargs)
        return self

    def end(self) -> _FakeGeneration:
        self.ended = True
        return self


class _FakeSdk:
    def __init__(self) -> None:
        self.generations: list[dict[str, Any]] = []
        self.created: list[_FakeGeneration] = []
        self.flushed = 0
        self.shutdowns = 0

    def start_generation(self, **kwargs: Any) -> _FakeGeneration:
        self.generations.append(kwargs)
        generation = _FakeGeneration()
        self.created.append(generation)
        return generation

    def flush(self) -> None:
        self.flushed += 1

    def shutdown(self) -> None:
        self.shutdowns += 1


# ---------------------------------------------------------------------------
# Adapter mapping
# ---------------------------------------------------------------------------


def test_start_span_maps_to_generation_with_model() -> None:
    sdk = _FakeSdk()
    client = LangfuseSdkClient(sdk)

    span = client.start_span(
        name="my-agent",
        input=[{"role": "user", "content": "hi"}],
        metadata={"model": "qwen-max", "tenant_id": "t-1", "run_id": "r-1"},
    )

    assert isinstance(client, LangfuseClient)  # protocol conformance
    assert sdk.generations == [
        {
            "name": "my-agent",
            "input": [{"role": "user", "content": "hi"}],
            "metadata": {"model": "qwen-max", "tenant_id": "t-1", "run_id": "r-1"},
            "model": "qwen-max",
        }
    ]
    span.end()
    assert sdk.created[0].ended is True


def test_start_span_without_model_passes_none() -> None:
    sdk = _FakeSdk()
    LangfuseSdkClient(sdk).start_span(name="n", input=None, metadata=None)
    assert sdk.generations[0]["model"] is None
    assert sdk.generations[0]["metadata"] == {}


def test_record_output_and_usage_map_to_update() -> None:
    sdk = _FakeSdk()
    span = LangfuseSdkClient(sdk).start_span(name="n", input="x", metadata={})

    span.record_output("the answer")
    span.record_usage({"input_tokens": 10, "output_tokens": 3})

    generation = sdk.created[0]
    assert generation.updates[0] == {"output": "the answer"}
    assert generation.updates[1] == {"usage_details": {"input_tokens": 10, "output_tokens": 3}}


def test_record_error_marks_level_error() -> None:
    sdk = _FakeSdk()
    span = LangfuseSdkClient(sdk).start_span(name="n", input="x", metadata={})

    span.record_error(RuntimeError("provider down"))

    assert sdk.created[0].updates == [
        {"level": "ERROR", "status_message": "RuntimeError: provider down"}
    ]


def test_flush_and_shutdown_delegate() -> None:
    sdk = _FakeSdk()
    client = LangfuseSdkClient(sdk)
    client.flush()
    client.shutdown()
    assert sdk.flushed == 1
    assert sdk.shutdowns == 1


# ---------------------------------------------------------------------------
# Factory resolution (Mini-ADR HX-G3)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("host", "public", "secret"),
    [
        (None, None, None),
        ("https://langfuse.local", None, "sk"),
        ("https://langfuse.local", "pk", None),
        (None, "pk", "sk"),
        ("", "pk", "sk"),
    ],
)
def test_factory_incomplete_settings_degrade_to_recording(
    host: str | None, public: str | None, secret: str | None
) -> None:
    client = make_langfuse_client(host=host, public_key=public, secret_key=secret)
    assert isinstance(client, RecordingLangfuseClient)


def test_factory_import_failure_degrades_to_recording(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Simulate a broken install: ``import langfuse`` must fail inside the
    # factory. A None entry in sys.modules raises ImportError on import.
    monkeypatch.setitem(sys.modules, "langfuse", None)
    client = make_langfuse_client(host="https://langfuse.local", public_key="pk", secret_key="sk")
    assert isinstance(client, RecordingLangfuseClient)


def test_factory_complete_settings_build_sdk_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    constructed: list[dict[str, Any]] = []

    class _FakeLangfuse:
        def __init__(self, **kwargs: Any) -> None:
            constructed.append(kwargs)

    import types

    fake_module = types.ModuleType("langfuse")
    fake_module.Langfuse = _FakeLangfuse  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "langfuse", fake_module)

    client = make_langfuse_client(host="https://langfuse.local", public_key="pk", secret_key="sk")

    assert isinstance(client, LangfuseSdkClient)
    assert constructed == [
        {
            "public_key": "pk",
            "secret_key": "sk",
            "host": "https://langfuse.local",
            "tracing_enabled": True,
        }
    ]
