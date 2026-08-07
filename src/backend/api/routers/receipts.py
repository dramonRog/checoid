from fastapi import APIRouter, Depends, UploadFile, File, HTTPException, status, Query
from fastapi.concurrency import run_in_threadpool
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, delete
from sqlalchemy.orm import selectinload
from sqlalchemy.exc import IntegrityError
from datetime import datetime
from typing import Optional

from src.backend.db.database import get_db
from src.backend.db.models import User, Receipt, ReceiptItem
from src.backend.api.deps import get_current_user
from src.backend.api.services.nip_lookup import fetch_company_by_nip
from src.backend.services.categories import get_or_create_category_id
from src.backend.services.company_resolution import (
    resolve_company_and_shop,
    get_or_create_company_by_nip,
)
from src.backend.services.warranty import apply_warranty, finalize_receipt_warranty_state
from src.backend.services.brands import clean_nip, ensure_brand_in_catalog
from src.backend.services.receipt_extraction import (
    schedule_receipt_extraction,
    reconcile_stale_processing_receipt,
    cancel_receipt_extraction,
    get_extraction_metrics,
    get_active_extraction_ids,
    is_stale_processing,
)
from src.backend.core.storage import save_upload_file, delete_receipt_image, sync_receipt_image_storage
from src.backend.schemas import (
    ReceiptResponse,
    ReceiptUpdate,
    ReceiptListResponse,
    ReceiptCreate,
    ReceiptExtractAcceptedResponse,
    ExtractionStatusResponse,
    ExtractionJobItem,
    ExtractionMetricsResponse,
)
from src.backend.core.config import settings

from src.ai_pipeline.parser import categorize_product_names

router = APIRouter(prefix="/receipts", tags=["Receipts"])

MAX_UPLOAD_BYTES = 10 * 1024 * 1024


async def _validate_upload_size(file: UploadFile) -> None:
    file.file.seek(0, 2)
    file_size = file.file.tell()
    file.file.seek(0)
    if file_size > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="File is too large. Maximum allowed size is 10MB.",
        )


@router.post("/extract-pdf", response_model=ReceiptExtractAcceptedResponse, status_code=status.HTTP_202_ACCEPTED)
async def extract_pdf_receipt_data(
        file: UploadFile = File(...),
        current_user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db),
):
    """
    POST /api/v1/receipts/extract-pdf
    Accepts PDF, returns immediately with receipt_id (status PROCESSING).
    Poll GET /receipts/{receipt_id} until status is not PROCESSING.
    """
    user_id = current_user.id

    if file.content_type != "application/pdf":
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Only PDF files are allowed for this endpoint.",
        )

    await _validate_upload_size(file)

    file_url = await save_upload_file(file)

    new_receipt = Receipt(
        user_id=user_id,
        image_url=file_url,
        status="PROCESSING",
    )

    db.add(new_receipt)
    await db.commit()
    await db.refresh(new_receipt)

    schedule_receipt_extraction(new_receipt.id, file_url, "pdf")

    return ReceiptExtractAcceptedResponse(receipt_id=new_receipt.id)


