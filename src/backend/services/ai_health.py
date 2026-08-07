"""Readiness checks for YOLO, PaddleOCR, and Ollama."""
from __future__ import annotations

from typing import Any

import httpx
from fastapi.concurrency import run_in_threadpool

from src.backend.core.config import settings


def _check_yolo_sync() -> dict[str, Any]:
    try:
        from src.ai_pipeline import pipeline as pl

        model = getattr(pl, "model_yolo", None)
        if model is None:
            return {
                "status": "unavailable",
                "detail": "YOLO model not loaded (missing weights or init failed)",
            }
        return {"status": "ok", "detail": "YOLO model loaded"}
    except Exception as exc:
        return {"status": "unavailable", "detail": str(exc)[:200]}


def _check_paddle_sync() -> dict[str, Any]:
    try:
        from src.ai_pipeline import pipeline as pl

        reader = getattr(pl, "reader_ocr", None)
        if reader is None:
            return {
                "status": "unavailable",
                "detail": "PaddleOCR reader not initialized",
            }
        return {
            "status": "ok",
            "detail": f"PaddleOCR ready (OCR_DEVICE={settings.OCR_DEVICE})",
        }
    except Exception as exc:
        return {"status": "unavailable", "detail": str(exc)[:200]}


async def check_ollama() -> dict[str, Any]:
    base = settings.OLLAMA_BASE_URL.rstrip("/")
    model = settings.OLLAMA_MODEL
    try:
        async with httpx.AsyncClient(timeout=settings.HEALTH_CHECK_TIMEOUT_SECONDS) as client:
            response = await client.get(f"{base}/api/tags")
        if response.status_code != 200:
            return {
                "status": "unavailable",
                "detail": f"Ollama HTTP {response.status_code} at {base}",
            }
        names = [m.get("name", "") for m in response.json().get("models", [])]
        if any(model in name for name in names):
            return {"status": "ok", "detail": f"model {model} available", "base_url": base}
        return {
            "status": "degraded",
            "detail": f"Ollama up but model '{model}' not found; run: ollama pull {model}",
            "base_url": base,
            "models": names[:8],
        }
    except Exception as exc:
        return {
            "status": "unavailable",
            "detail": f"Cannot reach Ollama at {base}: {str(exc)[:160]}",
            "base_url": base,
        }


async def collect_ai_component_health() -> dict[str, Any]:
    yolo = await run_in_threadpool(_check_yolo_sync)
    paddle = await run_in_threadpool(_check_paddle_sync)
    ollama = await check_ollama()
    components = {"yolo": yolo, "paddleocr": paddle, "ollama": ollama}

    statuses = {c["status"] for c in components.values()}
    if statuses == {"ok"}:
        overall = "ok"
    elif "ok" in statuses or "degraded" in statuses:
        overall = "degraded"
    else:
        overall = "unavailable"

    return {"status": overall, "components": components}
