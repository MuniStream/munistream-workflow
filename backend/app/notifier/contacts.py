"""Where a citizen's notifications are delivered.

The account email and phone come from the identity provider (LlaveMX syncs
them into `Customer`). On top of that, the admin can define Profile Fields of
type `phone` or `email` that citizens fill in themselves: those are added to the
account ones, deduplicated, and notifications go to all of them.
"""
import re
from typing import Dict, List, Optional

from beanie import PydanticObjectId

from ..models.customer import Customer
from ..models.profile_field_definition import ProfileFieldDefinition
from ..models.user_profile import UserProfile

DEFAULT_COUNTRY_CODE = "52"

# Synthetic address given to LlaveMX accounts without an email
# (public_auth.get_current_customer): not a real mailbox.
_SYNTHETIC_EMAIL_DOMAINS = ("@llavemx.local",)

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


async def resolve_customer(user_id: Optional[str]) -> Optional[Customer]:
    """Instances store the Customer _id; some older paths store the Keycloak
    sub. Both are accepted."""
    if not user_id:
        return None
    try:
        customer = await Customer.get(PydanticObjectId(user_id))
        if customer:
            return customer
    except Exception:
        pass
    return await Customer.find_one(Customer.keycloak_id == user_id)


def normalize_email(raw: Optional[str]) -> Optional[str]:
    if not raw or not isinstance(raw, str):
        return None
    email = raw.strip().lower()
    if not _EMAIL_RE.match(email):
        return None
    if email.endswith(_SYNTHETIC_EMAIL_DOMAINS):
        return None
    return email


def normalize_phone(raw: Optional[str], default_country_code: str = DEFAULT_COUNTRY_CODE) -> Optional[str]:
    """Number in international format (+<country code><number>).

    A 10-digit number without a country code is national and gets the default
    country code. The `1` Mexico used to prefix to mobiles (+52 1 ...) is no
    longer dialed and WhatsApp resolves the number without it, so it is dropped.
    """
    if not raw or not isinstance(raw, str):
        return None
    text = raw.strip()
    digits = re.sub(r"\D", "", text)
    if not digits:
        return None
    if text.startswith("00"):
        digits = digits[2:]
    elif not text.startswith("+") and len(digits) == 10:
        digits = f"{default_country_code}{digits}"
    if digits.startswith("521") and len(digits) == 13:
        digits = "52" + digits[3:]
    if len(digits) < 10 or len(digits) > 15:
        return None
    return f"+{digits}"


def _dedupe(values: List[Optional[str]]) -> List[str]:
    out: List[str] = []
    for value in values:
        if value and value not in out:
            out.append(value)
    return out


async def _profile_values(customer: Customer, field_type: str) -> List[str]:
    fields = await ProfileFieldDefinition.find(
        {"type": field_type, "active": True}
    ).to_list()
    if not fields:
        return []
    profile = await UserProfile.find_one(UserProfile.customer_id == str(customer.id))
    if not profile:
        return []
    values = []
    for field in sorted(fields, key=lambda f: f.order or 0):
        value = profile.data.get(field.field_id)
        if isinstance(value, str):
            values.append(value)
    return values


async def contact_points(
    customer: Optional[Customer],
    default_country_code: str = DEFAULT_COUNTRY_CODE,
) -> Dict[str, List[str]]:
    """{"email": [...], "whatsapp": [...]}: account values first, then the
    profile ones."""
    if not customer:
        return {"email": [], "whatsapp": []}
    emails = [customer.email] + await _profile_values(customer, "email")
    phones = [customer.phone] + await _profile_values(customer, "phone")
    return {
        "email": _dedupe([normalize_email(e) for e in emails]),
        "whatsapp": _dedupe([normalize_phone(p, default_country_code) for p in phones]),
    }