@router.post("/manual", response_model=ReceiptResponse, status_code=status.HTTP_201_CREATED)
async def create_manual_receipt(
        payload: ReceiptCreate,
        current_user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db)
):
    """
    POST /api/v1/receipts/manual
    Create a receipt manually from JSON data.
    Uses the same company/shop resolution as AI extract, and LLM-categorizes
    items that do not already provide category_id / category.
    LLM also decides warranty unless the client sets is_under_warranty explicitly.
    """
    user_id = current_user.id
    receipt_status = payload.status if payload.status != "PROCESSING" else "MANUALLY_CREATED"

    # Company / shop resolution (same 4 cases as AI pipeline)
    resolution = await resolve_company_and_shop(db, payload.nip, payload.shop_name)
    company_id = payload.company_id if payload.company_id is not None else resolution.company_id
    # Prefer client shop_name as-is (any free text); fall back to resolver suggestion
    shop_name = payload.shop_name if payload.shop_name is not None else resolution.shop_name
    if isinstance(shop_name, str):
        shop_name = shop_name.strip() or None
    if resolution.needs_review and payload.company_id is None:
        # Incomplete identity (e.g. neither NIP nor shop) → ask for review
        if resolution.case == "neither":
            receipt_status = "NEEDS_HUMAN_REVIEW"

    new_receipt = Receipt(
        user_id=user_id,
        purchase_date=payload.purchase_date,
        total_amount=payload.total_amount,
        status=receipt_status,
        image_url=payload.image_url,
        shop_name=shop_name,
        store_address=(
            payload.store_address.strip()[:255]
            if payload.store_address and payload.store_address.strip()
            else None
        ),
        company_id=company_id,
    )

    db.add(new_receipt)

    try:
        await db.flush()
        receipt_id = new_receipt.id

        if payload.items:
            # LLM for missing category and/or warranty decision
            names_needing_llm = [
                item.name for item in payload.items
                if (
                    (not item.category_id and not (item.category and item.category.strip()))
                    or item.is_under_warranty is None
                )
            ]
            llm_map = {}
            if names_needing_llm:
                llm_map = await run_in_threadpool(categorize_product_names, names_needing_llm)

            for item in payload.items:
                llm_info = llm_map.get(item.name) or {}
                if item.category_id:
                    category_id = item.category_id
                else:
                    label = (
                        (item.category or "").strip()
                        or llm_info.get("kategoria")
                        or "Inne"
                    )
                    category_id = await get_or_create_category_id(db, label)

                if item.warranty_end_date is not None and item.is_under_warranty is None:
                    under_flag = True
                elif item.is_under_warranty is not None:
                    under_flag = item.is_under_warranty
                else:
                    under_flag = bool(llm_info.get("gwarancja"))

                under_w, end_w = apply_warranty(
                    under_flag,
                    payload.purchase_date,
                    item.warranty_end_date,
                )

                db.add(
                    ReceiptItem(
                        receipt_id=receipt_id,
                        name=item.name,
                        quantity=item.quantity,
                        price=item.price,
                        is_under_warranty=under_w,
                        warranty_end_date=end_w,
                        category_id=category_id,
                    )
                )

            await finalize_receipt_warranty_state(db, new_receipt)
        else:
            new_receipt.has_warranty_items = False
            if new_receipt.image_url:
                new_receipt.image_url = sync_receipt_image_storage(
                    new_receipt.image_url,
                    False,
                )

        await db.commit()

    except IntegrityError:
        await db.rollback()

        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid request: The provided company_id or category_id does not exist in the database."
        )

    return await _get_user_receipt_or_404(receipt_id, user_id, db)


@router.post("/extract", response_model=ReceiptExtractAcceptedResponse, status_code=status.HTTP_202_ACCEPTED)
async def extract_receipt_data(
        file: UploadFile = File(...),
        current_user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db),
):
    """
    POST /api/v1/receipts/extract
    Accepts JPEG/PNG, returns immediately with receipt_id (status PROCESSING).
    Poll GET /receipts/{receipt_id} until status is not PROCESSING.
    """
    user_id = current_user.id

    ALLOWED_TYPES = ["image/jpeg", "image/png"]
    if file.content_type not in ALLOWED_TYPES:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Only JPEG and PNG images are allowed.",
        )

    await _validate_upload_size(file)

    file_url = await save_upload_file(file)

    new_receipt = Receipt(
        user_id=user_id,
        image_url=file_url,
        status="PROCESSING",
    )

    db.add(new_receipt)
    await db.commit()
    await db.refresh(new_receipt)

    schedule_receipt_extraction(new_receipt.id, file_url, "image")

    return ReceiptExtractAcceptedResponse(receipt_id=new_receipt.id)


@router.get("/lookup/{nip}")
async def lookup_company_by_nip(
        nip: str,
        current_user: User = Depends(get_current_user),
):
    company_data = await fetch_company_by_nip(nip)

    if not company_data:
        raise HTTPException(status_code=404, detail="Company not found or API unavailable.")

    return company_data


