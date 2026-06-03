# Stream V-B — Tenant MCP Server Registry (persistence layer) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a tenant-scoped `tenant_mcp_server` registry (protocol record + ORM model + Alembic migration + base/sql/memory store triple) plus a reusable SSRF URL-validation utility — the persistence foundation for Stream V (tenant self-service remote MCP servers).

**Architecture:** Mirror the existing `tenant_config` store triple exactly. The new table stores **only** remote (`sse`/`streamable_http`) MCP server records; the bearer token lives in the encrypted secret store (Stream T) and the table holds only its `secret://` ref. Row-Level Security isolates rows per tenant, identical to `tenant_member`. The SSRF guard is a pure function in `helix-common` consumed later by V-C (registration/probe) and V-D (runtime connect).

**Tech Stack:** Python 3.12, SQLAlchemy 2.0 async, Alembic, Pydantic v2, pytest (+ pytest-asyncio, testcontainers for RLS integration).

**Scope (this PR = V-B only):** persistence + util + tests. NO API endpoints (V-C), NO runtime wiring (V-D), NO manifest schema change (V-E), NO UI (V-F/V-G).

**Branch:** `stream-v/b-registry` (off `main`).

**Key facts (verified 2026-06-03):**
- Persistence package root: `helix_agent.persistence` (`packages/helix-persistence/src/helix_agent/persistence/`).
- Protocol package root: `helix_agent.protocol` (`packages/helix-protocol/src/helix_agent/protocol/`).
- Common package root: `helix_agent.common` (`packages/helix-common/src/helix_agent/common/`).
- Declarative `Base`: `from helix_agent.persistence.base import Base`.
- **Current Alembic head: `0053_tenant_status`** → new migration `down_revision = "0053_tenant_status"`.
- RLS GUC pattern: `tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid`.
- Migrations dir: `packages/helix-persistence/migrations/versions/`.
- CI scopes ([memory:reference_ci_lint_type_test_scopes]): `uv run python -m pytest -m "not integration"` from repo root for unit; mypy excludes control-plane/src; RLS tests are `@pytest.mark.integration` (need Postgres, run separately).

---

## File Structure

**Create:**
- `packages/helix-common/src/helix_agent/common/url_validation.py` — `RemoteURLError`, `validate_remote_url()` (SSRF guard).
- `packages/helix-common/tests/test_url_validation.py` — unit tests.
- `packages/helix-protocol/src/helix_agent/protocol/tenant_mcp_server.py` — `McpServerTransport`, `McpServerAuthType`, `TenantMcpServerRecord`, `TenantMcpServerPatch`.
- `packages/helix-protocol/tests/test_tenant_mcp_server.py` — record/validator unit tests.
- `packages/helix-persistence/src/helix_agent/persistence/models/tenant_mcp_server.py` — `TenantMcpServerRow` ORM model.
- `packages/helix-persistence/migrations/versions/0054_tenant_mcp_server.py` — table + RLS migration.
- `packages/helix-persistence/src/helix_agent/persistence/tenant_mcp_server/__init__.py` — package exports.
- `packages/helix-persistence/src/helix_agent/persistence/tenant_mcp_server/base.py` — `TenantMcpServerStore` ABC + exceptions.
- `packages/helix-persistence/src/helix_agent/persistence/tenant_mcp_server/memory.py` — `InMemoryTenantMcpServerStore`.
- `packages/helix-persistence/src/helix_agent/persistence/tenant_mcp_server/sql.py` — `SqlTenantMcpServerStore`.
- `packages/helix-persistence/tests/test_in_memory_tenant_mcp_server_store.py` — unit tests.
- `packages/helix-persistence/tests/test_sql_tenant_mcp_server_store.py` — integration (RLS) tests.

**Modify:**
- `packages/helix-common/src/helix_agent/common/__init__.py` — export `validate_remote_url`, `RemoteURLError`.
- `packages/helix-protocol/src/helix_agent/protocol/__init__.py` — export new protocol symbols.
- `packages/helix-persistence/src/helix_agent/persistence/models/__init__.py` — export `TenantMcpServerRow`.
- `packages/helix-persistence/src/helix_agent/persistence/__init__.py` — export store classes + exceptions.

---

## Task 1: SSRF URL-validation utility (helix-common)

**Files:**
- Create: `packages/helix-common/src/helix_agent/common/url_validation.py`
- Create: `packages/helix-common/tests/test_url_validation.py`
- Modify: `packages/helix-common/src/helix_agent/common/__init__.py`

- [ ] **Step 1: Write the failing test**

Create `packages/helix-common/tests/test_url_validation.py`:

```python
"""Unit tests for the remote-URL SSRF guard."""

from __future__ import annotations

import pytest

from helix_agent.common.url_validation import RemoteURLError, validate_remote_url


@pytest.mark.parametrize(
    "url",
    [
        "https://mcp.githubcopilot.com/mcp",
        "https://api.example.com:8443/sse",
        "http://public.example.org/mcp",
    ],
)
def test_accepts_public_https_and_http(url: str) -> None:
    assert validate_remote_url(url) == url


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost/mcp",
        "http://127.0.0.1:8080/mcp",
        "http://[::1]/mcp",
        "http://10.1.2.3/mcp",
        "http://172.16.5.4/mcp",
        "http://192.168.0.10/mcp",
        "http://169.254.169.254/latest/meta-data",  # cloud metadata
        "http://0.0.0.0/mcp",
    ],
)
def test_rejects_private_loopback_linklocal_metadata(url: str) -> None:
    with pytest.raises(RemoteURLError):
        validate_remote_url(url)


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "ftp://example.com/x",
        "gopher://example.com/x",
        "ws://example.com/x",
    ],
)
def test_rejects_unsupported_schemes(url: str) -> None:
    with pytest.raises(RemoteURLError):
        validate_remote_url(url)


def test_rejects_missing_hostname() -> None:
    with pytest.raises(RemoteURLError):
        validate_remote_url("https:///mcp")


def test_https_only_mode_rejects_http() -> None:
    with pytest.raises(RemoteURLError):
        validate_remote_url("http://public.example.org/mcp", allowed_schemes=("https",))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/mac/src/github/jone_qian/helix-agent && uv run python -m pytest packages/helix-common/tests/test_url_validation.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'helix_agent.common.url_validation'`.

- [ ] **Step 3: Write the implementation**

Create `packages/helix-common/src/helix_agent/common/url_validation.py`:

