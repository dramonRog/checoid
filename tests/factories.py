"""Test data factories (avoid importing from conftest — name clashes with site-packages)."""
from __future__ import annotations

from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from src.backend.db.models import Receipt, ReceiptItem


async def create_receipt_with_warranty_item(
    db_session: AsyncSession,
    user_id: int,
    *,
    under_warranty: bool = True,
    purchase: date = date(2025, 6, 1),
    warranty_end: date | None = date(2027, 6, 1),
) -> tuple[Receipt, ReceiptItem]:
    receipt = Receipt(
        user_id=user_id,
        purchase_date=purchase,
        total_amount=199.99,
        status="MANUALLY_CREATED",
        shop_name="MediaMarkt",
        has_warranty_items=under_warranty,
        image_url="/media/receipts/test.jpg",
    )
    db_session.add(receipt)
    await db_session.flush()

    item = ReceiptItem(
        receipt_id=receipt.id,
        name="Klawiatura",
        quantity=1,
        price=199.99,
        is_under_warranty=under_warranty,
        warranty_end_date=warranty_end if under_warranty else None,
    )
    db_session.add(item)
    await db_session.commit()
    await db_session.refresh(receipt)
    await db_session.refresh(item)
    return receipt, item
