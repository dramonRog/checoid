"""Background receipt extraction jobs (async upload → poll pattern)."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import Any, Literal, Optional, Set

from fastapi.concurrency import run_in_threadpool
from loguru import logger
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.ai_pipeline import process_receipt_end_to_end
from src.ai_pipeline.pipeline import process_pdf_receipt
from src.backend.core.config import settings
from src.backend.core.storage import local_pipeline_path
from src.backend.db.database import AsyncSessionLocal
from src.backend.db.models import Receipt, ReceiptItem
from src.backend.services.categories import get_or_create_category_id
from src.backend.services.company_resolution import resolve_company_and_shop
from src.backend.services.warranty import apply_warranty, finalize_receipt_warranty_state
from src.backend.services import extraction_observability as obs

ExtractMode = Literal["image", "pdf"]

_PIPELINE_FAILURE_STATUSES = frozenset({"FAILED_PARSING", "FAILED_SCHEMA"})
_active_extractions: Set[int] = set()
_cancelled_extractions: Set[int] = set()


def get_active_extraction_ids() -> list[int]:
    return sorted(_active_extractions)


def get_extraction_metrics() -> dict[str, Any]:
    return obs.get_extraction_metrics_snapshot(active_jobs=len(_active_extractions))


def is_pipeline_failure(extracted_data: dict[str, Any]) -> bool:
    """True when pipeline output should not be saved as a successful receipt."""
    if not isinstance(extracted_data, dict):
        return True

    status = str(extracted_data.get("status") or "")
    if status.startswith("FAILED") or status in _PIPELINE_FAILURE_STATUSES:
        return True

    # Bare error payloads (YOLO/OCR/file missing) without a success status
    if extracted_data.get("error") and status not in {
        "VERIFIED_COMPLETED",
        "COMPLETED",
        "NEEDS_HUMAN_REVIEW",
    }:
        return True

    return False


def cancel_receipt_extraction(receipt_id: int) -> None:
    """Signal in-flight job to stop after current attempt (e.g. receipt deleted)."""
    _cancelled_extractions.add(receipt_id)


def is_extraction_cancelled(receipt_id: int) -> bool:
    return receipt_id in _cancelled_extractions


def clear_extraction_cancel(receipt_id: int) -> None:
    _cancelled_extractions.discard(receipt_id)

def infer_extract_mode(image_url: str) -> ExtractMode:
    path = image_url.split("?", 1)[0].lower()
    return "pdf" if path.endswith(".pdf") else "image"


def _processing_anchor(receipt: Receipt) -> datetime:
    return receipt.extraction_started_at or receipt.created_at


def is_stale_processing(receipt: Receipt, now: Optional[datetime] = None) -> bool:
    if receipt.status != "PROCESSING":
        return False
    now = now or datetime.utcnow()
    deadline = _processing_anchor(receipt) + timedelta(minutes=settings.EXTRACTION_STALE_MINUTES)
    return now >= deadline


async def mark_extraction_failed(
    db: AsyncSession,
    receipt: Receipt,
    error: str,
    *,
    mode: Optional[ExtractMode] = None,
) -> None:
    receipt.status = "FAILED"
    receipt.extraction_error = error[:512]
    await db.commit()
    inferred = mode or (infer_extract_mode(receipt.image_url) if receipt.image_url else "image")
    obs.record_job_failed(receipt.id, inferred, error)
    if "timed out" in error.lower():
        obs.record_stale_marked(receipt.id, settings.EXTRACTION_STALE_MINUTES)


async def reconcile_stale_processing_receipt(
    db: AsyncSession,
    receipt: Receipt,
) -> bool:
    """If PROCESSING past stale threshold, mark FAILED. Returns True if reconciled."""
    if not is_stale_processing(receipt):
        return False
    minutes = settings.EXTRACTION_STALE_MINUTES
    await mark_extraction_failed(
        db,
        receipt,
        f"Extraction timed out after {minutes} minutes",
    )
    return True


async def cleanup_stale_processing_receipts() -> int:
    """Mark all stale PROCESSING receipts as FAILED. Returns count updated."""
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(Receipt).where(Receipt.status == "PROCESSING"))
        receipts = list(result.scalars().all())
        count = 0
        for receipt in receipts:
            if await reconcile_stale_processing_receipt(db, receipt):
                count += 1
        return count


async def recover_interrupted_extractions() -> None:
    """
    On server startup: fail stale PROCESSING receipts; re-queue the rest.
    Handles jobs lost to restart (receipt still PROCESSING, file still on disk).
    """
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(Receipt).where(Receipt.status == "PROCESSING"))
        receipts = list(result.scalars().all())

    if not receipts:
        return

    to_requeue: list[tuple[int, str, ExtractMode]] = []
    stale_count = 0

    async with AsyncSessionLocal() as db:
        for receipt in receipts:
            if await reconcile_stale_processing_receipt(db, receipt):
                stale_count += 1
                continue
            if not receipt.image_url:
                await mark_extraction_failed(db, receipt, "Missing image_url for extraction recovery")
                continue
            to_requeue.append((receipt.id, receipt.image_url, infer_extract_mode(receipt.image_url)))

    if stale_count:
        logger.info(f"Marked {stale_count} stale PROCESSING receipt(s) as FAILED on startup")

    for receipt_id, file_url, mode in to_requeue:
        obs.record_queued(receipt_id, mode, recovered=True)
        asyncio.create_task(process_receipt_extraction(receipt_id, file_url, mode))

    if to_requeue:
        logger.info(f"Re-queued {len(to_requeue)} interrupted extraction job(s)")


async def extraction_watchdog_loop() -> None:
    """Periodic stale PROCESSING cleanup while the server is running."""
    interval = settings.EXTRACTION_WATCHDOG_INTERVAL_SECONDS
    while True:
        await asyncio.sleep(interval)
        try:
            count = await cleanup_stale_processing_receipts()
            if count:
                logger.info(f"Watchdog marked {count} stale PROCESSING receipt(s) as FAILED")
        except Exception as exc:
            logger.error(f"Extraction watchdog error: {exc}")


def schedule_receipt_extraction(receipt_id: int, file_url: str, mode: ExtractMode) -> None:
    obs.record_queued(receipt_id, mode, recovered=False)
    asyncio.create_task(process_receipt_extraction(receipt_id, file_url, mode))


async def populate_receipt_from_extraction(
    db: AsyncSession,
    receipt: Receipt,
    extracted_data: dict[str, Any],
) -> None:
    await db.execute(delete(ReceiptItem).where(ReceiptItem.receipt_id == receipt.id))

    receipt.status = extracted_data.get("status", "COMPLETED")
    receipt.extraction_error = None

    if extracted_data.get("suma_calkowita") is not None:
        receipt.total_amount = float(extracted_data["suma_calkowita"])

    date_str = extracted_data.get("data")
    if date_str:
        try:
            receipt.purchase_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            logger.warning(f"Could not parse date format: {date_str}")

    nip = extracted_data.get("nip")
    sklep_name = extracted_data.get("sklep")
    resolution = await resolve_company_and_shop(db, nip, sklep_name)
    receipt.company_id = resolution.company_id
    receipt.shop_name = resolution.shop_name

    adres = extracted_data.get("adres")
    if isinstance(adres, str) and adres.strip():
        receipt.store_address = adres.strip()[:255]
    if resolution.needs_review:
        receipt.status = "NEEDS_HUMAN_REVIEW"

    pozycje = extracted_data.get("pozycje") or []
    for p in pozycje:
        # cena / price = paid line total after discount (not unit price).
        raw_cena = p.get("cena")
        safe_cena = float(raw_cena) if raw_cena is not None else 0.0

        raw_ilosc = p.get("ilosc")
        safe_ilosc = float(raw_ilosc) if raw_ilosc is not None else 1.0

        category_id = await get_or_create_category_id(db, p.get("kategoria"))
        under_w, end_w = apply_warranty(
            bool(p.get("gwarancja")),
            receipt.purchase_date,
        )

        db.add(
            ReceiptItem(
                receipt_id=receipt.id,
                name=p.get("nazwa") or "Unknown Item",
                quantity=safe_ilosc,
                price=safe_cena,
                category_id=category_id,
                is_under_warranty=under_w,
                warranty_end_date=end_w,
            )
        )


async def _run_pipeline_once(file_url: str, mode: ExtractMode) -> dict[str, Any]:
    pipeline_fn = process_receipt_end_to_end if mode == "image" else process_pdf_receipt
    with local_pipeline_path(file_url) as local_path:
        return await run_in_threadpool(pipeline_fn, local_path, False)


async def process_receipt_extraction(
    receipt_id: int,
    file_url: str,
    mode: ExtractMode,
) -> None:
    """
    Run AI pipeline in the background with retries and dead-letter error storage.
    Skips if receipt is no longer PROCESSING, deleted, cancelled, or already active.
    """
    if receipt_id in _active_extractions:
        logger.debug(f"Extraction already active for receipt {receipt_id}, skipping duplicate")
        return

    clear_extraction_cancel(receipt_id)
    _active_extractions.add(receipt_id)
    label = "AI" if mode == "image" else "PDF"
    last_error = "Unknown extraction error"
    job_started = datetime.utcnow()

    try:
        async with AsyncSessionLocal() as db:
            receipt = await db.get(Receipt, receipt_id)
            if receipt is None:
                obs.record_cancelled(receipt_id, mode, "receipt_not_found")
                return
            if receipt.status != "PROCESSING":
                obs.record_cancelled(receipt_id, mode, f"status_{receipt.status}")
                return

        max_retries = settings.EXTRACTION_MAX_RETRIES
        delay = settings.EXTRACTION_RETRY_DELAY_SECONDS

        for attempt in range(1, max_retries + 1):
            if is_extraction_cancelled(receipt_id):
                obs.record_cancelled(receipt_id, mode, "cancelled_flag")
                return

            async with AsyncSessionLocal() as db:
                receipt = await db.get(Receipt, receipt_id)
                if receipt is None:
                    obs.record_cancelled(receipt_id, mode, "deleted_mid_job")
                    return
                if receipt.status != "PROCESSING":
                    obs.record_cancelled(receipt_id, mode, f"status_{receipt.status}")
                    return
                if is_stale_processing(receipt):
                    await mark_extraction_failed(
                        db,
                        receipt,
                        f"Extraction timed out after {settings.EXTRACTION_STALE_MINUTES} minutes",
                        mode=mode,
                    )
                    return

                receipt.extraction_started_at = datetime.utcnow()
                receipt.extraction_attempts = attempt
                receipt.extraction_error = None
                await db.commit()

            attempt_started = obs.record_attempt_start(
                receipt_id, mode, attempt, max_retries
            )
            try:
                extracted_data = await _run_pipeline_once(file_url, mode)

                if is_extraction_cancelled(receipt_id):
                    obs.record_cancelled(receipt_id, mode, "cancelled_after_pipeline")
                    return

                if is_pipeline_failure(extracted_data):
                    detail = (
                        extracted_data.get("error")
                        or extracted_data.get("status")
                        or "Pipeline failed"
                    )
                    raise RuntimeError(str(detail))

                async with AsyncSessionLocal() as db:
                    receipt = await db.get(Receipt, receipt_id)
                    if receipt is None:
                        obs.record_cancelled(receipt_id, mode, "deleted_before_save")
                        return
                    if receipt.status != "PROCESSING" or is_extraction_cancelled(receipt_id):
                        obs.record_cancelled(receipt_id, mode, "aborted_before_save")
                        return

                    await populate_receipt_from_extraction(db, receipt, extracted_data)
                    await finalize_receipt_warranty_state(db, receipt)
                    await db.commit()

                    obs.record_attempt_success(
                        receipt_id,
                        mode,
                        attempt,
                        attempt_started,
                        receipt.status,
                    )
                    total_ms = (datetime.utcnow() - job_started).total_seconds() * 1000
                    logger.info(
                        f"{label} pipeline completed for receipt {receipt_id} "
                        f"→ {receipt.status} (job_ms={total_ms:.0f})"
                    )
                return

            except Exception as exc:
                last_error = str(exc)
                obs.record_attempt_failure(
                    receipt_id,
                    mode,
                    attempt,
                    max_retries,
                    attempt_started,
                    last_error,
                )
                if is_extraction_cancelled(receipt_id):
                    obs.record_cancelled(receipt_id, mode, "cancelled_after_failure")
                    return
                if attempt < max_retries:
                    await asyncio.sleep(delay)

        if is_extraction_cancelled(receipt_id):
            obs.record_cancelled(receipt_id, mode, "cancelled_before_final_fail")
            return

        async with AsyncSessionLocal() as db:
            receipt = await db.get(Receipt, receipt_id)
            if receipt is not None and receipt.status == "PROCESSING":
                await mark_extraction_failed(
                    db,
                    receipt,
                    f"Extraction failed after {max_retries} attempts: {last_error}",
                    mode=mode,
                )

    finally:
        _active_extractions.discard(receipt_id)
        clear_extraction_cancel(receipt_id)
