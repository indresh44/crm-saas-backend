"""
Seed a realistic demo business ("Vikram Interiors, Jaipur") for the demo video.

Run from the backend root:

    python -m scripts.seed_demo

Idempotent: if the demo business already exists, it's wiped and rebuilt.
Requires: R2 env vars populated (images are uploaded for real), PostgreSQL up,
alembic migrations applied.
"""

from __future__ import annotations

import mimetypes
import sys
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from uuid import UUID

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from sqlalchemy import delete
from sqlmodel import Session, select

from app.core.database import engine
from app.models.attachment import Attachment
from app.models.auth_identity import AuthIdentity
from app.models.auth_schemas import RegisterRequest
from app.models.business import Business
from app.models.catalog_item import CatalogItem, CatalogItemCreate
from app.models.chat import ChatMessage, ChatThread
from app.models.customer import Customer, CustomerCreateRequest
from app.models.enums import (
    AttachmentEntityType,
    AuthProvider,
    CatalogItemUnit,
    InvoiceStatus,
    LeadActivityType,
    LeadSource,
    PaymentMethod,
)
from app.models.invoice import Invoice
from app.models.invoice_item import InvoiceItem, InvoiceItemCreate
from app.models.lead import Lead, LeadActivity, LeadCreate
from app.models.lead_followup import LeadFollowup, LeadFollowupCreate, LeadFollowupDone
from app.models.payment import Payment, PaymentCreate
from app.models.pipeline import Pipeline, PipelineStage
from app.models.refresh_token import RefreshToken
from app.models.user import User
from app.repositories.lead_repository import create_lead_activity
from app.services import (
    attachment_service,
    auth_service,
    catalog_item_service,
    customer_service,
    invoice_service,
    lead_followup_service,
    lead_service,
    onboarding_service,
    payment_service,
)
from app.services.invoice_service import InvoiceCreateWithItems, InvoiceData


OWNER_EMAIL = "vikram@vikraminteriors.demo"
OWNER_PASSWORD = "Demo@12345"
OWNER_NAME = "Vikram Sharma"
OWNER_PHONE = "9829012345"
BUSINESS_NAME = "Vikram Interiors"
BUSINESS_CITY = "Jaipur"

CATALOG_DIR = Path(__file__).resolve().parent.parent / "demo_assets" / "catalog"


# ---------- utilities ----------

def _log(msg: str) -> None:
    print(f"[seed] {msg}", flush=True)


def _d(value: str | int | float) -> Decimal:
    return Decimal(str(value))


def _days_ago(n: int) -> date:
    return date.today() - timedelta(days=n)


def _dt_at(d: date, hour: int = 10, minute: int = 0) -> datetime:
    return datetime.combine(d, time(hour=hour, minute=minute), tzinfo=timezone.utc)


# ---------- wipe ----------

