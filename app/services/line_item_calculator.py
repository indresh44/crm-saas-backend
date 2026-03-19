from decimal import Decimal, ROUND_HALF_UP


def calculate_totals(line_items: list) -> dict:
    """
    Calculate subtotal, tax_total, and total_amount from a list of line items.

    Each line_item must have: quantity, unit_price, gst_percent
    """
    subtotal = Decimal("0.00")
    tax_total = Decimal("0.00")
    items_with_totals = []

    for item in line_items:
        qty = Decimal(str(item.quantity))
        rate = Decimal(str(item.unit_price))
        gst = Decimal(str(item.gst_percent))

        line_total = (qty * rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        tax_amount = (line_total * gst / Decimal("100")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        subtotal += line_total
        tax_total += tax_amount

        if isinstance(item, dict):
            item_data = dict(item)
        elif hasattr(item, "model_dump"):
            item_data = item.model_dump()
        else:
            item_data = vars(item).copy()

        item_data["amount"] = line_total
        items_with_totals.append(item_data)

    subtotal = subtotal.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    tax_total = tax_total.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    total_amount = (subtotal + tax_total).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    return {
        "subtotal": subtotal,
        "tax_total": tax_total,
        "total_amount": total_amount,
        "items_with_totals": items_with_totals,
    }