```python
"""Remote-URL validation — SSRF guard for tenant-supplied MCP server URLs.

A tenant registers a remote MCP server by URL; the control plane then
*connects out* to that URL (registration probe + runtime tool calls). An
unchecked URL lets a tenant point the platform at internal services or the
cloud metadata endpoint (169.254.169.254) — a classic SSRF. This guard is
applied at every connect-out site (registration, probe, runtime).

The check is static (scheme + IP-literal ranges + localhost names). It does
NOT resolve DNS; DNS-rebind defense (resolve, then re-check the resolved IP)
is a deeper follow-up. Static checks block the common cases (literal private
IPs, localhost, metadata IP).
"""

from __future__ import annotations

import ipaddress
from urllib.parse import urlparse

_LOCALHOST_NAMES = frozenset({"localhost", "localhost.localdomain", "ip6-localhost"})


class RemoteURLError(ValueError):
    """A URL fails remote-endpoint validation (unsupported scheme or SSRF risk)."""


def _ip_is_blocked(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local  # includes 169.254.0.0/16 (cloud metadata)
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified  # 0.0.0.0 / ::
    )


def validate_remote_url(
    url: str,
    *,
    allowed_schemes: tuple[str, ...] = ("http", "https"),
) -> str:
    """Validate a tenant-supplied remote URL for safe connect-out.

    Returns ``url`` unchanged when valid. Raises :class:`RemoteURLError` for
    an unsupported scheme, a missing hostname, a localhost name, or a
    private / loopback / link-local / reserved / multicast / unspecified IP
    literal.

    ``allowed_schemes`` defaults to ``("http", "https")``; pass
    ``("https",)`` to forbid plaintext (production).
    """
    parsed = urlparse(url)

    if parsed.scheme not in allowed_schemes:
        msg = f"unsupported URL scheme {parsed.scheme!r}; allowed: {allowed_schemes}"
        raise RemoteURLError(msg)

    hostname = parsed.hostname
    if not hostname:
        msg = f"URL has no hostname: {url!r}"
        raise RemoteURLError(msg)

    if hostname.lower() in _LOCALHOST_NAMES:
        msg = f"localhost address {hostname!r} not allowed"
        raise RemoteURLError(msg)

    try:
        ip = ipaddress.ip_address(hostname)
    except ValueError:
        # Hostname is a DNS name, not an IP literal — static check passes.
        return url

    if _ip_is_blocked(ip):
        msg = f"private/loopback/link-local IP {hostname!r} not allowed"
        raise RemoteURLError(msg)

    return url
```

- [ ] **Step 4: Export from the package**

In `packages/helix-common/src/helix_agent/common/__init__.py`, add the import near the other imports and add both names to `__all__` (keep `__all__` sorted if it already is):

```python
from helix_agent.common.url_validation import RemoteURLError, validate_remote_url
```

Add `"RemoteURLError"` and `"validate_remote_url"` to the `__all__` list. If `__init__.py` has no `__all__`, just add the import line.

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd /Users/mac/src/github/jone_qian/helix-agent && uv run python -m pytest packages/helix-common/tests/test_url_validation.py -q`
Expected: PASS (all parametrized cases green).

- [ ] **Step 6: Commit**

```bash
git add packages/helix-common/src/helix_agent/common/url_validation.py \
        packages/helix-common/src/helix_agent/common/__init__.py \
        packages/helix-common/tests/test_url_validation.py
git commit -m "feat(stream-v): SSRF guard validate_remote_url (V-B)"
```

---

## Task 2: Protocol record + literals + patch

**Files:**
- Create: `packages/helix-protocol/src/helix_agent/protocol/tenant_mcp_server.py`
- Create: `packages/helix-protocol/tests/test_tenant_mcp_server.py`
- Modify: `packages/helix-protocol/src/helix_agent/protocol/__init__.py`

- [ ] **Step 1: Write the failing test**

Create `packages/helix-protocol/tests/test_tenant_mcp_server.py`:

```python
"""Unit tests for TenantMcpServerRecord validation."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from helix_agent.protocol import TenantMcpServerRecord


def _record(**overrides: object) -> TenantMcpServerRecord:
    base: dict[str, object] = {
        "id": uuid4(),
        "tenant_id": uuid4(),
        "name": "github",
        "transport": "streamable_http",
        "url": "https://mcp.example.com/mcp",
        "auth_type": "none",
        "token_secret_ref": None,
        "timeout_s": 30.0,
        "enabled": True,
        "created_at": datetime.now(tz=UTC),
        "updated_at": datetime.now(tz=UTC),
        "created_by": "admin@acme",
    }
    base.update(overrides)
    return TenantMcpServerRecord(**base)  # type: ignore[arg-type]


def test_valid_none_auth_record() -> None:
    rec = _record()
    assert rec.name == "github"
    assert rec.auth_type == "none"


def test_valid_bearer_record_with_token_ref() -> None:
    rec = _record(auth_type="bearer", token_secret_ref="secret://helix-agent/t/mcp/github/token")
    assert rec.auth_type == "bearer"


def test_bearer_without_token_ref_rejected() -> None:
    with pytest.raises(ValueError, match="bearer auth requires token_secret_ref"):
        _record(auth_type="bearer", token_secret_ref=None)


def test_none_auth_with_token_ref_rejected() -> None:
    with pytest.raises(ValueError, match="token_secret_ref must be empty"):
        _record(auth_type="none", token_secret_ref="secret://x")


@pytest.mark.parametrize("bad_name", ["", "Has Space", "UPPER", "a/b", "x" * 65, "-leading"])
def test_invalid_server_name_rejected(bad_name: str) -> None:
    with pytest.raises(ValueError):
        _record(name=bad_name)


@pytest.mark.parametrize("good_name", ["github", "linear-prod", "pg_main", "a1"])
def test_valid_server_name_accepted(good_name: str) -> None:
    assert _record(name=good_name).name == good_name


def test_frozen() -> None:
    rec = _record()
    with pytest.raises(Exception):
        rec.name = "other"  # type: ignore[misc]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/mac/src/github/jone_qian/helix-agent && uv run python -m pytest packages/helix-protocol/tests/test_tenant_mcp_server.py -q`
Expected: FAIL — `ImportError: cannot import name 'TenantMcpServerRecord'`.

- [ ] **Step 3: Write the implementation**

Create `packages/helix-protocol/src/helix_agent/protocol/tenant_mcp_server.py`:

```python
"""``tenant_mcp_server`` registry record — Stream V.

A tenant-registered **remote** MCP server (sse / streamable_http). stdio
servers are operator-only (subprocess RCE risk) and never live here. The
bearer token is stored in the encrypted secret store; this record holds only
its ``secret://`` reference.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