@router.get("", response_model=ReceiptListResponse)
async def list_receipts(
        limit: int = Query(20, ge=1, le=100),
        offset: int = Query(0, ge=0),
        has_warranty_items: Optional[bool] = Query(
            None,
            description="If true/false, filter receipts that have (or lack) warranty items.",
        ),
        status_filter: Optional[str] = Query(
            None,
            alias="status",
            description="Filter by receipt status (e.g. PROCESSING, FAILED, VERIFIED_COMPLETED).",
        ),
        current_user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db)
):
    user_id = current_user.id
    filters = [Receipt.user_id == user_id]
    if has_warranty_items is not None:
        filters.append(Receipt.has_warranty_items == has_warranty_items)
    if status_filter is not None:
        filters.append(Receipt.status == status_filter.strip())

    count_stmt = (
        select(func.count())
        .select_from(Receipt)
        .where(*filters)
    )

    total = (await db.execute(count_stmt)).scalar_one()

    stmt = (
        select(Receipt)
        .where(*filters)
        .options(
            selectinload(Receipt.items).selectinload(ReceiptItem.category),
            selectinload(Receipt.company)
        )
        .order_by(Receipt.created_at.desc())
        .limit(limit)
        .offset(offset)
    )

    result = await db.execute(stmt)
    receipts = result.scalars().all()

    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "items": receipts
    }


def _to_extraction_job_item(
    receipt: Receipt,
    active_ids: set[int],
    now: datetime,
) -> ExtractionJobItem:
    anchor = receipt.extraction_started_at or receipt.created_at
    age = max(0, int((now - anchor).total_seconds())) if anchor else 0
    return ExtractionJobItem(
        receipt_id=receipt.id,
        status=receipt.status,
        created_at=receipt.created_at,
        extraction_started_at=receipt.extraction_started_at,
        extraction_attempts=receipt.extraction_attempts or 0,
        extraction_error=receipt.extraction_error,
        image_url=receipt.image_url,
        is_stale=is_stale_processing(receipt, now),
        is_active_in_process=receipt.id in active_ids,
        age_seconds=age,
    )


@router.get("/extraction/status", response_model=ExtractionStatusResponse)
async def get_extraction_status(
        current_user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db),
        recent_failures_limit: int = Query(10, ge=1, le=50),
):
    """
    Observability for async extract jobs.
    - metrics: process-wide counters since server start
    - processing: current user's PROCESSING receipts (stuck/in-flight)
    - recent_failures: current user's latest FAILED extractions
    """
    now = datetime.utcnow()
    active_ids = set(get_active_extraction_ids())
    metrics = ExtractionMetricsResponse(**get_extraction_metrics())

    processing_result = await db.execute(
        select(Receipt)
        .where(Receipt.user_id == current_user.id, Receipt.status == "PROCESSING")
        .order_by(Receipt.created_at.desc())
    )
    processing = [
        _to_extraction_job_item(r, active_ids, now)
        for r in processing_result.scalars().all()
    ]

    failed_result = await db.execute(
        select(Receipt)
        .where(Receipt.user_id == current_user.id, Receipt.status == "FAILED")
        .order_by(Receipt.created_at.desc())
        .limit(recent_failures_limit)
    )
    recent_failures = [
        _to_extraction_job_item(r, active_ids, now)
        for r in failed_result.scalars().all()
    ]

    return ExtractionStatusResponse(
        metrics=metrics,
        processing=processing,
        recent_failures=recent_failures,
        stale_minutes=settings.EXTRACTION_STALE_MINUTES,
    )


@router.get("/{receipt_id}", response_model=ReceiptResponse)
async def get_receipt(
        receipt_id: int,
        current_user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db)
):
    user_id = current_user.id
    return await _get_user_receipt_or_404(receipt_id, user_id, db)


