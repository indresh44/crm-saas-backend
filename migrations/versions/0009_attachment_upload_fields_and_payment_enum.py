"""add attachment upload fields and payment entity type

Revision ID: 0009_attachment_upload
Revises: 0008_merge_0007_heads
Create Date: 2026-03-19 00:00:00
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0009_attachment_upload"
down_revision = "0008_merge_0007_heads"
branch_labels = None
depends_on = None


old_attachment_entity_type = postgresql.ENUM(
    "lead",
    "quote",
    "invoice",
    "task",
    name="attachment_entity_type_old",
    create_type=False,
)

def upgrade() -> None:
    op.execute("ALTER TYPE attachment_entity_type ADD VALUE IF NOT EXISTS 'payment'")

    op.add_column("attachments", sa.Column("filename", sa.String(), nullable=True))
    op.add_column("attachments", sa.Column("file_size", sa.Integer(), nullable=True))

    op.execute("UPDATE attachments SET filename = file_url WHERE filename IS NULL")
    op.execute("UPDATE attachments SET file_size = 0 WHERE file_size IS NULL")

    op.alter_column("attachments", "filename", existing_type=sa.String(), nullable=False)
    op.alter_column("attachments", "file_size", existing_type=sa.Integer(), nullable=False)


def downgrade() -> None:
    op.drop_column("attachments", "file_size")
    op.drop_column("attachments", "filename")

    bind = op.get_bind()
    old_attachment_entity_type.create(bind, checkfirst=False)

    op.execute(
        """
        ALTER TABLE attachments
        ALTER COLUMN entity_type TYPE attachment_entity_type_old
        USING entity_type::text::attachment_entity_type_old
        """
    )
    op.execute(
        """
        ALTER TABLE notifications
        ALTER COLUMN entity_type TYPE attachment_entity_type_old
        USING entity_type::text::attachment_entity_type_old
        """
    )

    op.execute("ALTER TYPE attachment_entity_type RENAME TO attachment_entity_type_new")
    op.execute("ALTER TYPE attachment_entity_type_old RENAME TO attachment_entity_type")
    op.execute("DROP TYPE attachment_entity_type_new")