McpServerTransport = Literal["sse", "streamable_http"]
McpServerAuthType = Literal["none", "bearer"]

# Server name is used in the runtime tool namespace (``mcp:<name>.<tool>``)
# and in the secret path — restrict to a safe slug.
_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


class TenantMcpServerRecord(BaseModel):
    """One row of ``tenant_mcp_server`` as exposed across layers."""

    model_config = ConfigDict(frozen=True)

    id: UUID
    tenant_id: UUID
    name: str
    transport: McpServerTransport
    url: str
    auth_type: McpServerAuthType = "none"
    token_secret_ref: str | None = None
    timeout_s: float = Field(default=30.0, gt=0, le=300)
    enabled: bool = True
    created_at: datetime
    updated_at: datetime
    created_by: str

    @model_validator(mode="after")
    def _validate(self) -> TenantMcpServerRecord:
        if not _NAME_RE.match(self.name):
            msg = (
                f"invalid MCP server name {self.name!r}: must match "
                r"^[a-z0-9][a-z0-9_-]{0,63}$"
            )
            raise ValueError(msg)
        if self.auth_type == "bearer" and not self.token_secret_ref:
            raise ValueError("bearer auth requires token_secret_ref")
        if self.auth_type == "none" and self.token_secret_ref:
            raise ValueError("token_secret_ref must be empty when auth_type='none'")
        return self


class TenantMcpServerPatch(BaseModel):
    """Partial update payload (V-C ``PATCH``). ``None`` = leave unchanged.

    Auth-type changes are out of scope — to switch between none/bearer, delete
    and re-register. Rotating a bearer token sets a new ``token_secret_ref``.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    url: str | None = None
    token_secret_ref: str | None = None
    timeout_s: float | None = Field(default=None, gt=0, le=300)
    enabled: bool | None = None
```

- [ ] **Step 4: Export from the package**

In `packages/helix-protocol/src/helix_agent/protocol/__init__.py`, mirror the `tenant_config` export style. Add imports:

```python
from helix_agent.protocol.tenant_mcp_server import (
    McpServerAuthType as McpServerAuthType,
)
from helix_agent.protocol.tenant_mcp_server import (
    McpServerTransport as McpServerTransport,
)
from helix_agent.protocol.tenant_mcp_server import (
    TenantMcpServerPatch as TenantMcpServerPatch,
)
from helix_agent.protocol.tenant_mcp_server import (
    TenantMcpServerRecord as TenantMcpServerRecord,
)
```

If the file has an `__all__`, add `"McpServerAuthType"`, `"McpServerTransport"`, `"TenantMcpServerPatch"`, `"TenantMcpServerRecord"` (keep sorting consistent with the existing list).

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd /Users/mac/src/github/jone_qian/helix-agent && uv run python -m pytest packages/helix-protocol/tests/test_tenant_mcp_server.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add packages/helix-protocol/src/helix_agent/protocol/tenant_mcp_server.py \
        packages/helix-protocol/src/helix_agent/protocol/__init__.py \
        packages/helix-protocol/tests/test_tenant_mcp_server.py
git commit -m "feat(stream-v): TenantMcpServerRecord + Patch protocol types (V-B)"
```

---

## Task 3: ORM model

**Files:**
- Create: `packages/helix-persistence/src/helix_agent/persistence/models/tenant_mcp_server.py`
- Modify: `packages/helix-persistence/src/helix_agent/persistence/models/__init__.py`

- [ ] **Step 1: Write the failing test**

Create a temporary import check (will be deleted — the real coverage is the store tests). Add to `packages/helix-persistence/tests/test_in_memory_tenant_mcp_server_store.py` later; for now write a one-off:

Run: `cd /Users/mac/src/github/jone_qian/helix-agent && uv run python -c "from helix_agent.persistence.models import TenantMcpServerRow; print(TenantMcpServerRow.__tablename__)"`
Expected: FAIL — `ImportError: cannot import name 'TenantMcpServerRow'`.

- [ ] **Step 2: Write the implementation**

Create `packages/helix-persistence/src/helix_agent/persistence/models/tenant_mcp_server.py`:

```python
"""``tenant_mcp_server`` ORM model — Stream V.

Tenant-registered remote MCP servers. RLS (tenant isolation) is declared in
migration ``0054_tenant_mcp_server``, not here — the model is purely
structural (mirrors ``tenant_config`` / ``tenant_member``).
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import Boolean, DateTime, Float, Text, func, text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from helix_agent.persistence.base import Base


class TenantMcpServerRow(Base):
    """One tenant-registered remote MCP server."""

    __tablename__ = "tenant_mcp_server"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    transport: Mapped[str] = mapped_column(Text, nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    auth_type: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'none'")
    )
    token_secret_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    timeout_s: Mapped[float] = mapped_column(
        Float, nullable=False, server_default=text("30")
    )
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    created_by: Mapped[str] = mapped_column(Text, nullable=False)
```

- [ ] **Step 3: Export the model**

In `packages/helix-persistence/src/helix_agent/persistence/models/__init__.py`, add (alphabetically near the other `tenant_*` imports):

```python
from helix_agent.persistence.models.tenant_mcp_server import TenantMcpServerRow
```

If the file has an `__all__`, add `"TenantMcpServerRow"`.

- [ ] **Step 4: Run the import check to verify it passes**

Run: `cd /Users/mac/src/github/jone_qian/helix-agent && uv run python -c "from helix_agent.persistence.models import TenantMcpServerRow; print(TenantMcpServerRow.__tablename__)"`
Expected: prints `tenant_mcp_server`.

- [ ] **Step 5: Commit**

```bash
git add packages/helix-persistence/src/helix_agent/persistence/models/tenant_mcp_server.py \
        packages/helix-persistence/src/helix_agent/persistence/models/__init__.py
