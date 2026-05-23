"""Postgres-backed integration test for the read-model virtual fields.

Unlike ``test_read_model.py`` (which asserts on rendered SQL), this actually
EXECUTES the compiled queries against the real database, closing the biggest
risk: that the SQL renders fine but computes the wrong values on Postgres.

It is side-effect-free: it builds a tiny isolated fixture (its own brand-new
business) via direct ORM inserts, ``flush()``es so the rows are visible WITHIN
the transaction, runs ``compile_query`` + ``execute_query`` on that same session,
asserts the actual returned values, then ``rollback()``s — nothing is committed,
no R2, no leftover rows.

Run from the backend root:

    python -m app.read_model.tests.test_integration_pg

If the database is unreachable it prints SKIP and exits 0 (so it never blocks the
dependency-free unit tests).
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

from sqlmodel import Session

from app.models.business import Business
from app.models.customer import Customer
from app.models.enums import InvoiceStatus, LeadSource, PaymentMethod, UserRole
from app.models.invoice import Invoice
from app.models.invoice_adjustment import InvoiceAdjustment
from app.models.lead import Lead
from app.models.lead_followup import LeadFollowup
from app.models.payment import Payment
from app.models.pipeline import Pipeline, PipelineStage
from app.models.user import User
from app.read_model.compiler import Aggregation, Filter, ReadQuery, SortKey, compile_query
from app.read_model.executor import execute_query

# Fixed clock so date math is deterministic. today = 2026-05-22.
_NOW = datetime(2026, 5, 22, 12, 0, 0, tzinfo=timezone.utc)
_TODAY = _NOW.date()


def _d(v) -> Decimal:
    return Decimal(str(v))


def _build_fixture(session: Session):
    """Insert one isolated business with leads, invoices, payments, an adjustment,
    and follow-ups. Returns the business_id. Flushed, not committed."""
    # Insert in dependency order, flushing parents before children, so FK targets
    # always exist (SQLAlchemy's single-flush ordering can't be relied on here,
    # partly due to the businesses<->users cycle). All within one rolled-back tx.
    bid = uuid4()
    session.add(Business(id=bid, name="RM Test Co", phone="9990000000"))
    session.flush()

    user_id = uuid4()
    session.add(User(id=user_id, business_id=bid, name="Owner", email=f"owner-{bid}@test.local", role=UserRole.OWNER))
    session.flush()

    pipeline_id = uuid4()
    session.add(Pipeline(id=pipeline_id, business_id=bid, name="Default", is_default=True))
    session.flush()
    won_stage, active_stage = uuid4(), uuid4()
    session.add(PipelineStage(id=won_stage, pipeline_id=pipeline_id, name="Won", position=5, color="#0a0"))
    session.add(PipelineStage(id=active_stage, pipeline_id=pipeline_id, name="Site Visited", position=1, color="#00a"))

    cust_id = uuid4()
    session.add(Customer(id=cust_id, business_id=bid, name="Rajesh Mehta",
                         phone="+91 99999 00001", phone_normalized="919999900001"))
    session.flush()

    # Two leads: one terminal (Won), one active (Site Visited).
    lead_won, lead_active = uuid4(), uuid4()
    session.add(Lead(id=lead_won, business_id=bid, customer_id=cust_id, stage_id=won_stage,
                     title="Closed kitchen", source=LeadSource.REFERRAL))
    session.add(Lead(id=lead_active, business_id=bid, customer_id=cust_id, stage_id=active_stage,
                     title="Open 3BHK", source=LeadSource.WHATSAPP))
    session.flush()

    def _inv(number, status, total, due_offset):
        iid = uuid4()
        session.add(Invoice(
            id=iid, business_id=bid, lead_id=lead_active, status=status,
            issued_date=_TODAY - timedelta(days=30), due_date=_TODAY - timedelta(days=due_offset),
            subtotal=_d(total), tax_total=_d(0), total_amount=_d(total),
            invoice_number=number,
        ))
        return iid

    # INV-A: approved, 100000, due 10d ago. Payment 60000 active + 10000 VOIDED, plus a
    #        5000 discount adjustment. Canonical balance = 100000 - 60000 - 5000 = 35000.
    # INV-B: approved, fully paid (balance 0), due 5d ago -> NOT overdue (balance not > 0).
    # INV-C: draft, due 3d ago -> NOT overdue (draft excluded); document_type estimate.
    # INV-D: cancelled, due 20d ago -> NOT overdue (cancelled excluded); is_cancelled True.
    # INV-E: approved, no payments, due in the FUTURE -> NOT overdue; balance 40000.
    # All invoices flushed before their payments/adjustments so FK targets exist.
    inv_a = _inv("T-A", InvoiceStatus.APPROVED, 100000, due_offset=10)
    inv_b = _inv("T-B", InvoiceStatus.APPROVED, 50000, due_offset=5)
    _inv("T-C", InvoiceStatus.DRAFT, 20000, due_offset=3)
    _inv("T-D", InvoiceStatus.CANCELLED, 30000, due_offset=20)
    _inv("T-E", InvoiceStatus.APPROVED, 40000, due_offset=-5)
    session.flush()

    session.add(Payment(business_id=bid, invoice_id=inv_a, amount=_d(60000),
                        payment_method=PaymentMethod.UPI, payment_date=_TODAY - timedelta(days=12)))
    session.add(Payment(business_id=bid, invoice_id=inv_a, amount=_d(10000),
                        payment_method=PaymentMethod.CASH, payment_date=_TODAY - timedelta(days=11),
                        voided_at=_NOW))  # VOIDED -> must be excluded
    session.add(InvoiceAdjustment(invoice_id=inv_a, amount=_d(5000), adjustment_type="discount",
                                  reason="festival discount", created_by=user_id))

    # INV-B fully paid (balance 0); C draft; D cancelled; E approved future-due (created above).
    session.add(Payment(business_id=bid, invoice_id=inv_b, amount=_d(50000),
                        payment_method=PaymentMethod.BANK_TRANSFER, payment_date=_TODAY - timedelta(days=6)))

    # Follow-ups: active lead has a PENDING overdue one (yesterday) -> has_overdue_followup True.
    session.add(LeadFollowup(lead_id=lead_active, created_by=user_id, status="pending",
                             scheduled_at=_NOW - timedelta(days=1)))
    # A pending FUTURE follow-up and a past DONE one on the won lead -> has_overdue_followup False.
    session.add(LeadFollowup(lead_id=lead_won, created_by=user_id, status="pending",
                             scheduled_at=_NOW + timedelta(days=2)))
    session.add(LeadFollowup(lead_id=lead_won, created_by=user_id, status="done",
                             scheduled_at=_NOW - timedelta(days=3), completed_at=_NOW - timedelta(days=3)))

    session.flush()  # assign PKs + make visible in this transaction; NO commit
    return bid


def test_pg_invoice_virtual_fields():
    with Session(engine) as session:
        try:
            bid = _build_fixture(session)
            cq = compile_query(
                ReadQuery(
                    "invoices",
                    sort=[SortKey("invoice_number", "asc")],
                    select=["invoice_number", "balance", "is_overdue", "days_overdue", "document_type", "is_cancelled"],
                ),
                business_id=bid, now=_NOW,
            )
            rows = {r["invoice_number"]: r for r in execute_query(cq, session).rows}

            # INV-A: voided payment excluded, adjustment subtracted -> 35000; overdue 10 days.
            a = rows["T-A"]
            assert a["balance"] == "35000.00", a["balance"]
            assert a["is_overdue"] is True, a
            assert a["days_overdue"] == 10, a["days_overdue"]
            assert a["document_type"] == "tax_invoice"
            assert a["is_cancelled"] is False

            # INV-B: fully paid -> balance 0, not overdue, days_overdue null.
            b = rows["T-B"]
            assert b["balance"] == "0.00", b["balance"]
            assert b["is_overdue"] is False
            assert b["days_overdue"] is None

            # INV-C: draft past due -> not overdue; estimate.
            c = rows["T-C"]
            assert c["is_overdue"] is False
            assert c["days_overdue"] is None
            assert c["document_type"] == "estimate"

            # INV-D: cancelled -> not overdue; flagged cancelled; tax_invoice.
            d = rows["T-D"]
            assert d["is_cancelled"] is True
            assert d["is_overdue"] is False
            assert d["document_type"] == "tax_invoice"

            # INV-E: approved, future due, no payments -> balance 40000, not overdue.
            e = rows["T-E"]
            assert e["balance"] == "40000.00", e["balance"]
            assert e["is_overdue"] is False
            assert e["days_overdue"] is None
        finally:
            session.rollback()


def test_pg_invoice_filter_is_overdue_excludes_paid_draft_cancelled():
    with Session(engine) as session:
        try:
            bid = _build_fixture(session)
            cq = compile_query(
                ReadQuery("invoices", filters=[Filter("is_overdue", "=", True)]),
                business_id=bid, now=_NOW,
            )
            rows = execute_query(cq, session).rows
            numbers = sorted(r["invoice_number"] for r in rows)
            # Only INV-A qualifies (B paid, C draft, D cancelled, E future-due).
            assert numbers == ["T-A"], numbers
        finally:
            session.rollback()


def test_pg_days_overdue_filter_is_null_safe():
    with Session(engine) as session:
        try:
            bid = _build_fixture(session)
            cq = compile_query(
                ReadQuery("invoices", filters=[Filter("days_overdue", ">", 5)]),
                business_id=bid, now=_NOW,
            )
            rows = execute_query(cq, session).rows
            # Only INV-A (10 days). NULL days_overdue rows must NOT match or error.
            assert sorted(r["invoice_number"] for r in rows) == ["T-A"]
        finally:
            session.rollback()


def test_pg_lead_virtual_fields():
    with Session(engine) as session:
        try:
            bid = _build_fixture(session)
            cq = compile_query(
                ReadQuery(
                    "leads",
                    sort=[SortKey("title", "asc")],
                    select=["title", "stage_name", "stage_color", "is_terminal", "is_active", "has_overdue_followup"],
                ),
                business_id=bid, now=_NOW,
            )
            rows = {r["title"]: r for r in execute_query(cq, session).rows}

            # "Closed kitchen" -> Won stage: terminal, not active, future+done follow-ups only.
            won = rows["Closed kitchen"]
            assert won["stage_name"] == "Won"
            assert won["is_terminal"] is True
            assert won["is_active"] is False
            assert won["has_overdue_followup"] is False

            # "Open 3BHK" -> Site Visited: not terminal, active, has a pending overdue follow-up.
            active = rows["Open 3BHK"]
            assert active["stage_name"] == "Site Visited"
            assert active["is_terminal"] is False
            assert active["is_active"] is True
            assert active["has_overdue_followup"] is True
        finally:
            session.rollback()


def test_pg_select_joined_field_returns_customer_name():
    with Session(engine) as session:
        try:
            bid = _build_fixture(session)
            cq = compile_query(
                ReadQuery("leads", sort=[SortKey("title", "asc")], select=["title", "notes", "customer_name"]),
                business_id=bid, now=_NOW,
            )
            result = execute_query(cq, session)
            assert result.fields == ("title", "notes", "customer_name")
            rows = {r["title"]: r for r in result.rows}
            # Both fixture leads belong to "Rajesh Mehta" — the joined column comes back.
            assert rows["Open 3BHK"]["customer_name"] == "Rajesh Mehta", rows["Open 3BHK"]
            assert rows["Closed kitchen"]["customer_name"] == "Rajesh Mehta"
            # `notes` (newly exposed) is selectable and present (None in this fixture).
            assert "notes" in rows["Open 3BHK"]
        finally:
            session.rollback()


def test_pg_invoice_two_hop_customer_details():
    # invoice -> lead -> customer (two-hop). All fixture invoices use lead_active,
    # whose customer is "Rajesh Mehta". lead_title comes from the single-hop lead join;
    # the leads table must be joined only once across both.
    with Session(engine) as session:
        try:
            bid = _build_fixture(session)
            cq = compile_query(
                ReadQuery(
                    "invoices",
                    sort=[SortKey("invoice_number", "asc")],
                    select=["invoice_number", "lead_title", "customer_name", "customer_phone"],
                ),
                business_id=bid, now=_NOW,
            )
            result = execute_query(cq, session)
            assert result.fields == ("invoice_number", "lead_title", "customer_name", "customer_phone")
            row = result.rows[0]
            assert row["customer_name"] == "Rajesh Mehta", row
            assert row["customer_phone"] == "+91 99999 00001", row
            assert row["lead_title"] == "Open 3BHK", row
        finally:
            session.rollback()


def test_pg_tenant_isolation_blocks_other_business():
    with Session(engine) as session:
        try:
            _build_fixture(session)  # real data under its own business_id
            other_bid = uuid4()      # a different tenant sees nothing
            cq = compile_query(ReadQuery("invoices", select=["balance"]), business_id=other_bid, now=_NOW)
            rows = execute_query(cq, session).rows
            assert rows == [], rows
        finally:
            session.rollback()


# ---------------------------------------------------------------------------
# Aggregation — executed against Postgres on the same isolated fixture.
# Fixture invoices: A,B,E approved | C draft | D cancelled.
# Balances: A=35000 (60000 active + 10000 VOIDED paid, 5000 discount), B=0 (paid),
#           C=20000, D=30000, E=40000.
# ---------------------------------------------------------------------------

def test_pg_count_grouped_by_status():
    with Session(engine) as session:
        try:
            bid = _build_fixture(session)
            cq = compile_query(
                ReadQuery("invoices", aggregations=[Aggregation("count")], group_by=["status"]),
                business_id=bid, now=_NOW,
            )
            result = execute_query(cq, session)
            counts = {r["status"]: r["count"] for r in result.rows}
            assert counts == {"approved": 3, "draft": 1, "cancelled": 1}, counts
            assert result.to_dict()["kind"] == "aggregate"
        finally:
            session.rollback()


def test_pg_sum_balance_grouped_by_status_exact():
    with Session(engine) as session:
        try:
            bid = _build_fixture(session)
            cq = compile_query(
                ReadQuery("invoices", aggregations=[Aggregation("sum", "balance")], group_by=["status"]),
                business_id=bid, now=_NOW,
            )
            sums = {r["status"]: r["sum_balance"] for r in execute_query(cq, session).rows}
            # Hand-computed from the fixture (Decimal-as-string, exact):
            #   approved = A 35000 + B 0 + E 40000 = 75000.00  (voided payment excluded,
            #                                                    5000 discount subtracted)
            #   draft    = C 20000.00
            #   cancelled= D 30000.00
            assert sums == {"approved": "75000.00", "draft": "20000.00", "cancelled": "30000.00"}, sums
        finally:
            session.rollback()


def test_pg_count_filtered_by_is_overdue():
    with Session(engine) as session:
        try:
            bid = _build_fixture(session)
            cq = compile_query(
                ReadQuery("invoices", aggregations=[Aggregation("count")], filters=[Filter("is_overdue", "=", True)]),
                business_id=bid, now=_NOW,
            )
            rows = execute_query(cq, session).rows
            # No group_by -> single total row. Only INV-A is overdue.
            assert rows == [{"count": 1}], rows
        finally:
            session.rollback()


def test_pg_aggregate_tenant_isolation():
    with Session(engine) as session:
        try:
            _build_fixture(session)  # data under its own business_id
            other_bid = uuid4()
            cq = compile_query(
                ReadQuery("invoices", aggregations=[Aggregation("sum", "balance")], group_by=["status"]),
                business_id=other_bid, now=_NOW,
            )
            rows = execute_query(cq, session).rows
            assert rows == [], rows  # another business sees zero groups
        finally:
            session.rollback()


# ---------------------------------------------------------------------------
# Runner (skips cleanly if the DB is unreachable)
# ---------------------------------------------------------------------------

def _run() -> int:
    try:
        from sqlalchemy import text
        with engine.connect() as conn:
            conn.execute(text("select 1"))
    except Exception as e:  # noqa: BLE001
        print(f"SKIP  database unreachable ({type(e).__name__}: {str(e)[:120]}) — integration tests skipped")
        return 0

    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failures = 0
    for t in tests:
        try:
            t()
            print(f"PASS  {t.__name__}")
        except AssertionError as e:
            failures += 1
            print(f"FAIL  {t.__name__}: {e}")
        except Exception as e:  # noqa: BLE001
            failures += 1
            print(f"ERROR {t.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - failures}/{len(tests)} passed")
    return 1 if failures else 0


# Imported lazily so module import doesn't require a live engine at collection time.
from app.core.database import engine  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(_run())
