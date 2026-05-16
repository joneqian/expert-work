"""Uvicorn entrypoint — ``uvicorn sandbox_supervisor.main:app``."""

from __future__ import annotations

from sandbox_supervisor.app import create_app

app = create_app()