git commit -m "feat(stream-v): tenant_mcp_server ORM model (V-B)"
```

---

## Task 4: Alembic migration (table + RLS)

**Files:**
- Create: `packages/helix-persistence/migrations/versions/0054_tenant_mcp_server.py`

- [ ] **Step 1: Confirm the current head**

Run: `cd /Users/mac/src/github/jone_qian/helix-agent && ls packages/helix-persistence/migrations/versions/ | sort | tail -5`
Expected: `0053_tenant_status.py` is the latest numbered revision. Confirm no revision lists `0053_tenant_status` as its `down_revision` (i.e. it is the head). If a newer head exists, set `down_revision` to that instead and rename the file's numeric prefix accordingly.

- [ ] **Step 2: Write the migration**

Create `packages/helix-persistence/migrations/versions/0054_tenant_mcp_server.py`:

```python
"""tenant_mcp_server registry table + RLS — Stream V-B.

Revision ID: 0054_tenant_mcp_server
Revises: 0053_tenant_status
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0054_tenant_mcp_server"
down_revision: str | Sequence[str] | None = "0053_tenant_status"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "tenant_mcp_server"
_POLICY = "tenant_mcp_server_tenant_isolation"


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("transport", sa.Text(), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column(
            "auth_type", sa.Text(), nullable=False, server_default=sa.text("'none'")
        ),
        sa.Column("token_secret_ref", sa.Text(), nullable=True),
        sa.Column(
            "timeout_s",
            sa.Float(),
            nullable=False,
            server_default=sa.text("30"),
        ),
        sa.Column(
            "enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("created_by", sa.Text(), nullable=False),
        sa.CheckConstraint(
            "transport IN ('sse', 'streamable_http')",
            name="tenant_mcp_server_transport_check",
        ),
        sa.CheckConstraint(
            "auth_type IN ('none', 'bearer')",
            name="tenant_mcp_server_auth_type_check",
        ),
    )
    op.create_index("tenant_mcp_server_tenant_idx", _TABLE, ["tenant_id"])
    op.create_index(
        "tenant_mcp_server_name_uniq",
        _TABLE,
        ["tenant_id", "name"],
        unique=True,
    )

    op.execute(f"ALTER TABLE {_TABLE} ENABLE ROW LEVEL SECURITY;")
    op.execute(f"ALTER TABLE {_TABLE} FORCE ROW LEVEL SECURITY;")
    op.execute(
        f"CREATE POLICY {_POLICY} ON {_TABLE} "
        "USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid) "
        "WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid);"
    )


def downgrade() -> None:
    op.execute(f"DROP POLICY IF EXISTS {_POLICY} ON {_TABLE};")
    op.drop_table(_TABLE)
```

- [ ] **Step 3: Verify the revision id length and chain**

Run: `cd /Users/mac/src/github/jone_qian/helix-agent && python -c "print(len('0054_tenant_mcp_server'))"`
Expected: `22` (≤ 32 — [memory:alembic-revision-id-32-chars]).

The migration is exercised by the integration tests in Task 7 (`command.upgrade(cfg, "head")`). No standalone run here.

- [ ] **Step 4: Commit**

```bash
git add packages/helix-persistence/migrations/versions/0054_tenant_mcp_server.py
git commit -m "feat(stream-v): 0054 tenant_mcp_server migration + RLS (V-B)"
```

---

## Task 5: Store base ABC + exceptions

**Files:**
- Create: `packages/helix-persistence/src/helix_agent/persistence/tenant_mcp_server/__init__.py` (minimal for now; finalized in Task 8)
- Create: `packages/helix-persistence/src/helix_agent/persistence/tenant_mcp_server/base.py`

- [ ] **Step 1: Write the base module**

Create `packages/helix-persistence/src/helix_agent/persistence/tenant_mcp_server/base.py`:

```python
"""Persistence Protocol for the tenant MCP server registry — Stream V."""

from __future__ import annotations

import abc
from uuid import UUID

from helix_agent.protocol import (
    McpServerAuthType,
    McpServerTransport,
    TenantMcpServerPatch,
    TenantMcpServerRecord,
)


class TenantMcpServerNotFoundError(Exception):
    """No ``tenant_mcp_server`` row for the requested (tenant, name)."""

    def __init__(self, *, tenant_id: UUID, name: str) -> None:
        super().__init__(f"tenant_mcp_server not found: tenant_id={tenant_id} name={name!r}")
        self.tenant_id = tenant_id
        self.name = name


class TenantMcpServerAlreadyExistsError(Exception):
    """A ``tenant_mcp_server`` row already exists for (tenant, name)."""

    def __init__(self, *, tenant_id: UUID, name: str) -> None:
        super().__init__(
            f"tenant_mcp_server already exists: tenant_id={tenant_id} name={name!r}"
        )
        self.tenant_id = tenant_id
        self.name = name


class TenantMcpServerStore(abc.ABC):
    """CRUD for tenant-registered remote MCP servers."""

    @abc.abstractmethod
    async def create(
        self,
        *,
        tenant_id: UUID,
        name: str,
        transport: McpServerTransport,
        url: str,
        auth_type: McpServerAuthType,
        token_secret_ref: str | None,
        timeout_s: float,
        created_by: str,
    ) -> TenantMcpServerRecord:
        """Insert a new server row. Raises
        :class:`TenantMcpServerAlreadyExistsError` on (tenant, name) conflict."""

    @abc.abstractmethod
    async def get(self, *, tenant_id: UUID, name: str) -> TenantMcpServerRecord | None:
        """Return the row, or None if absent."""

    @abc.abstractmethod
    async def list_for_tenant(self, *, tenant_id: UUID) -> list[TenantMcpServerRecord]:
        """Return all rows for the tenant, ordered by ``name``."""

    @abc.abstractmethod
    async def update(
        self, *, tenant_id: UUID, name: str, patch: TenantMcpServerPatch
    ) -> TenantMcpServerRecord:
        """Apply a partial update. Raises
        :class:`TenantMcpServerNotFoundError` if absent."""

    @abc.abstractmethod
    async def delete(self, *, tenant_id: UUID, name: str) -> None:
        """Delete the row. Raises
        :class:`TenantMcpServerNotFoundError` if absent."""
```

- [ ] **Step 2: Create a minimal package `__init__.py`** (finalized in Task 8)

Create `packages/helix-persistence/src/helix_agent/persistence/tenant_mcp_server/__init__.py`:

```python
"""Tenant MCP server registry persistence — Stream V."""

from helix_agent.persistence.tenant_mcp_server.base import (
    TenantMcpServerAlreadyExistsError,
    TenantMcpServerNotFoundError,
    TenantMcpServerStore,
)

__all__ = [
    "TenantMcpServerAlreadyExistsError",
    "TenantMcpServerNotFoundError",
    "TenantMcpServerStore",
]
```

- [ ] **Step 3: Verify import**

Run: `cd /Users/mac/src/github/jone_qian/helix-agent && uv run python -c "from helix_agent.persistence.tenant_mcp_server import TenantMcpServerStore; print('ok')"`
Expected: prints `ok`.

- [ ] **Step 4: Commit**

```bash
git add packages/helix-persistence/src/helix_agent/persistence/tenant_mcp_server/
git commit -m "feat(stream-v): TenantMcpServerStore ABC + exceptions (V-B)"
```

---

## Task 6: In-memory store + unit tests

**Files:**
- Create: `packages/helix-persistence/src/helix_agent/persistence/tenant_mcp_server/memory.py`
- Create: `packages/helix-persistence/tests/test_in_memory_tenant_mcp_server_store.py`

- [ ] **Step 1: Write the failing tests**

Create `packages/helix-persistence/tests/test_in_memory_tenant_mcp_server_store.py`:

```python
"""Unit tests for the in-memory tenant MCP server store."""

from __future__ import annotations

from uuid import uuid4

import pytest

from helix_agent.persistence.tenant_mcp_server import (
    InMemoryTenantMcpServerStore,
    TenantMcpServerAlreadyExistsError,
    TenantMcpServerNotFoundError,
)
from helix_agent.protocol import TenantMcpServerPatch


async def _make(store: InMemoryTenantMcpServerStore, tenant_id, **over):
    kwargs = {
        "tenant_id": tenant_id,
        "name": "github",
        "transport": "streamable_http",
        "url": "https://mcp.example.com/mcp",
        "auth_type": "none",
        "token_secret_ref": None,
        "timeout_s": 30.0,
        "created_by": "admin@acme",
    }
    kwargs.update(over)
    return await store.create(**kwargs)


@pytest.mark.asyncio
async def test_create_then_get_round_trip() -> None:
    store = InMemoryTenantMcpServerStore()
    tid = uuid4()
    created = await _make(store, tid)
    assert created.name == "github"
    assert created.tenant_id == tid
    fetched = await store.get(tenant_id=tid, name="github")
    assert fetched is not None
    assert fetched.id == created.id


@pytest.mark.asyncio
async def test_get_absent_returns_none() -> None:
    store = InMemoryTenantMcpServerStore()
    assert await store.get(tenant_id=uuid4(), name="nope") is None


@pytest.mark.asyncio
async def test_duplicate_name_same_tenant_rejected() -> None:
    store = InMemoryTenantMcpServerStore()
    tid = uuid4()
    await _make(store, tid)
    with pytest.raises(TenantMcpServerAlreadyExistsError):
        await _make(store, tid)


@pytest.mark.asyncio
async def test_same_name_different_tenant_ok() -> None:
    store = InMemoryTenantMcpServerStore()
    a, b = uuid4(), uuid4()
    await _make(store, a)
    await _make(store, b)  # no conflict
    assert (await store.get(tenant_id=a, name="github")) is not None
    assert (await store.get(tenant_id=b, name="github")) is not None


@pytest.mark.asyncio
async def test_list_for_tenant_sorted_and_scoped() -> None:
    store = InMemoryTenantMcpServerStore()
    a, b = uuid4(), uuid4()
    await _make(store, a, name="zeta")
    await _make(store, a, name="alpha")
    await _make(store, b, name="gamma")
    names = [r.name for r in await store.list_for_tenant(tenant_id=a)]
    assert names == ["alpha", "zeta"]


@pytest.mark.asyncio
async def test_update_applies_partial_fields() -> None:
    store = InMemoryTenantMcpServerStore()
    tid = uuid4()
    await _make(store, tid)
    updated = await store.update(
        tenant_id=tid,
        name="github",
        patch=TenantMcpServerPatch(url="https://new.example.com/mcp", enabled=False),
    )
    assert updated.url == "https://new.example.com/mcp"
    assert updated.enabled is False
    assert updated.updated_at >= updated.created_at


@pytest.mark.asyncio
async def test_update_absent_raises() -> None:
    store = InMemoryTenantMcpServerStore()
    with pytest.raises(TenantMcpServerNotFoundError):
        await store.update(
            tenant_id=uuid4(), name="nope", patch=TenantMcpServerPatch(enabled=False)
        )


@pytest.mark.asyncio
async def test_delete_removes_row() -> None:
    store = InMemoryTenantMcpServerStore()
    tid = uuid4()
    await _make(store, tid)
    await store.delete(tenant_id=tid, name="github")
    assert await store.get(tenant_id=tid, name="github") is None


@pytest.mark.asyncio
async def test_delete_absent_raises() -> None:
    store = InMemoryTenantMcpServerStore()
    with pytest.raises(TenantMcpServerNotFoundError):
        await store.delete(tenant_id=uuid4(), name="nope")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/mac/src/github/jone_qian/helix-agent && uv run python -m pytest packages/helix-persistence/tests/test_in_memory_tenant_mcp_server_store.py -q`
Expected: FAIL — `ImportError: cannot import name 'InMemoryTenantMcpServerStore'`.

- [ ] **Step 3: Write the implementation**

Create `packages/helix-persistence/src/helix_agent/persistence/tenant_mcp_server/memory.py`:

```python
"""In-memory :class:`TenantMcpServerStore` — Stream V."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from uuid import UUID, uuid4

