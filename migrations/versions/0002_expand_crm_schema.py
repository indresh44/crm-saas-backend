"""expand crm schema

Revision ID: 0002_expand_crm_schema
Revises: 0001_create_customers_table
Create Date: 2026-03-09 06:55:00
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0002_expand_crm_schema"
down_revision = "0001_create_customers_table"
branch_labels = None
depends_on = None


user_role = sa.Enum("owner", "manager", "staff", name="user_role")
lead_activity_type = sa.Enum("call", "whatsapp", "meeting", "note", "status_change", name="lead_activity_type")
quote_status = sa.Enum("draft", "sent", "accepted", "rejected", name="quote_status")
booking_status = sa.Enum("confirmed", "in_progress", "completed", "cancelled", name="booking_status")
invoice_status = sa.Enum("draft", "sent", "paid", "partial", "overdue", name="invoice_status")
payment_method = sa.Enum("upi", "cash", "bank_transfer", "card", name="payment_method")
task_status = sa.Enum("pending", "in_progress", "done", name="task_status")
attachment_entity_type = sa.Enum("lead", "quote", "invoice", "task", name="attachment_entity_type")
message_direction = sa.Enum("incoming", "outgoing", name="message_direction")
message_channel = sa.Enum("whatsapp", "sms", "email", name="message_channel")


def upgrade() -> None:
    bind = op.get_bind()
    user_role.create(bind, checkfirst=True)
    lead_activity_type.create(bind, checkfirst=True)
    quote_status.create(bind, checkfirst=True)
    booking_status.create(bind, checkfirst=True)
    invoice_status.create(bind, checkfirst=True)
    payment_method.create(bind, checkfirst=True)
    task_status.create(bind, checkfirst=True)
    attachment_entity_type.create(bind, checkfirst=True)
    message_direction.create(bind, checkfirst=True)
    message_channel.create(bind, checkfirst=True)

    op.create_table(
        "businesses",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("phone", sa.String(), nullable=False),
        sa.Column("whatsapp_number", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("business_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("email", sa.String(), nullable=False),
        sa.Column("phone", sa.String(), nullable=True),
        sa.Column("role", user_role, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["business_id"], ["businesses.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_users_business_id"), "users", ["business_id"], unique=False)
    op.create_index(op.f("ix_users_email"), "users", ["email"], unique=False)

    op.create_foreign_key("fk_businesses_owner_user_id_users", "businesses", "users", ["owner_user_id"], ["id"])

    op.rename_table("customer", "customers")
    op.drop_index("ix_customer_business_id", table_name="customers")
    op.drop_index("ix_customer_phone", table_name="customers")
    op.create_foreign_key("fk_customers_business_id_businesses", "customers", "businesses", ["business_id"], ["id"])
    op.create_index("ix_customers_business_phone", "customers", ["business_id", "phone"], unique=False)

    op.create_table(
        "pipelines",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("business_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["business_id"], ["businesses.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_pipelines_business_id"), "pipelines", ["business_id"], unique=False)

    op.create_table(
        "pipeline_stages",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("pipeline_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("color", sa.String(), nullable=False),
        sa.ForeignKeyConstraint(["pipeline_id"], ["pipelines.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_pipeline_stages_pipeline_id"), "pipeline_stages", ["pipeline_id"], unique=False)

    op.create_table(
        "leads",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("business_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("customer_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("stage_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("source", sa.String(), nullable=True),
        sa.Column("event_date", sa.Date(), nullable=True),
        sa.Column("estimated_value", sa.Numeric(12, 2), nullable=True),
        sa.Column("assigned_to", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("notes", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["assigned_to"], ["users.id"]),
        sa.ForeignKeyConstraint(["business_id"], ["businesses.id"]),
        sa.ForeignKeyConstraint(["customer_id"], ["customers.id"]),
        sa.ForeignKeyConstraint(["stage_id"], ["pipeline_stages.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_leads_business_stage", "leads", ["business_id", "stage_id"], unique=False)
    op.create_index("ix_leads_business_created", "leads", ["business_id", "created_at"], unique=False)
    op.create_index("ix_leads_business_customer", "leads", ["business_id", "customer_id"], unique=False)

    op.create_table(
        "lead_activities",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("lead_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("type", lead_activity_type, nullable=False),
        sa.Column("description", sa.String(), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["lead_id"], ["leads.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_lead_activities_created_by"), "lead_activities", ["created_by"], unique=False)
    op.create_index(op.f("ix_lead_activities_lead_id"), "lead_activities", ["lead_id"], unique=False)

    op.create_table(
        "quotes",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("lead_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("business_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("description", sa.String(), nullable=True),
        sa.Column("total_amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("status", quote_status, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["business_id"], ["businesses.id"]),
        sa.ForeignKeyConstraint(["lead_id"], ["leads.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_quotes_business_id"), "quotes", ["business_id"], unique=False)
    op.create_index(op.f("ix_quotes_lead_id"), "quotes", ["lead_id"], unique=False)

    op.create_table(
        "quote_items",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("quote_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("quantity", sa.Numeric(12, 2), nullable=False),
        sa.Column("price", sa.Numeric(12, 2), nullable=False),
        sa.Column("total", sa.Numeric(12, 2), nullable=False),
        sa.ForeignKeyConstraint(["quote_id"], ["quotes.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_quote_items_quote_id"), "quote_items", ["quote_id"], unique=False)

    op.create_table(
        "bookings",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("lead_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("quote_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("business_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_date", sa.Date(), nullable=False),
        sa.Column("total_amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("status", booking_status, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["business_id"], ["businesses.id"]),
        sa.ForeignKeyConstraint(["lead_id"], ["leads.id"]),
        sa.ForeignKeyConstraint(["quote_id"], ["quotes.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_bookings_business_id"), "bookings", ["business_id"], unique=False)
    op.create_index(op.f("ix_bookings_lead_id"), "bookings", ["lead_id"], unique=False)
    op.create_index(op.f("ix_bookings_quote_id"), "bookings", ["quote_id"], unique=False)

    op.create_table(
        "invoices",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("business_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("booking_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("invoice_number", sa.String(), nullable=False),
        sa.Column("total_amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("status", invoice_status, nullable=False),
        sa.Column("issued_date", sa.Date(), nullable=False),
        sa.Column("due_date", sa.Date(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["booking_id"], ["bookings.id"]),
        sa.ForeignKeyConstraint(["business_id"], ["businesses.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_invoices_booking_id"), "invoices", ["booking_id"], unique=False)
    op.create_index(op.f("ix_invoices_business_id"), "invoices", ["business_id"], unique=False)
    op.create_index(op.f("ix_invoices_invoice_number"), "invoices", ["invoice_number"], unique=False)

    op.create_table(
        "payments",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("invoice_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("business_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("payment_method", payment_method, nullable=False),
        sa.Column("payment_date", sa.Date(), nullable=False),
        sa.Column("reference", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["business_id"], ["businesses.id"]),
        sa.ForeignKeyConstraint(["invoice_id"], ["invoices.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_payments_business_id"), "payments", ["business_id"], unique=False)
    op.create_index(op.f("ix_payments_invoice_id"), "payments", ["invoice_id"], unique=False)

    op.create_table(
        "tasks",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("business_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("lead_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("status", task_status, nullable=False),
        sa.Column("assigned_to", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("due_date", sa.Date(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["assigned_to"], ["users.id"]),
        sa.ForeignKeyConstraint(["business_id"], ["businesses.id"]),
        sa.ForeignKeyConstraint(["lead_id"], ["leads.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_tasks_business_id"), "tasks", ["business_id"], unique=False)
    op.create_index(op.f("ix_tasks_lead_id"), "tasks", ["lead_id"], unique=False)

    op.create_table(
        "attachments",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("business_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("entity_type", attachment_entity_type, nullable=False),
        sa.Column("entity_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("file_url", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["business_id"], ["businesses.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_attachments_business_id"), "attachments", ["business_id"], unique=False)
    op.create_index(op.f("ix_attachments_entity_id"), "attachments", ["entity_id"], unique=False)

    op.create_table(
        "notifications",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("type", sa.String(), nullable=False),
        sa.Column("entity_type", attachment_entity_type, nullable=False),
        sa.Column("entity_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("message", sa.String(), nullable=False),
        sa.Column("is_read", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_notifications_entity_id"), "notifications", ["entity_id"], unique=False)
    op.create_index(op.f("ix_notifications_user_id"), "notifications", ["user_id"], unique=False)

    op.create_table(
        "messages",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("business_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("customer_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("lead_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("direction", message_direction, nullable=False),
        sa.Column("channel", message_channel, nullable=False),
        sa.Column("message_text", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["business_id"], ["businesses.id"]),
        sa.ForeignKeyConstraint(["customer_id"], ["customers.id"]),
        sa.ForeignKeyConstraint(["lead_id"], ["leads.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_messages_business_id"), "messages", ["business_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_messages_business_id"), table_name="messages")
    op.drop_table("messages")

    op.drop_index(op.f("ix_notifications_user_id"), table_name="notifications")
    op.drop_index(op.f("ix_notifications_entity_id"), table_name="notifications")
    op.drop_table("notifications")

    op.drop_index(op.f("ix_attachments_entity_id"), table_name="attachments")
    op.drop_index(op.f("ix_attachments_business_id"), table_name="attachments")
    op.drop_table("attachments")

    op.drop_index(op.f("ix_tasks_lead_id"), table_name="tasks")
    op.drop_index(op.f("ix_tasks_business_id"), table_name="tasks")
    op.drop_table("tasks")

    op.drop_index(op.f("ix_payments_invoice_id"), table_name="payments")
    op.drop_index(op.f("ix_payments_business_id"), table_name="payments")
    op.drop_table("payments")

    op.drop_index(op.f("ix_invoices_invoice_number"), table_name="invoices")
    op.drop_index(op.f("ix_invoices_business_id"), table_name="invoices")
    op.drop_index(op.f("ix_invoices_booking_id"), table_name="invoices")
    op.drop_table("invoices")

    op.drop_index(op.f("ix_bookings_quote_id"), table_name="bookings")
    op.drop_index(op.f("ix_bookings_lead_id"), table_name="bookings")
    op.drop_index(op.f("ix_bookings_business_id"), table_name="bookings")
    op.drop_table("bookings")

    op.drop_index(op.f("ix_quote_items_quote_id"), table_name="quote_items")
    op.drop_table("quote_items")

    op.drop_index(op.f("ix_quotes_lead_id"), table_name="quotes")
    op.drop_index(op.f("ix_quotes_business_id"), table_name="quotes")
    op.drop_table("quotes")

    op.drop_index(op.f("ix_lead_activities_lead_id"), table_name="lead_activities")
    op.drop_index(op.f("ix_lead_activities_created_by"), table_name="lead_activities")
    op.drop_table("lead_activities")

    op.drop_index("ix_leads_business_customer", table_name="leads")
    op.drop_index("ix_leads_business_created", table_name="leads")
    op.drop_index("ix_leads_business_stage", table_name="leads")
    op.drop_table("leads")

    op.drop_index(op.f("ix_pipeline_stages_pipeline_id"), table_name="pipeline_stages")
    op.drop_table("pipeline_stages")

    op.drop_index(op.f("ix_pipelines_business_id"), table_name="pipelines")
    op.drop_table("pipelines")

    op.drop_index("ix_customers_business_phone", table_name="customers")
    op.drop_constraint("fk_customers_business_id_businesses", "customers", type_="foreignkey")
    op.create_index("ix_customer_phone", "customers", ["phone"], unique=False)
    op.create_index("ix_customer_business_id", "customers", ["business_id"], unique=False)
    op.rename_table("customers", "customer")

    op.drop_constraint("fk_businesses_owner_user_id_users", "businesses", type_="foreignkey")

    op.drop_index(op.f("ix_users_email"), table_name="users")
    op.drop_index(op.f("ix_users_business_id"), table_name="users")
    op.drop_table("users")

    op.drop_table("businesses")

    message_channel.drop(op.get_bind(), checkfirst=True)
    message_direction.drop(op.get_bind(), checkfirst=True)
    attachment_entity_type.drop(op.get_bind(), checkfirst=True)
    task_status.drop(op.get_bind(), checkfirst=True)
    payment_method.drop(op.get_bind(), checkfirst=True)
    invoice_status.drop(op.get_bind(), checkfirst=True)
    booking_status.drop(op.get_bind(), checkfirst=True)
    quote_status.drop(op.get_bind(), checkfirst=True)
    lead_activity_type.drop(op.get_bind(), checkfirst=True)
    user_role.drop(op.get_bind(), checkfirst=True)
