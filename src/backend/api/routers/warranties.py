from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from datetime import date, datetime

from src.backend.db.database import get_db
from src.backend.db.models import User, Receipt, ReceiptItem, Company
from src.backend.api.deps import get_current_user
from src.backend.schemas import WarrantyActiveResponse

router = APIRouter(prefix="/warranties", tags=["Warranties"])

def add_two_years_eu_standard(purchase_date: date) -> date:
    try:
        return purchase_date.replace(year=purchase_date.year + 2)
    except ValueError: # If was bought in February 29
        return purchase_date.replace(year=purchase_date.year + 2, month=2, day=28)


@router.get("/active", response_model=list[WarrantyActiveResponse])
async def get_active_warranties(
        current_user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db)
):
    """
    Fetches all items currently under warranty.
    Calculates standard 2-year EU guarantee if an explicit end date isn't provided.
    """

    stmt = (
        select(ReceiptItem, Receipt, Company)
        .join(Receipt, ReceiptItem.receipt_id == Receipt.id)
        .outerjoin(Company, Receipt.company_id == Company.id)
        .where(Receipt.user_id == current_user.id)
        .where(ReceiptItem.is_under_warranty == True)
    )

    result = await db.execute(stmt)
    rows = result.all()

    today = datetime.now().date()
    active_warranties = []

    for item, receipt, company in rows:
        if not receipt.purchase_date:
            continue

        end_date = item.warranty_end_date or add_two_years_eu_standard(receipt.purchase_date)

        days_remaining = (end_date - today).days

        if days_remaining >= 0:
            active_warranties.append({
                "item_id": item.id,
                "receipt_id": receipt.id,
                "item_name": item.name,
                "company_name": company.name if company else "Unknown",
                "purchase_date": receipt.purchase_date,
                "warranty_end_date": end_date,
                "days_remaining": days_remaining
            })

    active_warranties.sort(key=lambda x: x["days_remaining"])

    return active_warranties


