from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from datetime import date
from typing import List

from src.backend.db.database import get_db
from src.backend.db.models import User, Receipt, ReceiptItem, Category
from src.backend.api.deps import get_current_user
from src.backend.schemas import DashboardSummaryResponse, CategorySpending

router = APIRouter(prefix="/statistics", tags=["Statistics"])

@router.get("/summary", response_model=DashboardSummaryResponse)
async def get_dashboard_summary(
        current_user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db)
):
    """
    GET /api/v1/statistics/summary
    Aggregates dashboard data natively in PostgreSQL to prevent mobile data bloat.
    """

    user_id = current_user.id
    today = date.today()

    first_day_this_month = today.replace(day=1)

    if today.month == 1:
        first_day_last_month = today.replace(year=today.year - 1, month=12, day=1)
    else:
        first_day_last_month = today.replace(month=today.month - 1, day=1)

    valid_statuses = ["COMPLETED", "MANUALLY_CREATED", "MANUALLY_CORRECTED"]

    stmt_this_month = (
        select(func.sum(Receipt.total_amount))
        .where(
            Receipt.user_id == user_id,
            Receipt.purchase_date >= first_day_this_month,
            Receipt.status.in_(valid_statuses)
        )
    )
    this_month_total = (await db.execute(stmt_this_month)).scalar() or 0.0

    stmt_last_month = (
        select(func.sum(Receipt.total_amount))
        .where(
            Receipt.user_id == user_id,
            Receipt.purchase_date >= first_day_last_month,
            Receipt.purchase_date < first_day_this_month,
            Receipt.status.in_(valid_statuses)
        )
    )
    last_month_total = (await db.execute(stmt_last_month)).scalar() or 0.0

    stmt_categories = (
        select(
            ReceiptItem.category_id,
            Category.name.label("category_name"),
            func.sum(ReceiptItem.price * ReceiptItem.quantity).label("total_spent")
        )
        .join(Receipt, Receipt.id == ReceiptItem.receipt_id)
        .outerjoin(Category, Category.id == ReceiptItem.category_id)
        .where(
            Receipt.user_id == user_id,
            Receipt.purchase_date >= first_day_this_month,
            Receipt.status.in_(valid_statuses)
        )
        .group_by(ReceiptItem.category_id, Category.name)
        .order_by(func.sum(ReceiptItem.price * ReceiptItem.quantity).desc())
    )

    result_categories = await db.execute(stmt_categories)

    category_breakdown = []
    for row in result_categories:
        category_breakdown.append(
            CategorySpending(
                category_id=row.category_id,
                category_name=row.category_name or "Uncategorized",
                total_amount=round(float(row.total_spent or 0.0), 2)
            )
        )

    return DashboardSummaryResponse(
        current_month=first_day_this_month.strftime("%Y-%m"),
        total_spent_this_month=round(float(this_month_total), 2),
        total_spent_last_month=round(float(last_month_total), 2),
        category_breakdown=category_breakdown
    )