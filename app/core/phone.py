import re


def normalize_phone_value(phone: str) -> str:
    phone = phone.strip()
    if not phone:
        return ""

    has_leading_plus = phone.startswith("+")
    digits_only = re.sub(r"\D", "", phone)

    if has_leading_plus:
        return f"+{digits_only}"

    return digits_only