from helix_agent.protocol import (
    McpServerAuthType,
    McpServerTransport,
    TenantMcpServerPatch,
    TenantMcpServerRecord,
)

from helix_agent.persistence.tenant_mcp_server.base import (
    TenantMcpServerAlreadyExistsError,
    TenantMcpServerNotFoundError,
    TenantMcpServerStore,
)


def _now() -> datetime:
    return datetime.now(tz=UTC)


class InMemoryTenantMcpServerStore(TenantMcpServerStore):
    """Dict-backed store keyed by ``(tenant_id, name)``; lock-guarded."""

    def __init__(self) -> None:
        self._rows: dict[tuple[UUID, str], TenantMcpServerRecord] = {}
        self._lock = asyncio.Lock()

    async def create(
        self,
        *,
        tenant_id: UUID,
        name: str,
        transport: McpServerTransport,
        url: str,
        auth_type: McpServerAuthType,
        token_secret_ref: str | None,
        timeout_s: float,
        created_by: str,
    ) -> TenantMcpServerRecord:
        async with self._lock:
            key = (tenant_id, name)
            if key in self._rows:
                raise TenantMcpServerAlreadyExistsError(tenant_id=tenant_id, name=name)
            now = _now()
            record = TenantMcpServerRecord(
                id=uuid4(),
                tenant_id=tenant_id,
                name=name,
                transport=transport,
                url=url,
                auth_type=auth_type,
                token_secret_ref=token_secret_ref,
                timeout_s=timeout_s,
                enabled=True,
                created_at=now,
                updated_at=now,
                created_by=created_by,
            )
            self._rows[key] = record
            return record

    async def get(self, *, tenant_id: UUID, name: str) -> TenantMcpServerRecord | None:
        async with self._lock:
            return self._rows.get((tenant_id, name))

    async def list_for_tenant(self, *, tenant_id: UUID) -> list[TenantMcpServerRecord]:
        async with self._lock:
            rows = [r for (tid, _), r in self._rows.items() if tid == tenant_id]
        return sorted(rows, key=lambda r: r.name)

    async def update(
        self, *, tenant_id: UUID, name: str, patch: TenantMcpServerPatch
    ) -> TenantMcpServerRecord:
        async with self._lock:
            key = (tenant_id, name)
            existing = self._rows.get(key)
            if existing is None:
                raise TenantMcpServerNotFoundError(tenant_id=tenant_id, name=name)
            changes: dict[str, object] = {"updated_at": _now()}
            if patch.url is not None:
                changes["url"] = patch.url
            if patch.token_secret_ref is not None:
                changes["token_secret_ref"] = patch.token_secret_ref
            if patch.timeout_s is not None:
                changes["timeout_s"] = patch.timeout_s
            if patch.enabled is not None:
                changes["enabled"] = patch.enabled
            updated = existing.model_copy(update=changes)
            self._rows[key] = updated
            return updated

    async def delete(self, *, tenant_id: UUID, name: str) -> None:
        async with self._lock:
            key = (tenant_id, name)
            if key not in self._rows:
                raise TenantMcpServerNotFoundError(tenant_id=tenant_id, name=name)
            del self._rows[key]
