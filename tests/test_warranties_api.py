"""API tests: warranty sejf endpoints."""
from datetime import date, timedelta

import pytest
from httpx import AsyncClient

from factories import create_receipt_with_warranty_item



@pytest.mark.asyncio
async def test_warranty_vault_active(
    client: AsyncClient,
    auth_headers: dict,
    db_session,
):
    me = await client.get("/api/v1/users/me", headers=auth_headers)
    user_id = me.json()["id"]
    await create_receipt_with_warranty_item(
        db_session,
        user_id,
        under_warranty=True,
        purchase=date.today() - timedelta(days=30),
        warranty_end=date.today() + timedelta(days=700),
    )

    response = await client.get(
        "/api/v1/warranties/vault",
        headers=auth_headers,
        params={"status": "active"},
    )
    assert response.status_code == 200, response.text
    items = response.json()
    assert len(items) >= 1
    row = items[0]
    assert "item_id" in row
    assert "image_url" in row
    assert "shop_name" in row
    assert "price" in row
    assert row["days_remaining"] >= 0


@pytest.mark.asyncio
async def test_warranty_patch_toggle_off_recalculates_flag(
    client: AsyncClient,
    auth_headers: dict,
    db_session,
):
    me = await client.get("/api/v1/users/me", headers=auth_headers)
    user_id = me.json()["id"]
    receipt, item = await create_receipt_with_warranty_item(db_session, user_id)

    patched = await client.patch(
        f"/api/v1/warranties/items/{item.id}",
        headers=auth_headers,
        json={"is_under_warranty": False},
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["is_under_warranty"] is False
    assert patched.json()["warranty_end_date"] is None

    detail = await client.get(f"/api/v1/receipts/{receipt.id}", headers=auth_headers)
    assert detail.status_code == 200
    assert detail.json()["has_warranty_items"] is False


@pytest.mark.asyncio
async def test_warranty_patch_requires_auth(client: AsyncClient):
    response = await client.patch(
        "/api/v1/warranties/items/1",
        json={"is_under_warranty": True},
    )
    assert response.status_code == 401
