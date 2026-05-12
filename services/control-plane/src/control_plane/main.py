"""Uvicorn entrypoint — ``uvicorn control_plane.main:app``."""

from __future__ import annotations

from control_plane.app import create_app

app = create_app()
