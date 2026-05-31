"""Follow-up outcome system: status enum, attempt tracking, lead contact freshness.

  1. lead_followups
        - status            String → followup_status enum (pending|done|cancelled)
        - attempt_count     int NOT NULL default 0
        - completed_at      already exists from 0006 (no-op)
        - last_outcome      varchar nullable
  2. lead_activities
        - followup_id       FK -> lead_followups(id) ON DELETE SET NULL, indexed
        - new composite index (lead_id, created_at DESC) for the timeline read
  3. leads
        - last_contacted_at timestamptz nullable
        - phone_flagged     bool NOT NULL default false
  4. New composite index lead_followups (lead_id, status)

Notes:
  * `status` on lead_followups was a free-form sa.String since 0006. We
    convert it in place to a real Postgres enum with a USING cast — rows
    today only hold 'pending' / 'done' / 'cancelled' so the cast is safe.
  * Outcome / ResultAction live in payload JSON only — no DB enum.
  * lead_activities.lead_id is already singly indexed (Field index=True
    in the model); the new composite is additive for the diary read path.

Revision ID: 0044_followup_outcomes
Revises: 0043_add_wa_ingest_tables
Create Date: 2026-05-30
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import UUID

revision = "0044_followup_outcomes"
down_revision = "0043_add_wa_ingest_tables"
branch_labels = None
depends_on = None


_FOLLOWUP_STATUS_VALUES = ("pending", "done", "cancelled")


def upgrade() -> None:
    # --- 1. followup_status enum ---
    values_sql = ", ".join(f"'{v}'" for v in _FOLLOWUP_STATUS_VALUES)
    op.execute(f"CREATE TYPE followup_status AS ENUM ({values_sql})")

    # --- 2. lead_followups: convert status String → enum, add new columns ---
    # Drop the old text default first; alter type; reattach typed default.
    op.execute("ALTER TABLE lead_followups ALTER COLUMN status DROP DEFAULT")
    op.execute(
        "ALTER TABLE lead_followups "
        "ALTER COLUMN status TYPE followup_status "
        "USING status::followup_status"
    )
    op.execute(
        "ALTER TABLE lead_followups "
        "ALTER COLUMN status SET DEFAULT 'pending'::followup_status"
    )
    # status was already NOT NULL since 0006 — no nullability change needed.

    op.add_column(
        "lead_followups",
        sa.Column(
            "attempt_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.add_column(
        "lead_followups",
        sa.Column("last_outcome", sa.String(), nullable=True),
    )

    op.create_index(
        "ix_lead_followups_lead_status",
        "lead_followups",
        ["lead_id", "status"],
        unique=False,
    )

    # --- 3. lead_activities: followup_id FK + composite timeline index ---
    op.add_column(
        "lead_activities",
        sa.Column("followup_id", UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_lead_activities_followup",
        "lead_activities", "lead_followups",
        ["followup_id"], ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_lead_activities_followup_id",
        "lead_activities",
        ["followup_id"],
        unique=False,
    )
    op.create_index(
        "ix_lead_activities_lead_created_desc",
        "lead_activities",
        ["lead_id", sa.text("created_at DESC")],
        unique=False,
    )

    # --- 4. leads: contact freshness + phone validity flag ---
    op.add_column(
        "leads",
        sa.Column(
            "last_contacted_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.add_column(
        "leads",
        sa.Column(
            "phone_flagged",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )


def downgrade() -> None:
    # --- leads ---
    op.drop_column("leads", "phone_flagged")
    op.drop_column("leads", "last_contacted_at")

    # --- lead_activities ---
    op.drop_index(
        "ix_lead_activities_lead_created_desc", table_name="lead_activities"
    )
    op.drop_index(
        "ix_lead_activities_followup_id", table_name="lead_activities"
    )
    op.drop_constraint(
        "fk_lead_activities_followup", "lead_activities", type_="foreignkey"
    )
    op.drop_column("lead_activities", "followup_id")

    # --- lead_followups ---
    op.drop_index(
        "ix_lead_followups_lead_status", table_name="lead_followups"
    )
    op.drop_column("lead_followups", "last_outcome")
    op.drop_column("lead_followups", "attempt_count")

    # Revert enum → String. USING cast back to text is straightforward.
    op.execute("ALTER TABLE lead_followups ALTER COLUMN status DROP DEFAULT")
    op.execute(
        "ALTER TABLE lead_followups "
        "ALTER COLUMN status TYPE varchar "
        "USING status::text"
    )
    op.execute(
        "ALTER TABLE lead_followups "
        "ALTER COLUMN status SET DEFAULT 'pending'"
    )

    op.execute("DROP TYPE followup_status")
