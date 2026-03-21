"""
Convert a number to words for invoice display.
"""

from fastapi import HTTPException, status


def _to_words(value: int) -> str:
    try:
        from num2words import num2words
    except ImportError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="PDF generation dependencies are not installed",
        ) from exc

    try:
        return num2words(value, lang="en_IN").title()
    except NotImplementedError:
        return num2words(value, lang="en").title()


def amount_to_words_inr(amount: float) -> str:
    """
    Convert an amount to Indian English words.
    """
    if amount < 0:
        return f"Negative {amount_to_words_inr(abs(amount))}"

    rupees = int(amount)
    paise = round((amount - rupees) * 100)

    if paise == 100:
        rupees += 1
        paise = 0

    rupee_words = _to_words(rupees)
    if paise > 0:
        paise_words = _to_words(paise)
        return f"Rupees {rupee_words} and {paise_words} Paise Only"
    return f"Rupees {rupee_words} Only"
