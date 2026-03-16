"""whatsapp tables

Revision ID: 0003_whatsapp_tables
Revises: 0002_expand_crm_schema
Create Date: 2026-03-15

"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0003_whatsapp_tables"
down_revision = "0002_expand_crm_schema"
branch_labels = None
depends_on = None


whatsapp_message_direction = postgresql.ENUM(
    "incoming",
    "outgoing",
    name="whatsapp_message_direction",
    create_type=False,
)
whatsapp_message_type = postgresql.ENUM(
    "text",
    "image",
    "document",
    "audio",
    "video",
    "interactive",
    "template",
    "unknown",
    name="whatsapp_message_type",
    create_type=False,
)
whatsapp_message_status = postgresql.ENUM(
    "pending",
    "accepted",
    "sent",
    "delivered",
    "read",
    "failed",
    name="whatsapp_message_status",
    create_type=False,
)
whatsapp_message_event_type = postgresql.ENUM(
    "webhook_received",
    "sent",
    "delivered",
    "read",
    "failed",
    name="whatsapp_message_event_type",
    create_type=False,
)


def upgrade() -> None:
    # Create ENUM types (create_type=True so they are created when used in tables)
    bind = op.get_bind()
    whatsapp_message_direction.create(bind, checkfirst=True)
    whatsapp_message_type.create(bind, checkfirst=True)
    whatsapp_message_status.create(bind, checkfirst=True)
    whatsapp_message_event_type.create(bind, checkfirst=True)

    op.create_table(
        "whatsapp_accounts",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("business_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("meta_app_id", sa.String(), nullable=True),
        sa.Column("waba_id", sa.String(), nullable=True),
        sa.Column("phone_number_id", sa.String(), nullable=False),
        sa.Column("display_phone_number", sa.String(), nullable=True),
        sa.Column("verified_name", sa.String(), nullable=True),
        sa.Column("access_token", sa.String(), nullable=False),
        sa.Column("token_type", sa.String(), nullable=True),
        sa.Column("webhook_verify_token", sa.String(), nullable=False),
        sa.Column("app_secret", sa.String(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["business_id"], ["businesses.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_whatsapp_accounts_business_id"),
        "whatsapp_accounts",
        ["business_id"],
        unique=False,
    )
    op.create_index(
        "ix_whatsapp_accounts_business_active",
        "whatsapp_accounts",
        ["business_id", "is_active"],
        unique=False,
    )
    op.create_unique_constraint(
        "uq_whatsapp_accounts_phone_number_id",
        "whatsapp_accounts",
        ["phone_number_id"],
    )

    op.create_table(
        "whatsapp_conversations",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("business_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("whatsapp_account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("customer_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("lead_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("phone_number", sa.String(), nullable=False),
        sa.Column("contact_name", sa.String(), nullable=True),
        sa.Column("last_message_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_incoming_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_outgoing_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_blocked", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["business_id"], ["businesses.id"]),
        sa.ForeignKeyConstraint(["customer_id"], ["customers.id"]),
        sa.ForeignKeyConstraint(["lead_id"], ["leads.id"]),
        sa.ForeignKeyConstraint(["whatsapp_account_id"], ["whatsapp_accounts.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_whatsapp_conversations_business_id"),
        "whatsapp_conversations",
        ["business_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_whatsapp_conversations_whatsapp_account_id"),
        "whatsapp_conversations",
        ["whatsapp_account_id"],
        unique=False,
    )
    op.create_index(
        "ix_whatsapp_conversations_business_phone",
        "whatsapp_conversations",
        ["business_id", "phone_number"],
        unique=False,
    )
    op.create_index(
        "ix_whatsapp_conversations_business_lead",
        "whatsapp_conversations",
        ["business_id", "lead_id"],
        unique=False,
    )
    op.create_index(
        "ix_whatsapp_conversations_business_customer",
        "whatsapp_conversations",
        ["business_id", "customer_id"],
        unique=False,
    )
    op.create_index(
        "ix_whatsapp_conversations_business_last_message",
        "whatsapp_conversations",
        ["business_id", "last_message_at"],
        unique=False,
    )

    op.create_table(
        "whatsapp_messages",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("business_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("whatsapp_account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("customer_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("lead_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("whatsapp_message_id", sa.String(), nullable=True),
        sa.Column("direction", whatsapp_message_direction, nullable=False),
        sa.Column("message_type", whatsapp_message_type, nullable=False),
        sa.Column("text_body", sa.String(), nullable=True),
        sa.Column("media_url", sa.String(), nullable=True),
        sa.Column("mime_type", sa.String(), nullable=True),
        sa.Column("file_name", sa.String(), nullable=True),
        sa.Column("caption", sa.String(), nullable=True),
        sa.Column("status", whatsapp_message_status, nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_message", sa.String(), nullable=True),
        sa.Column("raw_payload", postgresql.JSON(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["business_id"], ["businesses.id"]),
        sa.ForeignKeyConstraint(["conversation_id"], ["whatsapp_conversations.id"]),
        sa.ForeignKeyConstraint(["customer_id"], ["customers.id"]),
        sa.ForeignKeyConstraint(["lead_id"], ["leads.id"]),
        sa.ForeignKeyConstraint(["whatsapp_account_id"], ["whatsapp_accounts.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_whatsapp_messages_business_id"),
        "whatsapp_messages",
        ["business_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_whatsapp_messages_conversation_id"),
        "whatsapp_messages",
        ["conversation_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_whatsapp_messages_whatsapp_account_id"),
        "whatsapp_messages",
        ["whatsapp_account_id"],
        unique=False,
    )
    op.create_index(
        "ix_whatsapp_messages_conversation_created",
        "whatsapp_messages",
        ["conversation_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_whatsapp_messages_business_created",
        "whatsapp_messages",
        ["business_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_whatsapp_messages_whatsapp_message_id",
        "whatsapp_messages",
        ["whatsapp_message_id"],
        unique=False,
    )

    op.create_table(
        "whatsapp_message_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("business_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("whatsapp_message_id_ref", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_type", whatsapp_message_event_type, nullable=False),
        sa.Column("payload", postgresql.JSON(astext_type=sa.Text()), nullable=True),
        sa.Column("event_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["business_id"], ["businesses.id"]),
        sa.ForeignKeyConstraint(
            ["whatsapp_message_id_ref"],
            ["whatsapp_messages.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_whatsapp_message_events_business_id"),
        "whatsapp_message_events",
        ["business_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_whatsapp_message_events_whatsapp_message_id_ref"),
        "whatsapp_message_events",
        ["whatsapp_message_id_ref"],
        unique=False,
    )
    op.create_index(
        "ix_whatsapp_message_events_message_event_at",
        "whatsapp_message_events",
        ["whatsapp_message_id_ref", "event_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_whatsapp_message_events_message_event_at",
        table_name="whatsapp_message_events",
    )
    op.drop_index(
        op.f("ix_whatsapp_message_events_whatsapp_message_id_ref"),
        table_name="whatsapp_message_events",
    )
    op.drop_index(
        op.f("ix_whatsapp_message_events_business_id"),
        table_name="whatsapp_message_events",
    )
    op.drop_table("whatsapp_message_events")

    op.drop_index("ix_whatsapp_messages_whatsapp_message_id", table_name="whatsapp_messages")
    op.drop_index("ix_whatsapp_messages_business_created", table_name="whatsapp_messages")
    op.drop_index("ix_whatsapp_messages_conversation_created", table_name="whatsapp_messages")
    op.drop_index(
        op.f("ix_whatsapp_messages_whatsapp_account_id"),
        table_name="whatsapp_messages",
    )
    op.drop_index(
        op.f("ix_whatsapp_messages_conversation_id"),
        table_name="whatsapp_messages",
    )
    op.drop_index(op.f("ix_whatsapp_messages_business_id"), table_name="whatsapp_messages")
    op.drop_table("whatsapp_messages")

    op.drop_index(
        "ix_whatsapp_conversations_business_last_message",
        table_name="whatsapp_conversations",
    )
    op.drop_index(
        "ix_whatsapp_conversations_business_customer",
        table_name="whatsapp_conversations",
    )
    op.drop_index(
        "ix_whatsapp_conversations_business_lead",
        table_name="whatsapp_conversations",
    )
    op.drop_index(
        "ix_whatsapp_conversations_business_phone",
        table_name="whatsapp_conversations",
    )
    op.drop_index(
        op.f("ix_whatsapp_conversations_whatsapp_account_id"),
        table_name="whatsapp_conversations",
    )
    op.drop_index(
        op.f("ix_whatsapp_conversations_business_id"),
        table_name="whatsapp_conversations",
    )
    op.drop_table("whatsapp_conversations")

    op.drop_constraint(
        "uq_whatsapp_accounts_phone_number_id",
        "whatsapp_accounts",
        type_="unique",
    )
    op.drop_index(
        "ix_whatsapp_accounts_business_active",
        table_name="whatsapp_accounts",
    )
    op.drop_index(
        op.f("ix_whatsapp_accounts_business_id"),
        table_name="whatsapp_accounts",
    )
    op.drop_table("whatsapp_accounts")

    whatsapp_message_event_type.drop(op.get_bind(), checkfirst=True)
    whatsapp_message_status.drop(op.get_bind(), checkfirst=True)
    whatsapp_message_type.drop(op.get_bind(), checkfirst=True)
    whatsapp_message_direction.drop(op.get_bind(), checkfirst=True)