def wipe_existing(session: Session) -> None:
    """Delete Vikram Interiors business and every child row, if present."""
    identity = session.exec(
        select(AuthIdentity).where(
            AuthIdentity.provider == AuthProvider.EMAIL,
            AuthIdentity.provider_id == OWNER_EMAIL,
        )
    ).first()

    if identity is None:
        _log("no existing demo account — skipping wipe")
        return

    owner = session.get(User, identity.user_id)
    if owner is None:
        session.delete(identity)
        session.commit()
        return

    business_id = owner.business_id
    _log(f"wiping existing business {business_id}")

    # Collect IDs we need for cascading deletes.
    lead_ids = [
        lid for (lid,) in session.exec(
            select(Lead.id).where(Lead.business_id == business_id)
        ).all()
    ]
    invoice_ids = [
        iid for (iid,) in session.exec(
            select(Invoice.id).where(Invoice.business_id == business_id)
        ).all()
    ]
    thread_ids = [
        tid for (tid,) in session.exec(
            select(ChatThread.id).where(ChatThread.business_id == business_id)
        ).all()
    ]
    pipeline = session.exec(
        select(Pipeline).where(Pipeline.business_id == business_id)
    ).first()
    user_ids = [
        uid for (uid,) in session.exec(
            select(User.id).where(User.business_id == business_id)
        ).all()
    ]

    # Delete order respects FK constraints.
    if thread_ids:
        session.execute(delete(ChatMessage).where(ChatMessage.thread_id.in_(thread_ids)))
    session.execute(delete(ChatThread).where(ChatThread.business_id == business_id))

    # Attachments first — they reference invoice_items, invoices, leads, catalog items.
    session.execute(delete(Attachment).where(Attachment.business_id == business_id))

    session.execute(delete(Payment).where(Payment.business_id == business_id))
    if invoice_ids:
        session.execute(delete(InvoiceItem).where(InvoiceItem.invoice_id.in_(invoice_ids)))
    session.execute(delete(Invoice).where(Invoice.business_id == business_id))

    if lead_ids:
        session.execute(delete(LeadFollowup).where(LeadFollowup.lead_id.in_(lead_ids)))
        session.execute(delete(LeadActivity).where(LeadActivity.lead_id.in_(lead_ids)))
    session.execute(delete(Lead).where(Lead.business_id == business_id))
    session.execute(delete(Customer).where(Customer.business_id == business_id))
    session.execute(delete(CatalogItem).where(CatalogItem.business_id == business_id))

    if pipeline is not None:
        session.execute(delete(PipelineStage).where(PipelineStage.pipeline_id == pipeline.id))
        session.execute(delete(Pipeline).where(Pipeline.id == pipeline.id))

    if user_ids:
        session.execute(delete(RefreshToken).where(RefreshToken.user_id.in_(user_ids)))
        session.execute(delete(AuthIdentity).where(AuthIdentity.user_id.in_(user_ids)))
    session.execute(delete(User).where(User.business_id == business_id))
    session.execute(delete(Business).where(Business.id == business_id))
    session.commit()
    _log("wipe complete")


# ---------- business + owner ----------

def create_business_and_owner(session: Session) -> tuple[User, Business]:
    _log("creating business + owner")
    auth_service.register(
        session=session,
        data=RegisterRequest(
            email=OWNER_EMAIL,
            password=OWNER_PASSWORD,
            name=OWNER_NAME,
            business_name=BUSINESS_NAME,
            city=BUSINESS_CITY,
            phone=OWNER_PHONE,
            country_code="+91",
            is_whatsapp=True,
        ),
    )

    identity = session.exec(
        select(AuthIdentity).where(AuthIdentity.provider_id == OWNER_EMAIL)
    ).first()
    owner = session.get(User, identity.user_id)
    business = session.get(Business, owner.business_id)

    business.state = "Rajasthan"
    business.pin_code = "302001"
    business.gst_number = "08AXXPS1234A1Z5"
    business.whatsapp_number = business.phone
    business.invoice_prefix = "VI"
    business.default_due_days = 15
    business.bank_name = "HDFC Bank"
    business.bank_account_number = "50100234567890"
    business.bank_ifsc = "HDFC0001234"
    business.upi_id = "vikraminteriors@okhdfcbank"
    business.invoice_footer = (
        "Thank you for your business. Payment due within 15 days. "
        "For queries, WhatsApp Vikram at +91 98290 12345."
    )
    business.address = "Shop 12, Ground Floor, Raja Park, Adarsh Nagar"
    business.email = OWNER_EMAIL
    session.add(business)
    session.commit()
    session.refresh(business)
    session.refresh(owner)
    return owner, business


def setup_pipeline(session: Session, business_id: UUID) -> dict[str, PipelineStage]:
    _log("setting persona + pipeline")
    onboarding_service.set_persona(
        session=session,
        business_id=business_id,
        persona="interior_designer",
    )
    onboarding_service.set_language(
        session=session,
        business_id=business_id,
        language="hinglish",
    )
    onboarding_service.complete_onboarding(
        session=session,
        business_id=business_id,
        method="form",
    )  # returns (business, should_send_welcome_email) — seed script ignores both

    pipeline = session.exec(
        select(Pipeline).where(Pipeline.business_id == business_id)
    ).first()
    stages = session.exec(
        select(PipelineStage).where(PipelineStage.pipeline_id == pipeline.id)
    ).all()
    return {stage.name: stage for stage in stages}


