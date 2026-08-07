"""API tests: auth + user profile contracts used by mobile."""
import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_register_and_login(client: AsyncClient):
    register = await client.post(
        "/api/v1/auth/register",
        json={
            "first_name": "Test",
            "last_name": "User",
            "email": "testuser@example.com",
            "password": "password123",
        },
    )
    assert register.status_code == 201
    body = register.json()
    assert body["email"] == "testuser@example.com"
    assert "password" not in body
    assert "password_hash" not in body

    login = await client.post(
        "/api/v1/auth/login",
        data={"username": "testuser@example.com", "password": "password123"},
    )
    assert login.status_code == 200
    tokens = login.json()
    assert "access_token" in tokens
    assert "refresh_token" in tokens


@pytest.mark.asyncio
async def test_duplicate_register_rejected(client: AsyncClient, registered_user: dict):
    again = await client.post(
        "/api/v1/auth/register",
        json={
            "first_name": "Jan",
            "last_name": "Kowalski",
            "email": registered_user["email"],
            "password": "otherpass",
        },
    )
    assert again.status_code == 400


@pytest.mark.asyncio
async def test_me_requires_auth(client: AsyncClient):
    response = await client.get("/api/v1/users/me")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_get_me(client: AsyncClient, auth_headers: dict, registered_user: dict):
    response = await client.get("/api/v1/users/me", headers=auth_headers)
    assert response.status_code == 200
    assert response.json()["email"] == registered_user["email"]


@pytest.mark.asyncio
async def test_password_update_persists(client: AsyncClient, auth_headers: dict, registered_user: dict):
    update = await client.put(
        "/api/v1/users/me",
        headers=auth_headers,
        json={"password": "newpassword99"},
    )
    assert update.status_code == 200

    old_login = await client.post(
        "/api/v1/auth/login",
        data={"username": registered_user["email"], "password": registered_user["password"]},
    )
    assert old_login.status_code == 401

    new_login = await client.post(
        "/api/v1/auth/login",
        data={"username": registered_user["email"], "password": "newpassword99"},
    )
    assert new_login.status_code == 200


@pytest.mark.asyncio
async def test_email_conflict_returns_409(
    client: AsyncClient,
    auth_headers: dict,
    second_user_headers: dict,
):
    conflict = await client.put(
        "/api/v1/users/me",
        headers=auth_headers,
        json={"email": "anna@example.com"},
    )
    assert conflict.status_code == 409