```

- [ ] **Step 4: Wire into the package `__init__.py`**

Edit `packages/helix-persistence/src/helix_agent/persistence/tenant_mcp_server/__init__.py` to add the memory store import and `__all__` entry:

```python
from helix_agent.persistence.tenant_mcp_server.memory import (
    InMemoryTenantMcpServerStore,
)
```

Add `"InMemoryTenantMcpServerStore"` to `__all__`.

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd /Users/mac/src/github/jone_qian/helix-agent && uv run python -m pytest packages/helix-persistence/tests/test_in_memory_tenant_mcp_server_store.py -q`
Expected: PASS (all 9 tests).

- [ ] **Step 6: Commit**

```bash
git add packages/helix-persistence/src/helix_agent/persistence/tenant_mcp_server/memory.py \
        packages/helix-persistence/src/helix_agent/persistence/tenant_mcp_server/__init__.py \
        packages/helix-persistence/tests/test_in_memory_tenant_mcp_server_store.py
git commit -m "feat(stream-v): in-memory tenant MCP server store + unit tests (V-B)"
```

---

## Task 7: SQL store + RLS integration tests

**Files:**
- Create: `packages/helix-persistence/src/helix_agent/persistence/tenant_mcp_server/sql.py`
- Create: `packages/helix-persistence/tests/test_sql_tenant_mcp_server_store.py`

- [ ] **Step 1: Write the SQL store**

Create `packages/helix-persistence/src/helix_agent/persistence/tenant_mcp_server/sql.py`:

```python
"""Postgres-backed :class:`TenantMcpServerStore` — Stream V."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import delete as sa_delete
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from helix_agent.protocol import (
    McpServerAuthType,
    McpServerTransport,
    TenantMcpServerPatch,
    TenantMcpServerRecord,
)

from helix_agent.persistence.models import TenantMcpServerRow
from helix_agent.persistence.tenant_mcp_server.base import (
    TenantMcpServerAlreadyExistsError,
    TenantMcpServerNotFoundError,
    TenantMcpServerStore,
)


def _utc_now() -> datetime:
    return datetime.now(tz=UTC)


def _row_to_record(row: TenantMcpServerRow) -> TenantMcpServerRecord:
    return TenantMcpServerRecord(
        id=row.id,
        tenant_id=row.tenant_id,
        name=row.name,
        transport=row.transport,  # type: ignore[arg-type]
        url=row.url,
        auth_type=row.auth_type,  # type: ignore[arg-type]
        token_secret_ref=row.token_secret_ref,
        timeout_s=row.timeout_s,
        enabled=row.enabled,
        created_at=row.created_at,
        updated_at=row.updated_at,
        created_by=row.created_by,
    )


class SqlTenantMcpServerStore(TenantMcpServerStore):
    """Postgres-backed tenant MCP server registry (RLS-scoped sessions)."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory

    async def create(
        self,
        *,
        tenant_id: UUID,
        name: str,
        transport: McpServerTransport,
        url: str,
        auth_type: McpServerAuthType,
        token_secret_ref: str | None,
        timeout_s: float,
        created_by: str,
    ) -> TenantMcpServerRecord:
        now = _utc_now()
        stmt = (
            pg_insert(TenantMcpServerRow)
            .values(
                tenant_id=tenant_id,
                name=name,
                transport=transport,
                url=url,
                auth_type=auth_type,
                token_secret_ref=token_secret_ref,
                timeout_s=timeout_s,
                enabled=True,
                created_at=now,
                updated_at=now,
                created_by=created_by,
            )
            .returning(TenantMcpServerRow)
        )
        async with self._sf() as session:
            try:
                row = (await session.execute(stmt)).scalar_one()
                await session.commit()
            except IntegrityError as exc:
                await session.rollback()
                raise TenantMcpServerAlreadyExistsError(
                    tenant_id=tenant_id, name=name
                ) from exc
            await session.refresh(row)
            return _row_to_record(row)

    async def get(self, *, tenant_id: UUID, name: str) -> TenantMcpServerRecord | None:
        stmt = select(TenantMcpServerRow).where(
            TenantMcpServerRow.tenant_id == tenant_id,
            TenantMcpServerRow.name == name,
        )
        async with self._sf() as session:
            row = (await session.execute(stmt)).scalar_one_or_none()
        return _row_to_record(row) if row is not None else None

    async def list_for_tenant(self, *, tenant_id: UUID) -> list[TenantMcpServerRecord]:
        stmt = (
            select(TenantMcpServerRow)
            .where(TenantMcpServerRow.tenant_id == tenant_id)
            .order_by(TenantMcpServerRow.name)
        )
        async with self._sf() as session:
            rows = (await session.execute(stmt)).scalars().all()
        return [_row_to_record(r) for r in rows]

    async def update(
        self, *, tenant_id: UUID, name: str, patch: TenantMcpServerPatch
    ) -> TenantMcpServerRecord:
        async with self._sf() as session:
            stmt = select(TenantMcpServerRow).where(
                TenantMcpServerRow.tenant_id == tenant_id,
                TenantMcpServerRow.name == name,
            )
            existing = (await session.execute(stmt)).scalar_one_or_none()
            if existing is None:
                raise TenantMcpServerNotFoundError(tenant_id=tenant_id, name=name)
            if patch.url is not None:
                existing.url = patch.url
            if patch.token_secret_ref is not None:
                existing.token_secret_ref = patch.token_secret_ref
            if patch.timeout_s is not None:
                existing.timeout_s = patch.timeout_s
            if patch.enabled is not None:
                existing.enabled = patch.enabled
            existing.updated_at = _utc_now()
            await session.commit()
            await session.refresh(existing)
            return _row_to_record(existing)

    async def delete(self, *, tenant_id: UUID, name: str) -> None:
        stmt = (
            sa_delete(TenantMcpServerRow)
            .where(
                TenantMcpServerRow.tenant_id == tenant_id,
                TenantMcpServerRow.name == name,
            )
            .returning(TenantMcpServerRow.id)
        )
        async with self._sf() as session:
            deleted = (await session.execute(stmt)).scalar_one_or_none()
            await session.commit()
        if deleted is None:
            raise TenantMcpServerNotFoundError(tenant_id=tenant_id, name=name)
```

