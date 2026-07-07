"""Encrypted-secret vault ORM model — Stream Q (Mini-ADR Q-1/Q-2/Q-3).

The storage table behind :class:`SqlEncryptedSecretStore` (a ``SecretStore``
backend). Unlike ``platform_secret`` (which holds only ``secret://`` refs),
this table holds the actual secret **values** — but only as AES-256-GCM
**ciphertext**, never plaintext (Mini-ADR Q-6). A platform admin pastes a raw
key in the web UI; the write path encrypts it and stores a row here, while the
``platform_provider_secret`` catalog keeps pointing at a ``secret://<name>``
ref. ``secret_store.get(ref)`` then decrypts on read — the resolution chain is
unchanged.

Scope axis (Mini-ADR Q-3): ``tenant_id`` is NULLABLE. ``NULL`` = a
platform-global secret (this iteration's only writer; accessed via
``bypass_rls_session()``); a non-NULL ``tenant_id`` = a tenant-scoped secret
(reserved for a later iteration). RLS is declared in the migration so future
tenant rows are isolated by default.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import Boolean, DateTime, LargeBinary, Text, func, text
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from expert_work.persistence.base import Base


class EncryptedSecretRow(Base):
    """One envelope-encrypted secret version.

    ``(tenant_id, name, version)`` is unique; exactly one row per
    ``(tenant_id, name)`` carries ``is_current = true`` — that is the version
    ``get(name)`` returns. Re-pasting a key inserts a new version and flips the
    prior current row off (Mini-ADR Q-4).
    """

    __tablename__ = "encrypted_secret"

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    #: NULL = platform-global; non-NULL = tenant-scoped (reserved, Mini-ADR Q-3).
    tenant_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    #: Opaque secret name — the ``parse_secret_ref`` output of the catalog ref.
    name: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[str] = mapped_column(Text, nullable=False)
    #: AES-256-GCM ciphertext (includes the GCM tag). Never plaintext.
    ciphertext: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    #: 96-bit nonce, fresh per encryption (``os.urandom(12)``).
    nonce: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    #: Which KEK encrypted this row (e.g. ``"env-v1"``) — enables rotation.
    kek_version: Mapped[str] = mapped_column(Text, nullable=False)
    is_current: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    created_by: Mapped[str] = mapped_column(Text, nullable=False)
