"""
Invoice PDF generation service.
Renders HTML template, converts to PDF via WeasyPrint, then uploads to R2.
"""

from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from fastapi import HTTPException, status
from sqlmodel import Session

from app.core.amount_to_words import amount_to_words_inr
from app.core.storage import upload_bytes_to_r2
from app.models.business import Business
from app.models.customer import Customer
from app.models.invoice import Invoice
from app.models.invoice_item import InvoiceItem


TEMPLATE_DIR = Path(__file__).parent.parent / "templates"


def _get_template_environment():
    try:
        from jinja2 import Environment, FileSystemLoader, select_autoescape
    except ImportError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="PDF generation dependencies are not installed",
        ) from exc

    return Environment(
        loader=FileSystemLoader(str(TEMPLATE_DIR)),
        autoescape=select_autoescape(["html", "xml"]),
    )


def _render_pdf(html_content: str) -> bytes:
    try:
        from weasyprint import HTML
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="WeasyPrint is unavailable. Install its system libraries before generating PDFs.",
        ) from exc

    return HTML(string=html_content, base_url=str(TEMPLATE_DIR)).write_pdf()


def format_inr(amount: Decimal | float | int | None) -> str:
    """Format a number as Indian Rupees."""
    if amount is None:
        return "Rs. 0"

    value = Decimal(str(amount)).quantize(Decimal("0.01"))
    sign = "-" if value < 0 else ""
    value = abs(value)
    whole = int(value)
    fraction = int((value - Decimal(whole)) * 100)

    digits = str(whole)
    if len(digits) > 3:
        last_three = digits[-3:]
        leading = digits[:-3]
        parts: list[str] = []
        while len(leading) > 2:
            parts.insert(0, leading[-2:])
            leading = leading[:-2]
        if leading:
            parts.insert(0, leading)
        formatted_whole = ",".join(parts + [last_three])
    else:
        formatted_whole = digits

    return f"{sign}Rs. {formatted_whole}.{fraction:02d}"


def generate_invoice_pdf(
    session: Session,
    invoice: Invoice,
    business: Business,
    customer: Customer,
    items: list[InvoiceItem],
    payments_total: Decimal = Decimal("0"),
) -> str:
    """
    Generate a PDF for an invoice and upload it to R2.
    """
    subtotal = invoice.subtotal
    tax_total = invoice.tax_total
    total_amount = invoice.total_amount

    items_with_tax: list[dict] = []
    for item in items:
        line_total = item.amount
        gst_amount = (line_total * item.gst_percent) / Decimal("100")
        items_with_tax.append(
            {
                "name": item.name or item.description,
                "description": item.description,
                "unit": item.unit or "-",
                "quantity": item.quantity,
                "rate": item.unit_price,
                "gst_percent": item.gst_percent,
                "line_total": line_total,
                "gst_amount": gst_amount,
                "line_total_with_tax": line_total + gst_amount,
            }
        )

    is_same_state = True
    cgst_total = tax_total / Decimal("2")
    sgst_total = tax_total / Decimal("2")

    gst_rates = {item.gst_percent for item in items}
    half_gst_label = ""
    if len(gst_rates) == 1:
        half_rate = (next(iter(gst_rates)) / Decimal("2")).normalize()
        half_gst_label = f"{half_rate}%"

    has_payment_details = any(
        [
            business.bank_name,
            business.bank_account_number,
            business.bank_ifsc,
            business.upi_id,
        ]
    )

    issued_date = invoice.issued_date.strftime("%d %b %Y")
    due_date = invoice.due_date.strftime("%d %b %Y")
    amount_paid = payments_total
    balance_due = total_amount - amount_paid
    amount_in_words = amount_to_words_inr(float(balance_due if amount_paid > 0 else total_amount))

    jinja_env = _get_template_environment()
    template = jinja_env.get_template("invoice_pdf.html")
    html_content = template.render(
        business=business,
        invoice={
            "invoice_number": invoice.invoice_number,
            "issued_date": issued_date,
            "due_date": due_date,
            "status_label": invoice.status.value,
            "status_class": invoice.status.value,
        },
        customer=customer,
        items=items_with_tax,
        subtotal=subtotal,
        tax_total=tax_total,
        cgst_total=cgst_total,
        sgst_total=sgst_total,
        is_same_state=is_same_state,
        half_gst_label=half_gst_label,
        total_amount=total_amount,
        amount_paid=amount_paid,
        balance_due=balance_due,
        amount_in_words=amount_in_words,
        has_payment_details=has_payment_details,
        format_inr=format_inr,
    )

    pdf_bytes = _render_pdf(html_content)

    filename = f"invoices/{business.id}/{invoice.invoice_number}.pdf"
    pdf_url = upload_bytes_to_r2(
        file_bytes=pdf_bytes,
        filename=filename,
        content_type="application/pdf",
    )

    invoice.pdf_url = pdf_url
    invoice.pdf_generated_at = datetime.now(timezone.utc)
    session.add(invoice)
    session.commit()
    session.refresh(invoice)

    return pdf_url