- [ ] **Step 2: Wire into the package `__init__.py`**

Edit `packages/helix-persistence/src/helix_agent/persistence/tenant_mcp_server/__init__.py` to add:

```python
from helix_agent.persistence.tenant_mcp_server.sql import SqlTenantMcpServerStore
```

Add `"SqlTenantMcpServerStore"` to `__all__`.

- [ ] **Step 3: Write the RLS integration tests**

Create `packages/helix-persistence/tests/test_sql_tenant_mcp_server_store.py`. **Mirror the fixture setup in `packages/helix-persistence/tests/test_sql_tenant_config_store.py`** (the `postgres_container` fixture, `_provision_app_role`, `build_rls_sessionmaker`, `create_async_engine_from_config`, and the `reset_rls` autouse fixture + `current_tenant_id_var` import). Reuse any shared `conftest.py` fixtures rather than duplicating. The test body:

```python
"""Integration (RLS) tests for the SQL tenant MCP server store.

Mirrors test_sql_tenant_config_store.py fixture setup (postgres_container,
app role provisioning, RLS sessionmaker, reset_rls). See that file for the
exact fixture wiring to copy.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from helix_agent.persistence.tenant_mcp_server import (
    SqlTenantMcpServerStore,
    TenantMcpServerAlreadyExistsError,
)
from helix_agent.protocol import TenantMcpServerPatch

# Import the RLS context var the same way test_sql_tenant_config_store.py does:
from helix_agent.persistence.tenant_scope import current_tenant_id_var  # adjust if path differs

pytestmark = pytest.mark.integration


# ---- Reuse fixture `tenant_mcp_server_store` analogous to `tenant_config_store`
# in test_sql_tenant_config_store.py: it runs `command.upgrade(cfg, "head")`,
# provisions the app role, and yields (SqlTenantMcpServerStore(sf), engine). ----


@pytest.mark.asyncio
async def test_create_get_round_trip(tenant_mcp_server_store) -> None:
    store, _engine = tenant_mcp_server_store
    tid = uuid4()
    current_tenant_id_var.set(tid)
    try:
        created = await store.create(
            tenant_id=tid,
            name="github",
            transport="streamable_http",
            url="https://mcp.example.com/mcp",
            auth_type="bearer",
            token_secret_ref="secret://helix-agent/t/mcp/github/token",
            timeout_s=30.0,
            created_by="admin@acme",
        )
        assert created.name == "github"
        got = await store.get(tenant_id=tid, name="github")
        assert got is not None and got.id == created.id
    finally:
        current_tenant_id_var.set(None)


@pytest.mark.asyncio
async def test_rls_isolation_between_tenants(tenant_mcp_server_store) -> None:
    store, _engine = tenant_mcp_server_store
    a, b = uuid4(), uuid4()

    current_tenant_id_var.set(a)
    try:
        await store.create(
            tenant_id=a, name="github", transport="streamable_http",
            url="https://a.example.com/mcp", auth_type="none",
            token_secret_ref=None, timeout_s=30.0, created_by="a@x",
        )
    finally:
        current_tenant_id_var.set(None)

    # Tenant B must NOT see tenant A's row.
    current_tenant_id_var.set(b)
    try:
        assert await store.get(tenant_id=a, name="github") is None
        assert await store.list_for_tenant(tenant_id=a) == []
    finally:
        current_tenant_id_var.set(None)


@pytest.mark.asyncio
async def test_duplicate_name_rejected(tenant_mcp_server_store) -> None:
    store, _engine = tenant_mcp_server_store
    tid = uuid4()
    current_tenant_id_var.set(tid)
    try:
        kwargs = dict(
            tenant_id=tid, name="github", transport="streamable_http",
            url="https://a.example.com/mcp", auth_type="none",
            token_secret_ref=None, timeout_s=30.0, created_by="a@x",
        )
        await store.create(**kwargs)
        with pytest.raises(TenantMcpServerAlreadyExistsError):
            await store.create(**kwargs)
    finally:
        current_tenant_id_var.set(None)


@pytest.mark.asyncio
async def test_update_and_delete(tenant_mcp_server_store) -> None:
    store, _engine = tenant_mcp_server_store
    tid = uuid4()
    current_tenant_id_var.set(tid)
    try:
        await store.create(
            tenant_id=tid, name="github", transport="streamable_http",
            url="https://a.example.com/mcp", auth_type="none",
            token_secret_ref=None, timeout_s=30.0, created_by="a@x",
        )
        updated = await store.update(
            tenant_id=tid, name="github",
            patch=TenantMcpServerPatch(enabled=False, url="https://b.example.com/mcp"),
        )
        assert updated.enabled is False
        assert updated.url == "https://b.example.com/mcp"
        await store.delete(tenant_id=tid, name="github")
        assert await store.get(tenant_id=tid, name="github") is None
    finally:
        current_tenant_id_var.set(None)
```

- [ ] **Step 4: Run the integration tests**

Run: `cd /Users/mac/src/github/jone_qian/helix-agent && uv run python -m pytest packages/helix-persistence/tests/test_sql_tenant_mcp_server_store.py -q`
Expected: PASS (requires Docker for testcontainers). If Docker is unavailable locally, note that CI's `Test (integration)` job runs these; verify the fixture wiring compiles by running `uv run python -m pytest packages/helix-persistence/tests/test_sql_tenant_mcp_server_store.py --collect-only -q` (collection must succeed).

- [ ] **Step 5: Commit**

```bash
git add packages/helix-persistence/src/helix_agent/persistence/tenant_mcp_server/sql.py \
        packages/helix-persistence/src/helix_agent/persistence/tenant_mcp_server/__init__.py \
        packages/helix-persistence/tests/test_sql_tenant_mcp_server_store.py
git commit -m "feat(stream-v): SQL tenant MCP server store + RLS integration tests (V-B)"
```