# ---------- catalog ----------

CATALOG = [
    ("Modular Kitchen", "modular-kitchen.jpg", "Full modular kitchen with cabinets, chimney, hob — standard 10x8 ft layout", CatalogItemUnit.PIECE, "180000"),
    ("Wardrobe (Sliding 3-door)", "sliding-wardrobe.jpg", "Sliding wardrobe, 7ft height, laminate finish, Hettich channels", CatalogItemUnit.PIECE, "65000"),
    ("False Ceiling", "false-ceiling.jpg", "Gypsum false ceiling with cove lighting — rate per sq ft", CatalogItemUnit.SQ_FT, "85"),
    ("Hettich Hardware Set", "hettich-hardware.jpg", "Soft-close hinges + drawer slides bundle for one wardrobe", CatalogItemUnit.PIECE, "8500"),
    ("Asian Royale Paint (per room)", "asian-royale-paint.jpg", "Premium emulsion paint, 2 coats, per standard 10x10 ft room", CatalogItemUnit.PIECE, "12000"),
    ("Carpenter Labour", "carpenter-labour.jpg", "Skilled carpenter, on-site work, per hour", CatalogItemUnit.HOUR, "650"),
    ("Electrician Labour", "electrician-labour.jpg", "Licensed electrician, wiring + fittings, per hour", CatalogItemUnit.HOUR, "700"),
    ("Vitrified Tiles", "tiles.jpg", "Premium vitrified flooring tiles — rate per sq ft including laying", CatalogItemUnit.SQ_FT, "95"),
    ("POP Work", "pop-work.jpg", "Plaster of Paris ceiling / cornice work — rate per sq ft", CatalogItemUnit.SQ_FT, "55"),
    ("Glass Partition", "glass-partition.jpg", "12mm toughened glass partition with SS fittings — per sq ft", CatalogItemUnit.SQ_FT, "450"),
]


def create_catalog(session: Session, owner: User) -> dict[str, CatalogItem]:
    _log("creating catalog items + uploading images")
    items: dict[str, CatalogItem] = {}
    for name, filename, description, unit, rate in CATALOG:
        item = catalog_item_service.create_catalog_item(
            session=session,
            current_user=owner,
            data=CatalogItemCreate(
                name=name,
                description=description,
                unit=unit,
                default_rate=_d(rate),
                gst_percent=_d("18"),
            ),
        )
        items[name] = item

        image_path = CATALOG_DIR / filename
        if not image_path.exists():
            _log(f"  WARNING: image missing for {name} → {image_path}")
            continue

        file_bytes = image_path.read_bytes()
        content_type = mimetypes.guess_type(filename)[0] or "image/jpeg"
        attachment_service.upload_and_save_attachment(
            session=session,
            business_id=owner.business_id,
            entity_type=AttachmentEntityType.CATALOG,
            entity_id=item.id,
            file_bytes=file_bytes,
            filename=filename,
            content_type=content_type,
            file_size=len(file_bytes),
        )
        _log(f"  ✓ {name}")
    return items


# ---------- customers + leads ----------

