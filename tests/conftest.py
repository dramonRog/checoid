"""
Shared pytest fixtures for FastAPI + async SQLAlchemy tests.

Heavy AI modules (YOLO / PaddleOCR / Ollama pipeline) are stubbed before
backend imports so the suite stays fast and CPU/GPU-agnostic.
"""
from __future__ import annotations

import os
import sys
from typing import AsyncGenerator
from unittest.mock import MagicMock

# Test env must be set before Settings / backend imports
os.environ.setdefault("PROJECT_NAME", "Checoid Test")
os.environ.setdefault("VERSION", "0.0.0-test")
os.environ.setdefault("DESCRIPTION", "test")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("SECRET_KEY", "test-secret-key-at-least-32-characters-long")
os.environ.setdefault("STORAGE_BACKEND", "local")
os.environ["OCR_DEVICE"] = "cpu"
os.environ["RATE_LIMIT_ENABLED"] = "false"
os.environ.setdefault("OLLAMA_BASE_URL", "http://127.0.0.1:11434")

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

# --- Stub AI pipeline before any backend router import ---
_mock_pipeline = MagicMock()
_mock_pipeline.process_receipt_end_to_end = MagicMock(
    return_value={
        "status": "VERIFIED_COMPLETED",
        "sklep": "Test Shop",
        "nip": "7791011327",
        "data": "2026-01-15",
        "suma_calkowita": 10.0,
        "pozycje": [],
    }
)
_mock_pipeline.process_pdf_receipt = MagicMock(
    return_value={
        "status": "VERIFIED_COMPLETED",
        "sklep": "Test Shop",
        "data": "2026-01-15",
        "suma_calkowita": 10.0,
        "pozycje": [],
    }
)
sys.modules["src.ai_pipeline.pipeline"] = _mock_pipeline

_mock_parser = MagicMock()
_mock_parser.categorize_product_names = MagicMock(return_value={})
_mock_parser.parse_with_llm = MagicMock(return_value={"status": "VERIFIED_COMPLETED"})
_mock_parser.validate_and_clean_payload = MagicMock(side_effect=lambda d, *_: d)
sys.modules["src.ai_pipeline.parser"] = _mock_parser

_ai_pkg = MagicMock()
_ai_pkg.process_receipt_end_to_end = _mock_pipeline.process_receipt_end_to_end
_ai_pkg.pipeline = _mock_pipeline
_ai_pkg.parser = _mock_parser
sys.modules["src.ai_pipeline"] = _ai_pkg

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError

from src.backend.api.routers import auth, receipts, users, warranties, categories, statistics
from src.backend.core.exceptions import validation_exception_handler, global_exception_handler
from src.backend.core.rate_limit import limiter
from src.backend.db.base import Base
from src.backend.db.database import get_db
from src.backend.services.categories import seed_categories


@pytest_asyncio.fixture
async def engine():
    # StaticPool keeps one shared in-memory SQLite DB across connections/sessions
    eng = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        echo=False,
    )
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest_asyncio.fixture
async def db_session(engine) -> AsyncGenerator[AsyncSession, None]:
    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with session_factory() as session:
        yield session


@pytest_asyncio.fixture
async def app(engine) -> FastAPI:
    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    async with session_factory() as session:
        await seed_categories(session)

    application = FastAPI()
    application.state.limiter = limiter
    application.add_exception_handler(RequestValidationError, validation_exception_handler)
    application.add_exception_handler(Exception, global_exception_handler)

    async def _override_get_db() -> AsyncGenerator[AsyncSession, None]:
        async with session_factory() as session:
            try:
                yield session
            finally:
                await session.close()

    application.dependency_overrides[get_db] = _override_get_db
    application.include_router(auth.router, prefix="/api/v1")
    application.include_router(receipts.router, prefix="/api/v1")
    application.include_router(users.router, prefix="/api/v1")
    application.include_router(warranties.router, prefix="/api/v1")
    application.include_router(categories.router, prefix="/api/v1")
    application.include_router(statistics.router, prefix="/api/v1")
    return application


@pytest_asyncio.fixture
async def client(app: FastAPI) -> AsyncGenerator[AsyncClient, None]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest_asyncio.fixture
async def registered_user(client: AsyncClient) -> dict:
    payload = {
        "first_name": "Jan",
        "last_name": "Kowalski",
        "email": "jan@example.com",
        "password": "secret123",
    }
    response = await client.post("/api/v1/auth/register", json=payload)
    assert response.status_code == 201, response.text
    return payload


@pytest_asyncio.fixture
async def auth_headers(client: AsyncClient, registered_user: dict) -> dict:
    response = await client.post(
        "/api/v1/auth/login",
        data={
            "username": registered_user["email"],
            "password": registered_user["password"],
        },
    )
    assert response.status_code == 200, response.text
    token = response.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest_asyncio.fixture
async def second_user_headers(client: AsyncClient) -> dict:
    await client.post(
        "/api/v1/auth/register",
        json={
            "first_name": "Anna",
            "last_name": "Nowak",
            "email": "anna@example.com",
            "password": "secret456",
        },
    )
    response = await client.post(
        "/api/v1/auth/login",
        data={"username": "anna@example.com", "password": "secret456"},
    )
    token = response.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}
