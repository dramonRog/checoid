"""Shared analytics helpers for statistics endpoints."""
from __future__ import annotations

from datetime import date
from typing import Optional, Sequence

from sqlalchemy import and_
from sqlalchemy.sql import ColumnElement

from src.backend.db.models import Receipt

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
) -> list[ColumnElement]:
    """
    Common WHERE clauses for analytics receipt queries.
    - start_date / end_date: inclusive range on purchase_date
    - end_exclusive: purchase_date < end_exclusive (e.g. start of this month for last-month window)
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
    return clauses


def receipts_in_range_where(
    user_id: int,
    *,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    end_exclusive: Optional[date] = None,
    statuses: Optional[Sequence[str]] = None,
) -> ColumnElement:
    """AND-combined filter for Receipt rows in an analytics window."""
    return and_(
        *analytics_receipt_filters(
            user_id,
            start_date=start_date,
            end_date=end_date,
            end_exclusive=end_exclusive,
            statuses=statuses,
        )
    )