CUSTOMERS = [
    {
        "name": "Rajesh Mehta",
        "phone": "+91 98290 11111",
        "email": "rajesh.mehta@gmail.com",
        "address": "A-42, Vaishali Nagar",
        "project": "Modular Kitchen + Wardrobe",
        "stage": "WIP",
        "estimated_value": "450000",
        "source": LeadSource.REFERRAL,
        "notes": "Referred by Sharma ji from Malviya Nagar. Urgent — shift-in in 6 weeks. Wife prefers L-shape kitchen, white shutters.",
    },
    {
        "name": "Anjali Verma",
        "phone": "+91 98290 22222",
        "email": "anjali.verma@outlook.com",
        "address": "B-7, Mansarovar Extension",
        "project": "Full 3BHK flat interiors",
        "stage": "WIP",
        "estimated_value": "1200000",
        "source": LeadSource.INSTAGRAM,
        "notes": "3BHK flat, 1450 sq ft. Full interiors including kitchen, wardrobes, beds, TV unit, false ceiling. Budget ~12L. Possession in 3 months.",
    },
    {
        "name": "Suresh Agarwal",
        "phone": "+91 98290 33333",
        "email": "suresh.agarwal@agarwaltraders.in",
        "address": "C-Scheme, Office 204, Ashok Marg",
        "project": "Office cabin renovation",
        "stage": "Interested",
        "estimated_value": "280000",
        "source": LeadSource.JUSTDIAL,
        "notes": "MD cabin + conference room. Wants premium look — wooden panels, glass partition. Quote sent, awaiting GM's approval.",
    },
    {
        "name": "Priya Sharma",
        "phone": "+91 98290 44444",
        "email": "priya.s@gmail.com",
        "address": "D-21, Jagatpura",
        "project": "Kids room + master bedroom",
        "stage": "Site Visit Scheduled",
        "estimated_value": "350000",
        "source": LeadSource.WHATSAPP,
        "notes": "Two rooms. Kids room needs bunk bed + study. Master needs wardrobe + dressing. Site visit done, measurements taken — quote banana hai.",
    },
    {
        "name": "Mohit Gupta",
        "phone": "+91 98290 55555",
        "email": None,
        "address": "Pratap Nagar, Sector 5",
        "project": "Kitchen renovation",
        "stage": "New Enquiry",
        "estimated_value": "180000",
        "source": LeadSource.WHATSAPP,
        "notes": "Existing kitchen break karke modular banwana hai. 9x10 ft. Budget around 1.5-2L.",
    },
    {
        "name": "Deepak Joshi",
        "phone": "+91 98290 66666",
        "email": "deepakjoshi.shop@gmail.com",
        "address": "MI Road, Shop 8",
        "project": "Shop counter + display unit",
        "stage": "WIP",
        "estimated_value": "95000",
        "source": LeadSource.WALK_IN,
        "notes": "Small sweet shop. Counter + glass display + ceiling lights. Walk-in from last month.",
    },
    {
        "name": "Kavita Reddy",
        "phone": "+91 98290 77777",
        "email": "kavitareddy@gmail.com",
        "address": "Tonk Road, Apartment 305",
        "project": "Living room makeover",
        "stage": "Completed",
        "estimated_value": "220000",
        "source": LeadSource.REFERRAL,
        "notes": "Living room renovation — TV unit, false ceiling, accent wall, new flooring. Project completed, client very happy, might refer friends.",
    },
    {
        "name": "Amit Patel",
        "phone": "+91 98290 88888",
        "email": "amit@spiceroute.com",
        "address": "Civil Lines, Plot 12",
        "project": "Restaurant interiors",
        "stage": "New Enquiry",
        "estimated_value": "800000",
        "source": LeadSource.WALK_IN,
        "notes": "Opening a 40-seater restaurant. Needs full interiors — seating, kitchen counter, wash area, lighting. First meeting scheduled.",
    },
]


