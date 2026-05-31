"""Add wa_credentials and wa_messages tables for the new WhatsApp webhook ingest.

Separate from the legacy whatsapp_accounts / whatsapp_messages tables — this is
the lean Wa* schema that backs the new hand-rolled webhook (no pywa yet).

Revision ID: 0043_add_wa_credentials_and_wa_messages
Revises: 0042_lead_activity_actor_payload
Create Date: 2026-05-27
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0043_add_wa_ingest_tables"
down_revision = "0042_lead_activity_actor_payload"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "wa_credentials",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("business_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("waba_id", sa.String(), nullable=False),
        sa.Column("phone_number_id", sa.String(), nullable=False),
        sa.Column("display_phone", sa.String(), nullable=False),
        sa.Column("access_token_enc", sa.LargeBinary(), nullable=False),
        sa.Column(
            "token_status",
            sa.String(),
            nullable=False,
            server_default=sa.text("'active'"),
        ),
        sa.Column("onboarded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["business_id"], ["businesses.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("business_id", name="uq_wa_credentials_business_id"),
        sa.UniqueConstraint("phone_number_id", name="uq_wa_credentials_phone_number_id"),
    )
    op.create_index(
        "ix_wa_credentials_phone_number_id",
        "wa_credentials",
        ["phone_number_id"],
        unique=False,
    )

    op.create_table(
        "wa_messages",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("business_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("wamid", sa.String(), nullable=False),
        sa.Column("direction", sa.String(), nullable=False),
        sa.Column("lead_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("contact_phone", sa.String(), nullable=False),
        sa.Column("msg_type", sa.String(), nullable=False),
        sa.Column("body", sa.String(), nullable=True),
        sa.Column("media_url", sa.String(), nullable=True),
        sa.Column("template_name", sa.String(), nullable=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("error_detail", sa.String(), nullable=True),
        sa.Column(
            "is_owner_echo",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("wa_timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["business_id"], ["businesses.id"]),
        sa.ForeignKeyConstraint(["lead_id"], ["leads.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("wamid", name="uq_wa_messages_wamid"),
    )
    op.create_index(
        "ix_wa_messages_business_id",
        "wa_messages",
        ["business_id"],
        unique=False,
    )
    op.create_index(
        "ix_wa_messages_lead_id",
        "wa_messages",
        ["lead_id"],
        unique=False,
    )
    op.create_index(
        "ix_wa_messages_contact_phone",
        "wa_messages",
        ["contact_phone"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_wa_messages_contact_phone", table_name="wa_messages")
    op.drop_index("ix_wa_messages_lead_id", table_name="wa_messages")
    op.drop_index("ix_wa_messages_business_id", table_name="wa_messages")
    op.drop_table("wa_messages")

    op.drop_index("ix_wa_credentials_phone_number_id", table_name="wa_credentials")
    op.drop_table("wa_credentials")
