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

ExtractMode = Literal["image", "pdf"]

_PIPELINE_FAILURE_STATUSES = frozenset({"FAILED_PARSING", "FAILED_SCHEMA"})
_active_extractions: Set[int] = set()


def is_pipeline_failure(extracted_data: dict[str, Any]) -> bool:
    if "error" in extracted_data:
        return True
    return extracted_data.get("status") in _PIPELINE_FAILURE_STATUSES


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
) -> None:
    receipt.status = "FAILED"
    receipt.extraction_error = error[:512]
    await db.commit()
    logger.warning(f"Receipt {receipt.id} extraction failed: {error}")


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
        logger.info(f"Re-queuing interrupted extraction for receipt {receipt_id}")
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
    Skips if receipt is no longer PROCESSING or job already active for this id.
    """
    if receipt_id in _active_extractions:
        logger.debug(f"Extraction already active for receipt {receipt_id}, skipping duplicate")
        return

    _active_extractions.add(receipt_id)
    label = "AI" if mode == "image" else "PDF"
    last_error = "Unknown extraction error"

    try:
        async with AsyncSessionLocal() as db:
            receipt = await db.get(Receipt, receipt_id)
            if receipt is None:
                logger.error(f"{label} extraction: receipt {receipt_id} not found")
                return
            if receipt.status != "PROCESSING":
                logger.info(f"Receipt {receipt_id} is {receipt.status}, skipping extraction")
                return

        max_retries = settings.EXTRACTION_MAX_RETRIES
        delay = settings.EXTRACTION_RETRY_DELAY_SECONDS

        for attempt in range(1, max_retries + 1):
            async with AsyncSessionLocal() as db:
                receipt = await db.get(Receipt, receipt_id)
                if receipt is None or receipt.status != "PROCESSING":
                    return
                if is_stale_processing(receipt):
                    await mark_extraction_failed(
                        db,
                        receipt,
                        f"Extraction timed out after {settings.EXTRACTION_STALE_MINUTES} minutes",
                    )
                    return

                receipt.extraction_started_at = datetime.utcnow()
                receipt.extraction_attempts = attempt
                receipt.extraction_error = None
                await db.commit()

            try:
                logger.info(
                    f"Starting {label} pipeline for receipt {receipt_id} "
                    f"(attempt {attempt}/{max_retries})"
                )
                extracted_data = await _run_pipeline_once(file_url, mode)

                if is_pipeline_failure(extracted_data):
                    detail = extracted_data.get("error") or extracted_data.get("status", "Pipeline failed")
                    raise RuntimeError(str(detail))

                async with AsyncSessionLocal() as db:
                    receipt = await db.get(Receipt, receipt_id)
                    if receipt is None or receipt.status != "PROCESSING":
                        return

                    await populate_receipt_from_extraction(db, receipt, extracted_data)
                    await finalize_receipt_warranty_state(db, receipt)
                    await db.commit()

                    logger.info(
                        f"{label} pipeline completed for receipt {receipt_id} → {receipt.status}"
                    )
                return

            except Exception as exc:
                last_error = str(exc)
                logger.warning(
                    f"{label} pipeline attempt {attempt}/{max_retries} failed "
                    f"for receipt {receipt_id}: {last_error}"
                )
                if attempt < max_retries:
                    await asyncio.sleep(delay)

        async with AsyncSessionLocal() as db:
            receipt = await db.get(Receipt, receipt_id)
            if receipt is not None and receipt.status == "PROCESSING":
                await mark_extraction_failed(
                    db,
                    receipt,
                    f"Extraction failed after {max_retries} attempts: {last_error}",
                )

    finally:
        _active_extractions.discard(receipt_id)