def create_customers_and_leads(
    session: Session,
    owner: User,
    stages: dict[str, PipelineStage],
) -> list[tuple[dict, Customer, Lead]]:
    _log("creating customers + leads")
    new_enquiry = stages["New Enquiry"]
    results: list[tuple[dict, Customer, Lead]] = []

    for data in CUSTOMERS:
        customer = customer_service.create_customer(
            session=session,
            current_user=owner,
            data=CustomerCreateRequest(
                name=data["name"],
                phone=data["phone"],
                email=data["email"],
                address=data["address"],
                city=BUSINESS_CITY,
                state="Rajasthan",
            ),
        )

        lead = lead_service.create_lead(
            session=session,
            current_user=owner,
            data=LeadCreate(
                customer_id=customer.id,
                stage_id=new_enquiry.id,
                title=data["project"],
                source=data["source"],
                estimated_value=_d(data["estimated_value"]),
                notes=data["notes"],
            ),
        )

        # Advance to target stage (auto-logs status_change activity).
        target_stage_name = data["stage"]
        if target_stage_name != "New Enquiry":
            lead = lead_service.move_lead_stage(
                session=session,
                current_user=owner,
                lead_id=lead.id,
                new_stage_id=stages[target_stage_name].id,
            )

        # A few manual activity entries to fill out the timeline.
        create_lead_activity(session, LeadActivity(
            lead_id=lead.id,
            type=LeadActivityType.CALL,
            description=f"Initial call with {data['name']} — discussed requirements and timeline.",
            created_by=owner.id,
        ))
        create_lead_activity(session, LeadActivity(
            lead_id=lead.id,
            type=LeadActivityType.WHATSAPP,
            description="Shared portfolio and a few reference images on WhatsApp.",
            created_by=owner.id,
        ))

        results.append((data, customer, lead))
    return results


# ---------- invoices + payments ----------

def _line(item: CatalogItem, qty: str) -> dict:
    """Build kwargs dict for InvoiceItemCreate from a catalog item."""
    return {
        "name": item.name,
        "description": item.description or item.name,
        "unit": item.unit.value,
        "catalog_item_id": item.id,
        "quantity": _d(qty),
        "unit_price": item.default_rate,
        "gst_percent": item.gst_percent,
    }


def _make_invoice(
    session: Session,
    owner: User,
    lead: Lead,
    status: InvoiceStatus,
    issued_offset: int,
    due_offset: int,
    lines: list[dict],
) -> Invoice:
    issued = _days_ago(issued_offset)
    due = _days_ago(due_offset)  # negative offset = future date
    invoice = invoice_service.create_invoice(
        session=session,
        current_user=owner,
        data=InvoiceCreateWithItems(
            invoice=InvoiceData(
                lead_id=lead.id,
                status=status,
                issued_date=issued,
                due_date=due,
            ),
            items=[InvoiceItemCreate(**line) for line in lines],
        ),
    )
    return invoice


def _record_payment(
    session: Session,
    owner: User,
    invoice: Invoice,
    amount: str,
    method: PaymentMethod,
    days_ago: int,
    reference: str | None = None,
) -> None:
    payment_service.create_payment(
        session=session,
        current_user=owner,
        data=PaymentCreate(
            invoice_id=invoice.id,
            amount=_d(amount),
            payment_method=method,
            payment_date=_days_ago(days_ago),
            reference=reference,
        ),
    )


