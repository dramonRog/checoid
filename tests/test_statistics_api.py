"""Light statistics API smoke tests."""
from datetime import date

import pytest
from httpx import AsyncClient

from src.backend.db.models import Receipt


@pytest.mark.asyncio
async def test_statistics_summary_empty(client: AsyncClient, auth_headers: dict):
    response = await client.get("/api/v1/statistics/summary", headers=auth_headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert "total_spent_this_month" in body
    assert "receipt_count" in body
    assert "average_ticket" in body


@pytest.mark.asyncio
async def test_statistics_summary_counts_manual_receipt(
    client: AsyncClient,
    auth_headers: dict,
    db_session,
):
    me = await client.get("/api/v1/users/me", headers=auth_headers)
    user_id = me.json()["id"]
    today = date.today()
    db_session.add(
        Receipt(
            user_id=user_id,
            purchase_date=today,
            total_amount=40.0,
            status="MANUALLY_CREATED",
            shop_name="Lidl",
            has_warranty_items=False,
        )
    )
    await db_session.commit()

    response = await client.get("/api/v1/statistics/summary", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["receipt_count"] >= 1
    assert body["total_spent_this_month"] >= 40.0
