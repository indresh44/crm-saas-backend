"""one pipeline per business

Revision ID: 0007_one_pipeline_per_business
Revises: 0006_msme_model_updates
Create Date: 2026-03-18 00:00:00
"""

from alembic import op
import sqlalchemy as sa


revision = "0007_one_pipeline_per_business"
down_revision = "0006_msme_model_updates"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Step 0: Repoint leads that reference stages in duplicate pipelines to the
    # first stage of the kept pipeline for that business.
    op.execute(
        """
        WITH kept_pipelines AS (
            SELECT DISTINCT ON (business_id) id, business_id
            FROM pipelines
            ORDER BY business_id, created_at DESC
        ),
        first_stage_per_business AS (
            SELECT DISTINCT ON (p.business_id) p.business_id, ps.id AS stage_id
            FROM pipelines p
            JOIN pipeline_stages ps ON ps.pipeline_id = p.id
            JOIN kept_pipelines kp ON kp.id = p.id
            ORDER BY p.business_id, ps.position ASC, ps.id ASC
        )
        UPDATE leads l
        SET stage_id = fs.stage_id
        FROM pipeline_stages current_ps
        JOIN pipelines current_p ON current_p.id = current_ps.pipeline_id
        JOIN first_stage_per_business fs ON fs.business_id = current_p.business_id
        WHERE l.stage_id = current_ps.id
          AND current_p.id NOT IN (SELECT id FROM kept_pipelines);
        """
    )

    # Step 1: Keep only the most recent pipeline per business, remove duplicates and their stages.
    op.execute(
        """
        DELETE FROM pipeline_stages
        WHERE pipeline_id IN (
          SELECT id FROM pipelines
          WHERE id NOT IN (
            SELECT DISTINCT ON (business_id) id
            FROM pipelines
            ORDER BY business_id, created_at DESC
          )
        );
        """
    )
    op.execute(
        """
        DELETE FROM pipelines
        WHERE id NOT IN (
          SELECT DISTINCT ON (business_id) id
          FROM pipelines
          ORDER BY business_id, created_at DESC
        );
        """
    )

    # Step 2: Add default marker to pipelines.
    op.add_column(
        "pipelines",
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default="true"),
    )

    # Step 3: Enforce one pipeline per business.
    op.create_unique_constraint(
        "uq_pipelines_business_id",
        "pipelines",
        ["business_id"],
    )

    # Step 4: Set each lead.stage_id to the first stage of the business pipeline.
    op.execute(
        """
        UPDATE leads AS l
        SET stage_id = (
            SELECT ps.id
            FROM pipelines AS p
            JOIN pipeline_stages AS ps ON ps.pipeline_id = p.id
            WHERE p.business_id = l.business_id
            ORDER BY ps.position ASC, ps.id ASC
            LIMIT 1
        )
        WHERE EXISTS (
            SELECT 1
            FROM pipelines AS p2
            JOIN pipeline_stages AS ps2 ON ps2.pipeline_id = p2.id
            WHERE p2.business_id = l.business_id
        );
        """
    )

    # Step 5: Mark all current pipelines as default after one-per-business enforcement.
    op.execute(
        """
        UPDATE pipelines
        SET is_default = true
        WHERE is_default IS DISTINCT FROM true;
        """
    )


def downgrade() -> None:
    op.drop_constraint("uq_pipelines_business_id", "pipelines")
    op.drop_column("pipelines", "is_default")