def create_invoices_and_payments(
    session: Session,
    owner: User,
    leads_by_name: dict[str, Lead],
    catalog: dict[str, CatalogItem],
) -> None:
    _log("creating invoices + payments")

    c = catalog

    # 1. Rajesh — small wardrobe + paint, fully paid (oldest)
    inv1 = _make_invoice(
        session, owner, leads_by_name["Rajesh Mehta"], InvoiceStatus.APPROVED,
        issued_offset=68, due_offset=53,
        lines=[
            dict(_line(c["Wardrobe (Sliding 3-door)"], "1")),
            dict(_line(c["Asian Royale Paint (per room)"], "1")),
        ],
    )
    _record_payment(session, owner, inv1, "90860", PaymentMethod.BANK_TRANSFER, 60, "UTR902834")

    # 2. Kavita — living room part 1 (paid)
    inv2 = _make_invoice(
        session, owner, leads_by_name["Kavita Reddy"], InvoiceStatus.APPROVED,
        issued_offset=63, due_offset=48,
        lines=[
            dict(_line(c["Wardrobe (Sliding 3-door)"], "1")),
            dict(_line(c["Asian Royale Paint (per room)"], "3")),
            dict(_line(c["Carpenter Labour"], "12")),
        ],
    )
    _record_payment(session, owner, inv2, str(inv2.total_amount), PaymentMethod.UPI, 55, "UPI-8834")

    # 3. Deepak — small labour bill (paid)
    inv3 = _make_invoice(
        session, owner, leads_by_name["Deepak Joshi"], InvoiceStatus.APPROVED,
        issued_offset=55, due_offset=40,
        lines=[
            dict(_line(c["Electrician Labour"], "4")),
            dict(_line(c["Carpenter Labour"], "6")),
        ],
    )
    _record_payment(session, owner, inv3, str(inv3.total_amount), PaymentMethod.CASH, 42)

    # 4. Anjali — advance modular kitchen (paid in full)
    inv4 = _make_invoice(
        session, owner, leads_by_name["Anjali Verma"], InvoiceStatus.APPROVED,
        issued_offset=53, due_offset=38,
        lines=[
            dict(_line(c["Modular Kitchen"], "1")),
        ],
    )
    _record_payment(session, owner, inv4, str(inv4.total_amount), PaymentMethod.BANK_TRANSFER, 45, "UTR884120")

    # 5. Deepak — main shop interiors, APPROVED + OVERDUE (no payment)
    _make_invoice(
        session, owner, leads_by_name["Deepak Joshi"], InvoiceStatus.APPROVED,
        issued_offset=43, due_offset=28,
        lines=[
            dict(_line(c["POP Work"], "150")),
            dict(_line(c["Carpenter Labour"], "20")),
            dict(_line(c["Glass Partition"], "12")),
        ],
    )

    # 6. Kavita — living room part 2, glass + POP (paid)
    inv6 = _make_invoice(
        session, owner, leads_by_name["Kavita Reddy"], InvoiceStatus.APPROVED,
        issued_offset=39, due_offset=24,
        lines=[
            dict(_line(c["Glass Partition"], "30")),
            dict(_line(c["POP Work"], "40")),
        ],
    )
    _record_payment(session, owner, inv6, str(inv6.total_amount), PaymentMethod.CASH, 26)

    # 7. Anjali — tiles + paint top-up, PARTIAL + OVERDUE
    inv7 = _make_invoice(
        session, owner, leads_by_name["Anjali Verma"], InvoiceStatus.APPROVED,
        issued_offset=34, due_offset=19,
        lines=[
            dict(_line(c["Vitrified Tiles"], "80")),
            dict(_line(c["Asian Royale Paint (per room)"], "1")),
        ],
    )
    _record_payment(session, owner, inv7, "10000", PaymentMethod.UPI, 25, "UPI-9923")

    # 8. Rajesh — main kitchen + wardrobe bundle, PARTIAL (not overdue)
    inv8 = _make_invoice(
        session, owner, leads_by_name["Rajesh Mehta"], InvoiceStatus.APPROVED,
        issued_offset=10, due_offset=-5,
        lines=[
            dict(_line(c["Modular Kitchen"], "1")),
            dict(_line(c["Wardrobe (Sliding 3-door)"], "1")),
            dict(_line(c["Hettich Hardware Set"], "2")),
        ],
    )
    _record_payment(session, owner, inv8, "150000", PaymentMethod.UPI, 5, "UPI-1287")

    # 9. Anjali — main 3BHK bill, APPROVED (not overdue, biggest invoice)
    _make_invoice(
        session, owner, leads_by_name["Anjali Verma"], InvoiceStatus.APPROVED,
        issued_offset=12, due_offset=-3,
        lines=[
            dict(_line(c["False Ceiling"], "200")),
            dict(_line(c["Asian Royale Paint (per room)"], "4")),
            dict(_line(c["Carpenter Labour"], "40")),
            dict(_line(c["Vitrified Tiles"], "600")),
        ],
    )

    # 10. Suresh — office cabin, SENT (quote for approval)
    _make_invoice(
        session, owner, leads_by_name["Suresh Agarwal"], InvoiceStatus.SENT,
        issued_offset=8, due_offset=-7,
        lines=[
            dict(_line(c["Wardrobe (Sliding 3-door)"], "1")),
            dict(_line(c["False Ceiling"], "150")),
            dict(_line(c["Glass Partition"], "40")),
        ],
    )

    # 11. Priya — kids + master estimate, DRAFT
    _make_invoice(
        session, owner, leads_by_name["Priya Sharma"], InvoiceStatus.DRAFT,
        issued_offset=5, due_offset=-10,
        lines=[
            dict(_line(c["Wardrobe (Sliding 3-door)"], "1")),
            dict(_line(c["Asian Royale Paint (per room)"], "2")),
            dict(_line(c["Vitrified Tiles"], "100")),
        ],
    )

    # 12. Rajesh — small add-on bill, DRAFT
    _make_invoice(
        session, owner, leads_by_name["Rajesh Mehta"], InvoiceStatus.DRAFT,
        issued_offset=3, due_offset=-12,
        lines=[
            dict(_line(c["Hettich Hardware Set"], "2")),
            dict(_line(c["Electrician Labour"], "8")),
        ],
    )


