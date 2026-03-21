from datetime import date
from sqlmodel import SQLModel


class OverdueInvoiceSummary(SQLModel):
    invoice_id: str
    lead_id: str | None = None
    invoice_number: str
    customer_name: str | None = None
    customer_phone: str | None = None
    total_amount: float
    amount_paid: float
    balance_due: float
    due_date: date
    days_overdue: int


class PaymentSummaryRead(SQLModel):
    collections_this_month: float
    collections_last_month: float
    total_outstanding: float
    outstanding_invoice_count: int
    overdue_invoices: list[OverdueInvoiceSummary]
