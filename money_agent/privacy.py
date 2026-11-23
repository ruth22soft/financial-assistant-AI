"""Mask account and card numbers before any text is sent to the AI model."""
import re

# 8+ digits in a row that are not part of an amount like 1,250.00 (account numbers, phone numbers).
_LONG_NUMBER = re.compile(r"(?<![\d.,])\d{8,}(?![\d.,]*\d)")
# Card-style numbers written in groups: 1234 5678 9012 3456
_GROUPED_NUMBER = re.compile(r"\b\d{4}(?:[ -]\d{4}){2,}\b")


def _keep_last_four(match: re.Match) -> str:
    digits = re.sub(r"\D", "", match.group(0))
    return "****" + digits[-4:]


def mask_account_numbers(text: str) -> str:
    """Replace long numbers with ****1234 so only the last four digits leave the device.

    Amounts (80.00, 1,250.00) and dates (2026-11-23, 23/11/2026) are left alone.
    """
    text = _GROUPED_NUMBER.sub(_keep_last_four, text)
    return _LONG_NUMBER.sub(_keep_last_four, text)
