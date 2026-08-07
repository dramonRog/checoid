"""API / unit tests for operational health checks."""
from unittest.mock import AsyncMock, patch

import pytest

from src.backend.services.ai_health import collect_ai_component_health, check_ollama


@pytest.mark.asyncio
async def test_collect_ai_health_with_mocks():
    with patch(
        "src.backend.services.ai_health._check_yolo_sync",
        return_value={"status": "ok", "detail": "yolo"},
    ), patch(
        "src.backend.services.ai_health._check_paddle_sync",
        return_value={"status": "ok", "detail": "paddle"},
    ), patch(
        "src.backend.services.ai_health.check_ollama",
        new=AsyncMock(return_value={"status": "ok", "detail": "ollama"}),
    ):
        result = await collect_ai_component_health()

    assert result["status"] == "ok"
    assert result["components"]["yolo"]["status"] == "ok"
    assert result["components"]["paddleocr"]["status"] == "ok"
    assert result["components"]["ollama"]["status"] == "ok"


@pytest.mark.asyncio
async def test_collect_ai_health_degraded_when_ollama_missing_model():
    with patch(
        "src.backend.services.ai_health._check_yolo_sync",
        return_value={"status": "ok", "detail": "yolo"},
    ), patch(
        "src.backend.services.ai_health._check_paddle_sync",
        return_value={"status": "ok", "detail": "paddle"},
    ), patch(
        "src.backend.services.ai_health.check_ollama",
        new=AsyncMock(
            return_value={
                "status": "degraded",
                "detail": "model missing",
            }
        ),
    ):
        result = await collect_ai_component_health()

    assert result["status"] == "degraded"


@pytest.mark.asyncio
async def test_check_ollama_unavailable(monkeypatch):
    monkeypatch.setattr(
        "src.backend.services.ai_health.settings.OLLAMA_BASE_URL",
        "http://127.0.0.1:9",
    )
    monkeypatch.setattr(
        "src.backend.services.ai_health.settings.HEALTH_CHECK_TIMEOUT_SECONDS",
        0.2,
    )
    result = await check_ollama()
    assert result["status"] == "unavailable"
