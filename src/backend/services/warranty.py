"""EU-standard warranty date helpers for receipt items."""
from __future__ import annotations

from datetime import date
from typing import Iterable, Literal, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.backend.db.models import Receipt, ReceiptItem
from src.backend.core.storage import sync_receipt_image_storage

WarrantyFilterStatus = Literal["active", "expiring", "expired", "all"]


def any_under_warranty(flags: Iterable[bool]) -> bool:
    """True if at least one item is under warranty (for Receipt.has_warranty_items)."""
    return any(flags)


async def sync_receipt_has_warranty_items(
    db: AsyncSession,
    receipt_id: int,
    receipt: Optional[Receipt] = None,
) -> bool:
    """Recompute Receipt.has_warranty_items from current line items."""
    stmt = (
        select(ReceiptItem.is_under_warranty)
        .where(ReceiptItem.receipt_id == receipt_id)
    )
    result = await db.execute(stmt)
    has_warranty = any_under_warranty(bool(flag) for flag in result.scalars().all())

    target = receipt if receipt is not None else await db.get(Receipt, receipt_id)
    if target is not None:
        target.has_warranty_items = has_warranty

    return has_warranty


async def finalize_receipt_warranty_state(db: AsyncSession, receipt: Receipt) -> None:
    """
    Sync has_warranty_items and relocate receipt image:
    warranty → Azure, non-warranty → local (when split storage is enabled).
    """
    has_warranty = await sync_receipt_has_warranty_items(db, receipt.id, receipt)
    if receipt.image_url:
        receipt.image_url = sync_receipt_image_storage(receipt.image_url, has_warranty)


def add_two_years_eu_standard(purchase_date: date) -> date:
    try:
        return purchase_date.replace(year=purchase_date.year + 2)
    except ValueError:
        # Feb 29 → Feb 28 in non-leap target year
        return purchase_date.replace(year=purchase_date.year + 2, month=2, day=28)


def resolve_warranty_end_date(
    purchase_date: Optional[date],
    explicit_end: Optional[date] = None,
) -> Optional[date]:
    """Explicit end date wins; else EU +2 years from purchase_date."""
    if explicit_end is not None:
        return explicit_end
    if purchase_date is not None:
        return add_two_years_eu_standard(purchase_date)
    return None


def warranty_lifecycle_status(days_remaining: int, days_ahead: int) -> str:
    if days_remaining < 0:
        return "expired"
    if days_remaining <= days_ahead:
        return "expiring"
    return "active"


def matches_warranty_filter(
    days_remaining: int,
    status: WarrantyFilterStatus,
    days_ahead: int,
) -> bool:
    if status == "all":
        return True
    if status == "active":
        return days_remaining >= 0
    if status == "expiring":
        return 0 <= days_remaining <= days_ahead
    if status == "expired":
        return days_remaining < 0
    return False


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
