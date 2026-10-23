"""PII masking for tool outputs (ID-2; applied at defense level D2+)."""

from __future__ import annotations

from typing import Any


def mask_email(email: str) -> str:
    local, _, domain = email.partition("@")
    return f"{local[:1]}****@{domain}" if domain else "****"


def mask_phone(phone: str) -> str:
    digits = "".join(ch for ch in phone if ch.isdigit())
    return "*" * max(len(digits) - 4, 0) + digits[-4:]


def mask_address(address: dict[str, Any]) -> dict[str, str]:
    """Keep city/state so the customer can recognise the address; hide street and most of the PIN."""
    pin = str(address.get("pin", ""))
    return {
        "line1": "****",
        "city": str(address.get("city", "")),
        "state": str(address.get("state", "")),
        "pin": "****" + pin[-2:] if pin else "",
        "country": str(address.get("country", "India")),
    }


def mask_upi(upi: str) -> str:
    handle, _, bank = upi.partition("@")
    return f"{handle[:2]}****@{bank}" if bank else "****"