@router.put("/{receipt_id}", response_model=ReceiptResponse)
async def update_receipt(
        receipt_id: int,
        payload: ReceiptUpdate,
        current_user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db)
):
    """
    PUT /api/v1/receipts/{id}
    Manual edit. NIP looks up/creates formal Company (DB-cached).
    shop_name / company_name only update Receipt.shop_name — never Company.name.
    """
    user_id = current_user.id
    receipt = await _get_user_receipt_or_404(receipt_id, user_id, db)

    # Track if major fields were updated to mark as MANUALLY_CORRECTED
    major_update_made = False

    if payload.purchase_date is not None:
        receipt.purchase_date = payload.purchase_date
    if payload.total_amount is not None:
        receipt.total_amount = payload.total_amount
        major_update_made = True
    if payload.status is not None:
        receipt.status = payload.status
    if payload.company_id is not None:
        receipt.company_id = payload.company_id
        major_update_made = True

    # Shop / brand label → receipt only (never poison global Company.name)
    if payload.shop_name is not None:
        text = payload.shop_name.strip()
        receipt.shop_name = text or None
        major_update_made = True
    elif payload.company_name is not None:
        # Legacy field: treat as shop/brand label on this receipt only
        text = payload.company_name.strip()
        receipt.shop_name = text or None
        major_update_made = True

    if payload.store_address is not None:
        text = payload.store_address.strip()
        receipt.store_address = text[:255] if text else None
        major_update_made = True

    # NIP → look up or create formal Company (Biała Lista only on cache miss)
    if payload.company_nip is not None:
        major_update_made = True
        nip = clean_nip(payload.company_nip)
        if len(nip) == 10:
            company = await get_or_create_company_by_nip(
                db,
                nip,
                fallback_name=receipt.shop_name or "Unknown",
            )
            if company:
                receipt.company_id = company.id
                if receipt.shop_name:
                    ensure_brand_in_catalog(
                        brand_name=receipt.shop_name,
                        nip=nip,
                        legal_alias=company.name,
                    )
        elif not nip:
            # Explicit empty NIP: detach company from this receipt only
            receipt.company_id = None

    if payload.items is not None:
        major_update_made = True

        await db.execute(
            delete(ReceiptItem).where(ReceiptItem.receipt_id == receipt.id)
        )

        for item in payload.items:
            under_flag = item.is_under_warranty if item.is_under_warranty is not None else False
            under_w, end_w = apply_warranty(
                under_flag,
                receipt.purchase_date,
                item.warranty_end_date,
            )
            db.add(
                ReceiptItem(
                    receipt_id=receipt.id,
                    name=item.name,
                    quantity=item.quantity,
                    price=item.price,
                    is_under_warranty=under_w,
                    warranty_end_date=end_w,
                    category_id=item.category_id,
                )
            )

        await finalize_receipt_warranty_state(db, receipt)

    if payload.status is None and major_update_made:
        receipt.status = "MANUALLY_CORRECTED"

    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid request: The provided company_id or category_id does not exist in the database."
        )

    return await _get_user_receipt_or_404(receipt_id, user_id, db)


@router.delete("/{receipt_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_receipt(
        receipt_id: int,
        current_user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db)
):
    user_id = current_user.id
    receipt = await _get_user_receipt_or_404(receipt_id, user_id, db)
    cancel_receipt_extraction(receipt_id)
    delete_receipt_image(receipt.image_url)
    await db.delete(receipt)
    await db.commit()

    return None


async def _get_user_receipt_or_404(
        receipt_id: int,
        user_id: int,
        db: AsyncSession
) -> Receipt:
    query = (
        select(Receipt)
        .where(Receipt.id == receipt_id, Receipt.user_id == user_id)
        .options(
            selectinload(Receipt.items).selectinload(ReceiptItem.category),
            selectinload(Receipt.company)
        )
    )

    result = await db.execute(query)
    receipt = result.scalars().first()

    if not receipt:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Receipt not found."
        )

    if receipt.status == "PROCESSING":
        await reconcile_stale_processing_receipt(db, receipt)
        await db.refresh(receipt)

    return receipt
