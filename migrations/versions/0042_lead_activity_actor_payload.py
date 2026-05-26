"""Enrich lead_activities for the assistant's diary.

  1. Add new LeadActivityType enum values: lead_created, lead_updated,
     invoice_sent, invoice_cancelled, invoice_adjusted.
  2. Create a new lead_activity_actor_type enum (human, ai, task, system).
  3. Add columns on lead_activities:
        actor_type        — nullable enum; NULL on pre-existing rows
                            (honest "unknown — pre-enrichment"); NOT NULL
                            enforced application-side via the
                            create_lead_activity chokepoint going forward.
        payload           — nullable JSONB; machine-readable facts for
                            system-emitted types. NULL for user-logged
                            (NOTE/CALL/MEETING/WHATSAPP) and for old rows.
        chat_session_id   — nullable FK -> agent_chat_sessions(id) ON DELETE
                            SET NULL; populated when actor_type is ai/task.
        task_id           — nullable FK -> agent_tasks(id) ON DELETE SET NULL;
                            populated when actor_type is task.
  4. Loosen lead_activities.created_by from NOT NULL to NULL. Required because
     the WhatsApp webhook (system actor) has no real user — previously it
     misattributed to a random business user; now it correctly stores NULL.
     Pre-condition audit (see chat record): only 3 readers exist for
     created_by — DB column, Pydantic schema, frontend TS type. All three
     are updated in this build to tolerate NULL. No UI surface renders
     created_by; the change is invisible to users.

The actor_type enum is created via raw DDL (matches the project's
existing pattern, e.g. lead_activity_type is a Postgres enum likewise
managed by hand-written ALTER TYPE statements).

Revision ID: 0042_lead_activity_actor_payload
Revises: 0041_agent_tasks
Create Date: 2026-05-25 00:00:00
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID
from alembic import op


revision = "0042_lead_activity_actor_payload"
down_revision = "0041_agent_tasks"
branch_labels = None
depends_on = None


_NEW_ACTIVITY_TYPES = (
    "lead_created",
    "lead_updated",
    "invoice_sent",
    "invoice_cancelled",
    "invoice_adjusted",
)

_ACTOR_TYPE_VALUES = ("human", "ai", "task", "system")


def upgrade() -> None:
    # --- 1. New lead_activity_type enum values ---
    # Use IF NOT EXISTS for re-run safety; matches 0024's pattern.
    for v in _NEW_ACTIVITY_TYPES:
        op.execute(
            f"ALTER TYPE lead_activity_type ADD VALUE IF NOT EXISTS '{v}'"
        )

    # --- 2. New lead_activity_actor_type enum ---
    values_sql = ", ".join(f"'{v}'" for v in _ACTOR_TYPE_VALUES)
    op.execute(
        f"CREATE TYPE lead_activity_actor_type AS ENUM ({values_sql})"
    )

    # --- 3. New columns on lead_activities ---
    op.add_column(
        "lead_activities",
        sa.Column(
            "actor_type",
            sa.Enum(
                *_ACTOR_TYPE_VALUES,
                name="lead_activity_actor_type",
                create_type=False,   # already created above
            ),
            nullable=True,           # NULL on old rows = "unknown"
        ),
    )
    op.add_column(
        "lead_activities",
        sa.Column("payload", JSONB, nullable=True),
    )
    op.add_column(
        "lead_activities",
        sa.Column(
            "chat_session_id", UUID(as_uuid=True), nullable=True,
        ),
    )
    op.add_column(
        "lead_activities",
        sa.Column(
            "task_id", UUID(as_uuid=True), nullable=True,
        ),
    )
    op.create_foreign_key(
        "fk_lead_activities_chat_session",
        "lead_activities", "agent_chat_sessions",
        ["chat_session_id"], ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_lead_activities_task",
        "lead_activities", "agent_tasks",
        ["task_id"], ["id"],
        ondelete="SET NULL",
    )

    # --- 4. Loosen created_by to NULL-able ---
    # The WhatsApp webhook + future system actors have no real user.
    op.alter_column(
        "lead_activities", "created_by",
        existing_type=UUID(as_uuid=True),
        nullable=True,
    )


def downgrade() -> None:
    # Reverse created_by nullability first. If any row has NULL
    # created_by (i.e. a SYSTEM-actor activity was logged after upgrade),
    # this will fail — that's correct, the downgrade is destructive.
    op.alter_column(
        "lead_activities", "created_by",
        existing_type=UUID(as_uuid=True),
        nullable=False,
    )

    op.drop_constraint(
        "fk_lead_activities_task", "lead_activities", type_="foreignkey",
    )
    op.drop_constraint(
        "fk_lead_activities_chat_session", "lead_activities", type_="foreignkey",
    )
    op.drop_column("lead_activities", "task_id")
    op.drop_column("lead_activities", "chat_session_id")
    op.drop_column("lead_activities", "payload")
    op.drop_column("lead_activities", "actor_type")

    op.execute("DROP TYPE lead_activity_actor_type")

    # PostgreSQL does not support removing enum values from an enum type
    # safely in-place. The new lead_activity_type values stay; matches
    # the no-op convention in 0024 and 0038.
