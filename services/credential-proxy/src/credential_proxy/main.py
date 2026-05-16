"""Entrypoint — ``python -m credential_proxy``."""

from __future__ import annotations

import logging

from aiohttp import web

from credential_proxy.app import create_app
from credential_proxy.settings import CredentialProxySettings


def main() -> None:
    settings = CredentialProxySettings()
    logging.basicConfig(level=settings.log_level)
    web.run_app(create_app(settings), host=settings.host, port=settings.port)


if __name__ == "__main__":
    main()
