"""Prepared-action row — server-side store for the assistant write surface.

The atomic single-use guarantee lives in ``app/write_surface/prepared_actions.py``
(``consume_prepared_action``), which flips status from 'pending' to 'consumed' in
ONE conditional SQL UPDATE — see that module's docstring.

This model is kept under ``app/models/`` so Alembic discovery and the model-init
cascade pick it up consistently with every other table.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, Column, Index, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel


# Status sentinels — module-level so the store + tests can import them.
# Stored as a plain VARCHAR (not a Postgres enum) so the atomic UPDATE works on
# any equality comparison; CHECK constraint guards values at write time. Same
# pattern as ``invoice_adjustments.adjustment_type``.
STATUS_PENDING = "pending"
STATUS_CONSUMED = "consumed"


class PreparedAction(SQLModel, table=True):
    """A pending or consumed write that the assistant prepared for human review.

    The ``id`` is an opaque token (``secrets.token_urlsafe(32)`` — ~43 chars) and
    is the ONLY handle a client ever receives. Tenant scoping is enforced on
    every consume by including ``business_id`` in the conditional UPDATE.
    """

    __tablename__ = "prepared_actions"
    __table_args__ = (
        CheckConstraint(
            f"status IN ('{STATUS_PENDING}', '{STATUS_CONSUMED}')",
            name="ck_prepared_actions_status",
        ),
        # Helps the cleanup function (delete old rows) and any future
        # admin/inspection query for pending-not-yet-expired rows.
        Index("ix_prepared_actions_status_expires", "status", "expires_at"),
    )

    id: str = Field(primary_key=True, max_length=64)
    business_id: uuid.UUID = Field(foreign_key="businesses.id", index=True, nullable=False)
    capability: str = Field(max_length=64, index=True, nullable=False)

    locked_data: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(JSONB, nullable=False),
    )
    editable_data: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(JSONB, nullable=False),
    )
    preview: str = Field(sa_column=Column(Text, nullable=False))

    status: str = Field(max_length=16, default=STATUS_PENDING, nullable=False)
    created_at: datetime = Field(nullable=False)
    expires_at: datetime = Field(nullable=False)
