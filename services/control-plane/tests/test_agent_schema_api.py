"""GET /v1/agents/schema — Stream S PR B (Mini-ADR S-1)."""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from control_plane.api.agent_schema import build_agent_schema_router


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(build_agent_schema_router())
    return TestClient(app)


def test_schema_endpoint_returns_agentspec_json_schema() -> None:
    resp = _client().get("/v1/agents/schema")
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    schema = body["data"]
    assert "apiVersion" in schema["properties"]
    assert "spec" in schema["properties"]
    assert "kind" in schema["properties"]
