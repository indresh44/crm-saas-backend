"""Add prepared_actions table (write-surface foundation)

Revision ID: 0039_prepared_actions
Revises: 0038_lead_act_attach_entity
Create Date: 2026-05-23 00:00:00
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID
from alembic import op


revision = "0039_prepared_actions"
down_revision = "0038_lead_act_attach_entity"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "prepared_actions",
        sa.Column("id", sa.String(length=64), primary_key=True, nullable=False),
        sa.Column(
            "business_id",
            UUID(as_uuid=True),
            sa.ForeignKey("businesses.id"),
            nullable=False,
        ),
        sa.Column("capability", sa.String(length=64), nullable=False),
        sa.Column("locked_data", JSONB, nullable=False),
        sa.Column("editable_data", JSONB, nullable=False),
        sa.Column("preview", sa.Text, nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('pending', 'consumed')",
            name="ck_prepared_actions_status",
        ),
    )
    op.create_index(
        "ix_prepared_actions_business_id",
        "prepared_actions",
        ["business_id"],
    )
    op.create_index(
        "ix_prepared_actions_capability",
        "prepared_actions",
        ["capability"],
    )
    op.create_index(
        "ix_prepared_actions_status_expires",
        "prepared_actions",
        ["status", "expires_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_prepared_actions_status_expires", table_name="prepared_actions")
    op.drop_index("ix_prepared_actions_capability", table_name="prepared_actions")
    op.drop_index("ix_prepared_actions_business_id", table_name="prepared_actions")
    op.drop_table("prepared_actions")
