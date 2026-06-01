"""Both Stream S endpoints are wired into the real app (Stream S PR B)."""

from fastapi.testclient import TestClient

from control_plane.app import create_app


def test_schema_and_catalog_are_wired() -> None:
    client = TestClient(create_app())
    assert client.get("/v1/agents/schema").status_code in (200, 401)
    assert client.get("/v1/model-catalog").status_code in (200, 401)