---

## Task 8: Top-level persistence package exports

**Files:**
- Modify: `packages/helix-persistence/src/helix_agent/persistence/__init__.py`

- [ ] **Step 1: Add the store exports**

In `packages/helix-persistence/src/helix_agent/persistence/__init__.py`, mirror the `tenant_config` export block (around line 124). Add:

```python
from helix_agent.persistence.tenant_mcp_server import (
    InMemoryTenantMcpServerStore as InMemoryTenantMcpServerStore,
)
from helix_agent.persistence.tenant_mcp_server import (
    SqlTenantMcpServerStore as SqlTenantMcpServerStore,
)
from helix_agent.persistence.tenant_mcp_server import (
    TenantMcpServerAlreadyExistsError as TenantMcpServerAlreadyExistsError,
)
from helix_agent.persistence.tenant_mcp_server import (
    TenantMcpServerNotFoundError as TenantMcpServerNotFoundError,
)
from helix_agent.persistence.tenant_mcp_server import (
    TenantMcpServerStore as TenantMcpServerStore,
)
```

Add these five names to the `__all__` list (keep alphabetical grouping consistent with neighbors).

- [ ] **Step 2: Verify top-level imports**

Run: `cd /Users/mac/src/github/jone_qian/helix-agent && uv run python -c "from helix_agent.persistence import SqlTenantMcpServerStore, InMemoryTenantMcpServerStore, TenantMcpServerStore; print('ok')"`
Expected: prints `ok`.

- [ ] **Step 3: Commit**

```bash
git add packages/helix-persistence/src/helix_agent/persistence/__init__.py
git commit -m "feat(stream-v): export tenant MCP server store from persistence package (V-B)"
```

---

## Task 9: Preflight + push + PR

- [ ] **Step 1: Run the full unit test scope**

Run: `cd /Users/mac/src/github/jone_qian/helix-agent && uv run python -m pytest -m "not integration" packages/helix-common packages/helix-protocol packages/helix-persistence -q`
Expected: PASS (new unit tests green; no regressions).

- [ ] **Step 2: Lint + type preflight** ([memory:feedback_ruff_strict_lint_traps], [memory:feedback_uv_lock_and_precommit_ruff])

Run: `cd /Users/mac/src/github/jone_qian/helix-agent && uv run ruff check packages/helix-common packages/helix-protocol packages/helix-persistence && uv run ruff format --check packages/helix-common packages/helix-protocol packages/helix-persistence`
Expected: no violations. Fix any (common traps: UP038 PEP-604 unions, RUF002 Chinese punctuation in docstrings, unused `type: ignore`).

Run: `cd /Users/mac/src/github/jone_qian/helix-agent && uv run mypy packages/helix-common/src packages/helix-protocol/src packages/helix-persistence/src`
Expected: clean (note: mypy may flag the `# type: ignore[arg-type]` on `transport=`/`auth_type=` in `_row_to_record` — those are intentional Literal narrowings; keep only if mypy actually requires them, else remove to avoid the unused-ignore error).

Run: `cd /Users/mac/src/github/jone_qian/helix-agent && pre-commit run --all-files` (catches the lint config the local commands may miss; if not installed, skip).

- [ ] **Step 3: Confirm no uv.lock drift**

Run: `cd /Users/mac/src/github/jone_qian/helix-agent && git status --short` — expect no `uv.lock` change (this PR adds no dependencies). If `uv.lock` changed, investigate before pushing.

- [ ] **Step 4: Push and open the PR**

```bash
cd /Users/mac/src/github/jone_qian/helix-agent
git push -u origin stream-v/b-registry
gh pr create --base main --head stream-v/b-registry \
  --title "feat(stream-v): PR B — Tenant MCP Server Registry (persistence + SSRF guard)" \
  --body "Implements Stream V-B per docs/streams/STREAM-V-DESIGN.md (Mini-ADR V-1, V-7).

## What
- \`tenant_mcp_server\` table (RLS) + ORM model + migration 0054
- base/sql/memory store triple (CRUD)
- \`TenantMcpServerRecord\`/\`TenantMcpServerPatch\` protocol types
- \`validate_remote_url\` SSRF guard in helix-common (rejects private/loopback/link-local/metadata IPs + non-http(s) schemes)

## Scope
Persistence + util only. API (V-C), runtime (V-D), schema (V-E), UI (V-F/G) are separate PRs.

## Tests
- Unit: url_validation, record validators, in-memory store CRUD
- Integration (RLS): SQL store round-trip, cross-tenant isolation, dup-name, update/delete

🤖 Generated with Claude Code"
```

- [ ] **Step 5: Poll CI to green**

Run: `gh pr checks <PR#> --watch` (or poll). All checks must pass before handing off to V-C. Address any failures ([memory:reference_ci_lint_type_test_scopes]: integration tests run in CI's `Test (integration)` job).

---

## Self-Review (completed by plan author)

**Spec coverage (vs STREAM-V-DESIGN.md):**
- V-1 (registry table base/sql/memory + protocol type) → Tasks 2,3,4,5,6,7,8 ✓
- V-7 (SSRF util, used at all connect-out sites) → Task 1 (util only; registration/probe/runtime callers wired in V-C/V-D) ✓
- token-only-ref (V-2) → table has `token_secret_ref`, no plaintext column ✓
- transport rejects stdio → CHECK constraint + `McpServerTransport` literal ✓
- All other Mini-ADRs (V-3 schema, V-4 runtime, V-5 API, V-6 discovery, V-8/9 UI, V-10 audit) are explicitly out of V-B scope (separate PRs). ✓

**Placeholder scan:** No TBD/TODO; every code step has complete code. The one deferred detail is the integration-test fixture wiring, which instructs copying the verified existing `test_sql_tenant_config_store.py` pattern (the exact fixtures already exist in-repo) rather than inventing — acceptable since the engineer copies a real file.

**Type consistency:** `TenantMcpServerRecord`/`TenantMcpServerPatch`/`TenantMcpServerStore`/`TenantMcpServerRow`/`InMemoryTenantMcpServerStore`/`SqlTenantMcpServerStore` and method signatures (`create`/`get`/`list_for_tenant`/`update`/`delete`) are consistent across base, memory, sql, and tests. `validate_remote_url(url, *, allowed_schemes)` consistent.

**Known follow-ups (not V-B):** DNS-rebind SSRF defense (resolve-then-recheck) noted in util docstring; auth-type change via update is intentionally unsupported (delete+recreate).
