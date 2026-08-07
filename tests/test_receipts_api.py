"""API tests: receipts contracts for mobile archive / extract / filters."""
from datetime import date
from io import BytesIO
from unittest.mock import patch

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from src.backend.db.models import User
from factories import create_receipt_with_warranty_item



@pytest.mark.asyncio
async def test_manual_receipt_create_and_get(client: AsyncClient, auth_headers: dict):
    payload = {
        "purchase_date": "2026-01-10",
        "total_amount": 25.50,
        "shop_name": "Biedronka",
        "store_address": "ul. Testowa 1",
        "items": [
            {
                "name": "Chleb",
                "quantity": 1,
                "price": 5.50,
                "is_under_warranty": False,
                "category": "Produkt spożywczy",
            },
            {
                "name": "Mysz",
                "quantity": 1,
                "price": 20.0,
                "is_under_warranty": True,
                "warranty_end_date": "2028-01-10",
                "category": "Elektronika",
            },
        ],
    }
    created = await client.post("/api/v1/receipts/manual", headers=auth_headers, json=payload)
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["shop_name"] == "Biedronka"
    assert body["has_warranty_items"] is True
    assert body["status"] == "MANUALLY_CREATED"
    assert len(body["items"]) == 2
    assert any(i["is_under_warranty"] for i in body["items"])

    receipt_id = body["id"]
    detail = await client.get(f"/api/v1/receipts/{receipt_id}", headers=auth_headers)
    assert detail.status_code == 200
    assert detail.json()["id"] == receipt_id


@pytest.mark.asyncio
async def test_list_receipts_status_and_warranty_filters(
    client: AsyncClient,
    auth_headers: dict,
    db_session,
):
    me = await client.get("/api/v1/users/me", headers=auth_headers)
    user_id = me.json()["id"]

    await create_receipt_with_warranty_item(db_session, user_id, under_warranty=True)
    # Direct insert for a non-warranty receipt
    from src.backend.db.models import Receipt

    bare = Receipt(
        user_id=user_id,
        purchase_date=date(2026, 1, 1),
        total_amount=5.0,
        status="FAILED",
        shop_name="Żabka",
        has_warranty_items=False,
    )
    db_session.add(bare)
    await db_session.commit()

    all_list = await client.get("/api/v1/receipts", headers=auth_headers)
    assert all_list.status_code == 200
    assert all_list.json()["total"] >= 2

    failed = await client.get("/api/v1/receipts", headers=auth_headers, params={"status": "FAILED"})
    assert failed.status_code == 200
    assert failed.json()["total"] >= 1
    assert all(item["status"] == "FAILED" for item in failed.json()["items"])

    warranty = await client.get(
        "/api/v1/receipts",
        headers=auth_headers,
        params={"has_warranty_items": True},
    )
    assert warranty.status_code == 200
    assert warranty.json()["total"] >= 1
    assert all(item["has_warranty_items"] is True for item in warranty.json()["items"])


@pytest.mark.asyncio
async def test_extract_returns_202_and_schedules_job(client: AsyncClient, auth_headers: dict):
    with patch(
        "src.backend.api.routers.receipts.save_upload_file",
        return_value="/media/receipts/fake.jpg",
    ), patch(
        "src.backend.api.routers.receipts.schedule_receipt_extraction"
    ) as schedule:
        files = {"file": ("receipt.jpg", BytesIO(b"fake-image-bytes"), "image/jpeg")}
        response = await client.post(
            "/api/v1/receipts/extract",
            headers=auth_headers,
            files=files,
        )

    assert response.status_code == 202, response.text
    body = response.json()
    assert "receipt_id" in body
    assert body["status"] == "PROCESSING"
    schedule.assert_called_once()
    assert schedule.call_args.args[0] == body["receipt_id"]
    assert schedule.call_args.args[2] == "image"

    detail = await client.get(f"/api/v1/receipts/{body['receipt_id']}", headers=auth_headers)
    assert detail.status_code == 200
    assert detail.json()["status"] == "PROCESSING"


@pytest.mark.asyncio
async def test_extract_rejects_non_image(client: AsyncClient, auth_headers: dict):
    files = {"file": ("notes.txt", BytesIO(b"hello"), "text/plain")}
    response = await client.post(
        "/api/v1/receipts/extract",
        headers=auth_headers,
        files=files,
    )
    assert response.status_code == 415


@pytest.mark.asyncio
async def test_nip_lookup_requires_auth(client: AsyncClient):
    response = await client.get("/api/v1/receipts/lookup/7791011327")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_extraction_status_endpoint(client: AsyncClient, auth_headers: dict):
    response = await client.get("/api/v1/receipts/extraction/status", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert "metrics" in body
    assert "processing" in body
    assert "recent_failures" in body
    assert "active_jobs" in body["metrics"]


@pytest.mark.asyncio
async def test_delete_receipt(client: AsyncClient, auth_headers: dict):
    created = await client.post(
        "/api/v1/receipts/manual",
        headers=auth_headers,
        json={
            "purchase_date": "2026-02-01",
            "total_amount": 3.0,
            "shop_name": "Shop",
            "items": [
                {
                    "name": "Woda",
                    "quantity": 1,
                    "price": 3.0,
                    "is_under_warranty": False,
                    "category": "Napoje",
                }
            ],
        },
    )
    receipt_id = created.json()["id"]

    with patch("src.backend.api.routers.receipts.delete_receipt_image") as delete_img, patch(
        "src.backend.api.routers.receipts.cancel_receipt_extraction"
    ) as cancel:
        deleted = await client.delete(f"/api/v1/receipts/{receipt_id}", headers=auth_headers)
    assert deleted.status_code == 204
    cancel.assert_called_once_with(receipt_id)
    delete_img.assert_called_once()

    missing = await client.get(f"/api/v1/receipts/{receipt_id}", headers=auth_headers)
    assert missing.status_code == 404
