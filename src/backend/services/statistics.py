"""Shared analytics helpers for statistics endpoints."""
from __future__ import annotations

from datetime import date
from typing import Optional, Sequence

from sqlalchemy import and_, exists, func, or_, select
from sqlalchemy.sql import ColumnElement

from src.backend.db.models import Receipt, ReceiptItem

# Align with AI pipeline statuses in src/ai_pipeline/parser.py (VERIFIED_COMPLETED).
# Keep COMPLETED as a legacy alias. Exclude NEEDS_HUMAN_REVIEW / FAILED / PROCESSING.
ANALYTICS_STATUSES: list[str] = [
    "VERIFIED_COMPLETED",
    "COMPLETED",
    "MANUALLY_CREATED",
    "MANUALLY_CORRECTED",
]


def month_window(today: Optional[date] = None) -> tuple[date, date, date]:
    """
    Return (first_day_this_month, first_day_last_month, today).
    last month ends the day before first_day_this_month.
    """
    today = today or date.today()
    first_day_this_month = today.replace(day=1)
    if today.month == 1:
        first_day_last_month = today.replace(year=today.year - 1, month=12, day=1)
    else:
        first_day_last_month = today.replace(month=today.month - 1, day=1)
    return first_day_this_month, first_day_last_month, today


def analytics_receipt_filters(
    user_id: int,
    *,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    end_exclusive: Optional[date] = None,
    statuses: Optional[Sequence[str]] = None,
    shop_name: Optional[str] = None,
    category_id: Optional[int] = None,
) -> list[ColumnElement]:
    """
    Common WHERE clauses for analytics receipt queries.
    - start_date / end_date: inclusive range on purchase_date
    - end_exclusive: purchase_date < end_exclusive (e.g. start of this month for last-month window)
    - shop_name: case-insensitive; "Unknown" matches null/blank shop_name
    - category_id: receipt must have at least one item with that category
    """
    status_list = list(statuses) if statuses is not None else ANALYTICS_STATUSES
    clauses: list[ColumnElement] = [
        Receipt.user_id == user_id,
        Receipt.status.in_(status_list),
        Receipt.purchase_date.is_not(None),
    ]
    if start_date is not None:
        clauses.append(Receipt.purchase_date >= start_date)
    if end_date is not None:
        clauses.append(Receipt.purchase_date <= end_date)
    if end_exclusive is not None:
        clauses.append(Receipt.purchase_date < end_exclusive)

    if shop_name is not None:
        hint = shop_name.strip()
        if hint:
            if hint.lower() == "unknown":
                clauses.append(
                    or_(
                        Receipt.shop_name.is_(None),
                        func.trim(Receipt.shop_name) == "",
                    )
                )
            else:
                clauses.append(func.lower(Receipt.shop_name) == hint.lower())

    if category_id is not None:
        clauses.append(
            exists(
                select(ReceiptItem.id).where(
                    ReceiptItem.receipt_id == Receipt.id,
                    ReceiptItem.category_id == category_id,
                )
            )
        )

    return clauses


def receipts_in_range_where(
    user_id: int,
    *,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    end_exclusive: Optional[date] = None,
    statuses: Optional[Sequence[str]] = None,
    shop_name: Optional[str] = None,
    category_id: Optional[int] = None,
) -> ColumnElement:
    """AND-combined filter for Receipt rows in an analytics window."""
    return and_(
        *analytics_receipt_filters(
            user_id,
            start_date=start_date,
            end_date=end_date,
            end_exclusive=end_exclusive,
            statuses=statuses,
            shop_name=shop_name,
            category_id=category_id,
        )
    )


def average_ticket(total: float, count: int) -> float:
    """total / count rounded to 2 decimals; 0 if empty."""
    if count <= 0:
        return 0.0
    return round(float(total) / count, 2)
