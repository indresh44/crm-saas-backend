"""Per-enquiry computed intelligence: requirement_summary, demand_tags, activity_summary.

  1. leads
        - requirement_summary           text nullable
        - activity_summary              text nullable
        - last_activity_id_summarized   FK -> lead_activities(id) ON DELETE SET NULL
        - summary_updated_at            timestamptz nullable
  2. demand_tags
        - id (UUID PK), business_id (FK), name (text, stored lowercase+trimmed),
          origin enum('ai','owner') default 'ai', created_at
        - UNIQUE(business_id, name)
  3. enquiry_demand_tags  (link table)
        - enquiry_id (FK leads), demand_tag_id (FK demand_tags), PK(both)

No embedding column on demand_tags yet — leave room for a future column
(e.g. for similarity dedupe across slight phrasing differences).

Revision ID: 0045_enquiry_intelligence
Revises: 0044_followup_outcomes
Create Date: 2026-06-01
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import UUID


revision = "0045_enquiry_intelligence"
down_revision = "0044_followup_outcomes"
branch_labels = None
depends_on = None


_DEMAND_TAG_ORIGIN_VALUES = ("ai", "owner")


def upgrade() -> None:
    # --- 1. leads: per-enquiry summary columns ---
    op.add_column(
        "leads",
        sa.Column("requirement_summary", sa.Text(), nullable=True),
    )
    op.add_column(
        "leads",
        sa.Column("activity_summary", sa.Text(), nullable=True),
    )
    op.add_column(
        "leads",
        sa.Column(
            "last_activity_id_summarized",
            UUID(as_uuid=True),
            nullable=True,
        ),
    )
    op.create_foreign_key(
        "fk_leads_last_activity_id_summarized",
        "leads",
        "lead_activities",
        ["last_activity_id_summarized"],
        ["id"],
        ondelete="SET NULL",
    )
    op.add_column(
        "leads",
        sa.Column(
            "summary_updated_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )

    # --- 2. demand_tag_origin enum + demand_tags table ---
    values_sql = ", ".join(f"'{v}'" for v in _DEMAND_TAG_ORIGIN_VALUES)
    op.execute(f"CREATE TYPE demand_tag_origin AS ENUM ({values_sql})")

    op.create_table(
        "demand_tags",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "business_id",
            UUID(as_uuid=True),
            sa.ForeignKey("businesses.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column(
            "origin",
            postgresql.ENUM(
                *_DEMAND_TAG_ORIGIN_VALUES,
                name="demand_tag_origin",
                create_type=False,
            ),
            nullable=False,
            server_default=sa.text("'ai'::demand_tag_origin"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "business_id", "name", name="uq_demand_tags_business_name",
        ),
    )

    # --- 3. enquiry_demand_tags link table ---
    op.create_table(
        "enquiry_demand_tags",
        sa.Column(
            "enquiry_id",
            UUID(as_uuid=True),
            sa.ForeignKey("leads.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "demand_tag_id",
            UUID(as_uuid=True),
            sa.ForeignKey("demand_tags.id", ondelete="CASCADE"),
            primary_key=True,
        ),
    )
    op.create_index(
        "ix_enquiry_demand_tags_tag",
        "enquiry_demand_tags",
        ["demand_tag_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_enquiry_demand_tags_tag", table_name="enquiry_demand_tags")
    op.drop_table("enquiry_demand_tags")
    op.drop_table("demand_tags")
    op.execute("DROP TYPE demand_tag_origin")

    op.drop_constraint(
        "fk_leads_last_activity_id_summarized", "leads", type_="foreignkey",
    )
    op.drop_column("leads", "summary_updated_at")
    op.drop_column("leads", "last_activity_id_summarized")
    op.drop_column("leads", "activity_summary")
    op.drop_column("leads", "requirement_summary")
