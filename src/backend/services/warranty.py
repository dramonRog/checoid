"""EU-standard warranty date helpers for receipt items."""
from __future__ import annotations

from datetime import date
from typing import Optional, Tuple


def add_two_years_eu_standard(purchase_date: date) -> date:
    try:
        return purchase_date.replace(year=purchase_date.year + 2)
    except ValueError:
        # Feb 29 → Feb 28 in non-leap target year
        return purchase_date.replace(year=purchase_date.year + 2, month=2, day=28)


def apply_warranty(
    under_warranty: bool,
    purchase_date: Optional[date],
    explicit_end: Optional[date] = None,
) -> Tuple[bool, Optional[date]]:
    """
    If under warranty: keep/set end date (explicit or purchase_date + 2 years).
    If not: clear end date.
    """
    if not under_warranty:
        return False, None
    if explicit_end is not None:
        return True, explicit_end
    if purchase_date is not None:
        return True, add_two_years_eu_standard(purchase_date)
    return True, None
