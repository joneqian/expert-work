"""Local-dev SecretStore — ``.env``-file / in-memory backend (Stream F.6).

The zero-dependency backend for development and tests (ADR-0007 § 2.3).
Secrets live in a plain mapping — seeded from a ``.env``-style file
(git-ignored) or constructed directly in test code. There is no
encryption, no versioning, no network: a real backend (Aliyun KMS)
handles those in production.

``.env`` format — one ``name=value`` per line:

    # comments and blank lines are ignored
    expert-work/dev/llm/anthropic-api-key=sk-ant-xxxxx
    expert-work/dev/llm/openai-api-key="sk-openai-xxxxx"

The key (left of the first ``=``) is the secret *name* exactly as
:meth:`SecretStore.get` receives it. Values may be optionally wrapped
in single or double quotes, which are stripped.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Self

from expert_work.runtime.secret_store.base import SecretNotFoundError

#: Synthetic version id — the dev backend does not version secrets, but
#: :meth:`SecretStore.list_versions` must return a non-empty list.
_DEV_VERSION = "local-dev"


@dataclass
class LocalDevSecretStore:
    """In-memory :class:`~expert_work.runtime.secret_store.base.SecretStore`.

    Construct with :meth:`from_env_file` (the usual dev path), with
    :meth:`from_mapping`, or directly with a ``secrets`` dict in tests.
    """

    secrets: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, mapping: Mapping[str, str]) -> Self:
        """Build a store from an explicit ``name → value`` mapping."""
        return cls(secrets=dict(mapping))

    @classmethod
    def from_env_file(cls, path: str | Path) -> Self:
        """Build a store from a ``.env``-style file.

        A missing file yields an **empty** store rather than raising —
        a dev checkout without a local ``.env`` should still boot (and
        fail later, loudly, only if a secret is actually requested).
        """
        env_path = Path(path)
        if not env_path.is_file():
            return cls(secrets={})
        return cls(secrets=_parse_env_file(env_path.read_text(encoding="utf-8")))

    async def get(self, name: str, *, version: str | None = None) -> str:
        if version is not None and version != _DEV_VERSION:
            raise SecretNotFoundError(f"{name}@{version}")
        try:
            return self.secrets[name]
        except KeyError:
            raise SecretNotFoundError(name) from None

    async def put(self, name: str, value: str) -> None:
        self.secrets[name] = value

    async def list_versions(self, name: str) -> list[str]:
        if name not in self.secrets:
            raise SecretNotFoundError(name)
        return [_DEV_VERSION]

    async def delete(self, name: str) -> None:
        self.secrets.pop(name, None)


def _parse_env_file(text: str) -> dict[str, str]:
    """Parse ``name=value`` lines; skip blanks / ``#`` comments; unquote."""
    result: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        name = name.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        if name:
            result[name] = value
    return result
