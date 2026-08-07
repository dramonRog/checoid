from datetime import date, datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from src.backend.db.database import get_db
from src.backend.db.models import User, Receipt, ReceiptItem, Company
from src.backend.api.deps import get_current_user
from src.backend.schemas import (
    WarrantyActiveResponse,
    WarrantyVaultResponse,
    WarrantyItemUpdate,
    ReceiptItemResponse,
    CategoryResponse,
)
from src.backend.services.warranty import (
    apply_warranty,
    finalize_receipt_warranty_state,
    resolve_warranty_end_date,
    warranty_lifecycle_status,
    matches_warranty_filter,
    WarrantyFilterStatus,
)

router = APIRouter(prefix="/warranties", tags=["Warranties"])


async def _get_user_warranty_item_or_404(
    item_id: int,
    user_id: int,
    db: AsyncSession,
) -> tuple[ReceiptItem, Receipt]:
    stmt = (
        select(ReceiptItem, Receipt)
        .join(Receipt, ReceiptItem.receipt_id == Receipt.id)
        .where(ReceiptItem.id == item_id, Receipt.user_id == user_id)
        .options(selectinload(ReceiptItem.category))
    )
    result = await db.execute(stmt)
    row = result.first()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Receipt item not found.",
        )
    item, receipt = row
    return item, receipt


def _build_warranty_row(
    item: ReceiptItem,
    receipt: Receipt,
    company: Optional[Company],
    today: date,
    days_ahead: int,
) -> Optional[dict]:
    if not receipt.purchase_date:
        return None

    end_date = resolve_warranty_end_date(receipt.purchase_date, item.warranty_end_date)
    if end_date is None:
        return None

    days_remaining = (end_date - today).days
    category = None
    if item.category:
        category = CategoryResponse(id=item.category.id, name=item.category.name)

    return {
        "item_id": item.id,
        "receipt_id": receipt.id,
        "item_name": item.name,
        "company_name": company.name if company else "Unknown",
        "purchase_date": receipt.purchase_date,
        "warranty_end_date": end_date,
        "days_remaining": days_remaining,
        "image_url": receipt.image_url,
        "shop_name": receipt.shop_name,
        "store_address": receipt.store_address,
        "price": float(item.price),
        "category": category,
        "warranty_status": warranty_lifecycle_status(days_remaining, days_ahead),
    }


async def _fetch_warranty_items(db: AsyncSession, user_id: int) -> list[tuple]:
    stmt = (
        select(ReceiptItem, Receipt, Company)
        .join(Receipt, ReceiptItem.receipt_id == Receipt.id)
        .outerjoin(Company, Receipt.company_id == Company.id)
        .where(Receipt.user_id == user_id)
        .where(ReceiptItem.is_under_warranty == True)
        .options(selectinload(ReceiptItem.category))
    )
    result = await db.execute(stmt)
    return list(result.all())


@router.get("/active", response_model=list[WarrantyActiveResponse])
async def get_active_warranties(
        current_user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db),
        days_ahead: int = Query(
            30,
            ge=1,
            le=365,
            description="Window (days) used to label expiring items in lifecycle status.",
        ),
):
    """
    Items currently under warranty (days_remaining >= 0).
    EU 2-year fallback when warranty_end_date is missing.
    """
    today = datetime.now().date()
    rows = await _fetch_warranty_items(db, current_user.id)
    items: list[dict] = []

    for item, receipt, company in rows:
        row = _build_warranty_row(item, receipt, company, today, days_ahead)
        if row is None:
            continue
        if row["days_remaining"] >= 0:
            items.append(row)

    items.sort(key=lambda x: x["days_remaining"])
    return items


@router.get("", response_model=list[WarrantyVaultResponse])
@router.get("/vault", response_model=list[WarrantyVaultResponse])
async def get_warranty_vault(
        current_user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db),
        status: WarrantyFilterStatus = Query(
            "active",
            description="active | expiring | expired | all",
        ),
        days_ahead: int = Query(
            30,
            ge=1,
            le=365,
            description="Expiring soon = 0..days_ahead days left (inclusive).",
        ),
):
    """
    Warranty sejf: filter by lifecycle status.
    - active: still valid (days_remaining >= 0)
    - expiring: valid but ends within days_ahead
    - expired: past end date
    - all: every flagged warranty item
    """
    today = datetime.now().date()
    rows = await _fetch_warranty_items(db, current_user.id)
    items: list[dict] = []

    for item, receipt, company in rows:
        row = _build_warranty_row(item, receipt, company, today, days_ahead)
        if row is None:
            continue
        if matches_warranty_filter(row["days_remaining"], status, days_ahead):
            items.append(row)

    items.sort(key=lambda x: x["days_remaining"])
    return items


@router.patch("/items/{item_id}", response_model=ReceiptItemResponse)
async def update_warranty_item(
        item_id: int,
        payload: WarrantyItemUpdate,
        current_user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db),
):
    """
    Toggle or correct warranty on a single line item (sejf edit).
    Recalculates Receipt.has_warranty_items on the parent receipt.
    """
    if payload.is_under_warranty is None and payload.warranty_end_date is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Provide at least one of is_under_warranty or warranty_end_date.",
        )

    item, receipt = await _get_user_warranty_item_or_404(
        item_id, current_user.id, db
    )

    if payload.is_under_warranty is not None:
        under_flag = payload.is_under_warranty
    else:
        # Explicit end date implies warranty on
        under_flag = True

    explicit_end = payload.warranty_end_date
    if explicit_end is None and under_flag:
        explicit_end = item.warranty_end_date

    under_w, end_w = apply_warranty(
        under_flag,
        receipt.purchase_date,
        explicit_end,
    )

    item.is_under_warranty = under_w
    item.warranty_end_date = end_w
    await finalize_receipt_warranty_state(db, receipt)

    await db.commit()

    item, _ = await _get_user_warranty_item_or_404(item_id, current_user.id, db)
    return item