# ---------- follow-ups ----------

def create_followups(
    session: Session,
    owner: User,
    leads_by_name: dict[str, Lead],
) -> None:
    _log("creating follow-ups")

    plan = [
        # (customer, days_offset, hour, note, completed)
        ("Suresh Agarwal", -5, 11, "Quote approval follow karo — GM ki meeting ho chuki hai", False),
        ("Priya Sharma", -3, 15, "Site visit date confirm karo phone par", False),
        ("Mohit Gupta", -1, 16, "Budget finalize hua kya puchho, warna drop kar denge", False),
        ("Anjali Verma", 0, 11, "Site par check karo — false ceiling work ongoing", False),
        ("Rajesh Mehta", 0, 16, "Remaining payment reminder — polite tone", False),
        ("Amit Patel", 2, 12, "Restaurant walk-through for measurements", False),
        ("Deepak Joshi", 4, 14, "Delivery date confirm + final payment collect", False),
        ("Anjali Verma", 7, 11, "WIP review — carpentry + electrical check", False),
        ("Kavita Reddy", -10, 15, "Handover hua, feedback liya, photos click kiye", True),
    ]

    for customer_name, days_offset, hour, note, completed in plan:
        lead = leads_by_name[customer_name]
        scheduled_at = _dt_at(date.today() + timedelta(days=days_offset), hour=hour)
        followup = lead_followup_service.create_followup(
            session=session,
            current_user=owner,
            data=LeadFollowupCreate(
                lead_id=lead.id,
                scheduled_at=scheduled_at,
                note=note,
            ),
        )
        if completed:
            lead_followup_service.mark_followup_done(
                session=session,
                current_user=owner,
                followup_id=followup.id,
                data=LeadFollowupDone(note=note),
            )


# ---------- main ----------

def main() -> int:
    if not CATALOG_DIR.exists():
        print(f"ERROR: catalog assets folder missing: {CATALOG_DIR}", file=sys.stderr)
        print("Create the folder and drop in the 10 images before running.", file=sys.stderr)
        return 1

    with Session(engine) as session:
        wipe_existing(session)
        owner, business = create_business_and_owner(session)
        stages = setup_pipeline(session, business.id)
        catalog = create_catalog(session, owner)
        customer_leads = create_customers_and_leads(session, owner, stages)

        leads_by_name = {data["name"]: lead for (data, _c, lead) in customer_leads}
        create_invoices_and_payments(session, owner, leads_by_name, catalog)
        create_followups(session, owner, leads_by_name)

        session.refresh(business)

    print()
    print("=" * 60)
    print("  SellNSettle demo account ready")
    print("=" * 60)
    print(f"  Business   : {BUSINESS_NAME} ({BUSINESS_CITY})")
    print(f"  Email      : {OWNER_EMAIL}")
    print(f"  Password   : {OWNER_PASSWORD}")
    print(f"  Customers  : {len(CUSTOMERS)}")
    print(f"  Catalog    : {len(CATALOG)}")
    print(f"  Invoices   : 12  (2 overdue, 2 drafts, 1 sent, 2 approved, 2 partial, 3 paid)")
    print(f"  Follow-ups : 9   (3 overdue, 2 today, 3 upcoming, 1 completed)")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
