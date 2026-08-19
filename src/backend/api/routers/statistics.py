from fastapi import APIRouter, Depends, Query, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from datetime import date
from typing import Optional

from src.backend.db.database import get_db
from src.backend.db.models import User, Receipt, ReceiptItem, Category
from src.backend.api.deps import get_current_user
from src.backend.schemas import (
    DashboardSummaryResponse,
    CategorySpending,
    ShopSpending,
    AnalyticsReportResponse,
    TimelineDataPoint,
)
from src.backend.services.statistics import (
    month_window,
    receipts_in_range_where,
    average_ticket,
)

router = APIRouter(prefix="/statistics", tags=["Statistics"])


async def _category_breakdown(
    db: AsyncSession,
    receipt_where,
    category_id: Optional[int] = None,
):
    """Category spend is SUM(line total). price is not a unit price."""
    conditions = [receipt_where]
    if category_id is not None:
        conditions.append(ReceiptItem.category_id == category_id)

    stmt = (
        select(
            ReceiptItem.category_id,
            Category.name.label("category_name"),
            func.sum(ReceiptItem.price).label("total_spent"),
        )
        .join(Receipt, Receipt.id == ReceiptItem.receipt_id)
        .outerjoin(Category, Category.id == ReceiptItem.category_id)
        .where(*conditions)
        .group_by(ReceiptItem.category_id, Category.name)
        .order_by(func.sum(ReceiptItem.price).desc())
    )
    rows = await db.execute(stmt)
    return [
        CategorySpending(
            category_id=row.category_id,
            category_name=row.category_name or "Uncategorized",
            total_amount=round(float(row.total_spent or 0.0), 2),
        )
        for row in rows
    ]


async def _shop_breakdown(db: AsyncSession, receipt_where):
    shop_label = func.coalesce(Receipt.shop_name, "Unknown")
    stmt = (
        select(
            shop_label.label("shop_name"),
            func.sum(Receipt.total_amount).label("total_spent"),
        )
        .where(receipt_where)
        .group_by(shop_label)
        .order_by(func.sum(Receipt.total_amount).desc())
    )
    rows = await db.execute(stmt)
    return [
        ShopSpending(
            shop_name=row.shop_name or "Unknown",
            total_amount=round(float(row.total_spent or 0.0), 2),
        )
        for row in rows
    ]


@router.get("/summary", response_model=DashboardSummaryResponse)
async def get_dashboard_summary(
        current_user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db),
        shop_name: Optional[str] = Query(
            None,
            description="Filter by shop brand (case-insensitive). Use Unknown for null shop.",
        ),
        category_id: Optional[int] = Query(
            None,
            gt=0,
            description="Only receipts that contain an item in this category.",
        ),
):
    """
    GET /api/v1/statistics/summary
    Aggregates dashboard data natively in PostgreSQL to prevent mobile data bloat.
    """

    user_id = current_user.id
    first_day_this_month, first_day_last_month, _today = month_window()

    filter_kwargs = {"shop_name": shop_name, "category_id": category_id}

    this_month_where = receipts_in_range_where(
        user_id,
        start_date=first_day_this_month,
        **filter_kwargs,
    )
    last_month_where = receipts_in_range_where(
        user_id,
        start_date=first_day_last_month,
        end_exclusive=first_day_this_month,
        **filter_kwargs,
    )

    this_month_total = float(
        (await db.execute(select(func.sum(Receipt.total_amount)).where(this_month_where))).scalar()
        or 0.0
    )
    last_month_total = float(
        (await db.execute(select(func.sum(Receipt.total_amount)).where(last_month_where))).scalar()
        or 0.0
    )
    receipt_count = int(
        (await db.execute(select(func.count(Receipt.id)).where(this_month_where))).scalar()
        or 0
    )

    return DashboardSummaryResponse(
        current_month=first_day_this_month.strftime("%Y-%m"),
        total_spent_this_month=round(this_month_total, 2),
        total_spent_last_month=round(last_month_total, 2),
        receipt_count=receipt_count,
        average_ticket=average_ticket(this_month_total, receipt_count),
        category_breakdown=await _category_breakdown(db, this_month_where, category_id),
        shop_breakdown=await _shop_breakdown(db, this_month_where),
    )


@router.get("/report", response_model=AnalyticsReportResponse)
async def get_analytics_report(
        start_date: date = Query(..., description="Start date for the report (YYYY-MM-DD)"),
        end_date: date = Query(..., description="End date for the report (YYYY-MM-DD)"),
        current_user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db),
        shop_name: Optional[str] = Query(
            None,
            description="Filter by shop brand (case-insensitive). Use Unknown for null shop.",
        ),
        category_id: Optional[int] = Query(
            None,
            gt=0,
            description="Only receipts that contain an item in this category.",
        ),
):
    """
    GET /api/v1/statistics/report
    Generates a custom date-range analytics report with timeline chart data.
    """
    if start_date > end_date:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Start date cannot be after end date."
        )

    user_id = current_user.id
    range_where = receipts_in_range_where(
        user_id,
        start_date=start_date,
        end_date=end_date,
        shop_name=shop_name,
        category_id=category_id,
    )

    total_spent = float(
        (await db.execute(select(func.sum(Receipt.total_amount)).where(range_where))).scalar()
        or 0.0
    )
    receipt_count = int(
        (await db.execute(select(func.count(Receipt.id)).where(range_where))).scalar()
        or 0
    )

    stmt_timeline = (
        select(
            Receipt.purchase_date,
            func.sum(Receipt.total_amount).label("daily_total"),
        )
        .where(range_where)
        .group_by(Receipt.purchase_date)
        .order_by(Receipt.purchase_date.asc())
    )
    result_timeline = await db.execute(stmt_timeline)
    timeline = [
        TimelineDataPoint(
            date=row.purchase_date,
            amount=round(float(row.daily_total or 0.0), 2),
        )
        for row in result_timeline
    ]

    return AnalyticsReportResponse(
        start_date=start_date,
        end_date=end_date,
        total_spent=round(total_spent, 2),
        receipt_count=receipt_count,
        average_ticket=average_ticket(total_spent, receipt_count),
        category_breakdown=await _category_breakdown(db, range_where, category_id),
        shop_breakdown=await _shop_breakdown(db, range_where),
        timeline=timeline,
    )
