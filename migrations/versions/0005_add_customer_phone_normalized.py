"""add customer phone normalized

Revision ID: 0005_customer_phone_norm
Revises: 0004_make_lead_customer_optional
Create Date: 2026-03-17 00:00:00
"""

from alembic import op
import sqlalchemy as sa


revision = "0005_customer_phone_norm"
down_revision = "0004_make_lead_customer_optional"
branch_labels = None
depends_on = None


OLD_INDEX_NAME = "ix_customers_business_phone"
NEW_INDEX_NAME = "ix_customers_business_phone_normalized"


def upgrade() -> None:
    op.add_column("customers", sa.Column("phone_normalized", sa.Text(), nullable=True))
    op.alter_column(
        "customers",
        "phone_normalized",
        existing_type=sa.String(),
        type_=sa.Text(),
        postgresql_using="phone_normalized::text",
    )

    op.execute(
        """
        UPDATE customers
        SET phone_normalized = CASE
            WHEN phone LIKE '+%' THEN '+' || regexp_replace(substr(phone, 2), '[^0-9]', '', 'g')
            ELSE regexp_replace(phone, '[^0-9]', '', 'g')
        END
        """
    )

    op.alter_column("customers", "phone_normalized", existing_type=sa.Text(), nullable=False)
    op.drop_index(OLD_INDEX_NAME, table_name="customers")
    op.create_index(NEW_INDEX_NAME, "customers", ["business_id", "phone_normalized"], unique=False)


def downgrade() -> None:
    op.drop_index(NEW_INDEX_NAME, table_name="customers")
    op.create_index(OLD_INDEX_NAME, "customers", ["business_id", "phone"], unique=False)
    op.drop_column("customers", "phone_normalized")
