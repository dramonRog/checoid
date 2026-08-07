from fastapi import APIRouter, Depends, UploadFile, File, HTTPException, status, Query
from fastapi.concurrency import run_in_threadpool
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, delete
from sqlalchemy.orm import selectinload
from sqlalchemy.exc import IntegrityError
from pathlib import Path
from datetime import datetime
from typing import Optional

from src.ai_pipeline.pipeline import process_pdf_receipt
from src.backend.db.database import get_db
from src.backend.db.models import User, Receipt, ReceiptItem
from src.backend.api.deps import get_current_user
from src.backend.api.services.nip_lookup import fetch_company_by_nip
from src.backend.services.categories import get_or_create_category_id
from src.backend.services.company_resolution import (
    resolve_company_and_shop,
    get_or_create_company_by_nip,
)
from src.backend.services.warranty import apply_warranty, any_under_warranty
from src.backend.services.brands import clean_nip, ensure_brand_in_catalog
from src.backend.core.storage import save_upload_file
from src.backend.schemas import ReceiptResponse, ReceiptUpdate, ReceiptListResponse, ReceiptCreate

from src.ai_pipeline import process_receipt_end_to_end
from src.ai_pipeline.parser import categorize_product_names

router = APIRouter(prefix="/receipts", tags=["Receipts"])
BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent.parent

@router.post("/extract-pdf", response_model=ReceiptResponse, status_code=status.HTTP_201_CREATED)
async def extract_pdf_receipt_data(
        file: UploadFile = File(...),
        current_user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db)
):
    """
    POST /api/v1/receipts/extract-pdf
    Uploads a digital PDF receipt.
    Bypasses YOLO and OCR, extracting embedded text directly for fast LLM processing.
    Company/shop via smart resolution (DB cache first; Biała Lista only on NIP miss).
    """
    user_id = current_user.id

    if file.content_type != "application/pdf":
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Only PDF files are allowed for this endpoint."
        )

    MAX_FILE_SIZE = 10 * 1024 * 1024

    file.file.seek(0, 2)
    file_size = file.file.tell()
    file.file.seek(0)

    if file_size > MAX_FILE_SIZE:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="File is too large. Maximum allowed size is 10MB."
        )

    file_url = await save_upload_file(file)

    new_receipt = Receipt(
        user_id=user_id,
        image_url=file_url,
        status="PROCESSING"
    )

    db.add(new_receipt)
    await db.commit()
    await db.refresh(new_receipt)
    receipt_id = new_receipt.id

    try:
        logger.info(f"Starting Fast-Lane PDF Pipeline for receipt ID {receipt_id}")

        filename = file_url.split("/")[-1]
        local_path = str(BASE_DIR / "media" / "receipts" / filename)

        extracted_data = await run_in_threadpool(
            process_pdf_receipt,
            local_path,
            True
        )

        if "error" in extracted_data:
            raise RuntimeError(extracted_data["error"])

        logger.info(f"PDF Pipeline completed for receipt ID {receipt_id}")

        new_receipt.status = extracted_data.get("status", "COMPLETED")

        if extracted_data.get("suma_calkowita") is not None:
            new_receipt.total_amount = float(extracted_data["suma_calkowita"])

        date_str = extracted_data.get("data")
        if date_str:
            try:
                new_receipt.purchase_date = datetime.strptime(date_str, "%Y-%m-%d").date()
            except ValueError:
                logger.warning(f"Could not parse date format: {date_str}")

        nip = extracted_data.get("nip")
        sklep_name = extracted_data.get("sklep")
        resolution = await resolve_company_and_shop(db, nip, sklep_name)
        new_receipt.company_id = resolution.company_id
        new_receipt.shop_name = resolution.shop_name
        # Where the purchase happened (from receipt OCR) — not Company legal address
        adres = extracted_data.get("adres")
        if isinstance(adres, str) and adres.strip():
            new_receipt.store_address = adres.strip()[:255]
        if resolution.needs_review:
            new_receipt.status = "NEEDS_HUMAN_REVIEW"

        pozycje = extracted_data.get("pozycje") or []
        warranty_flags: list[bool] = []
        for p in pozycje:
            raw_cena = p.get("cena")
            safe_cena = float(raw_cena) if raw_cena is not None else 0.0

            raw_ilosc = p.get("ilosc")
            safe_ilosc = float(raw_ilosc) if raw_ilosc is not None else 1.0

            category_id = await get_or_create_category_id(db, p.get("kategoria"))
            under_w, end_w = apply_warranty(
                bool(p.get("gwarancja")),
                new_receipt.purchase_date,
            )
            warranty_flags.append(under_w)

            new_item = ReceiptItem(
                receipt_id=receipt_id,
                name=p.get("nazwa") or "Unknown Item",
                quantity=safe_ilosc,
                price=safe_cena,
                category_id=category_id,
                is_under_warranty=under_w,
                warranty_end_date=end_w,
            )
            db.add(new_item)

        new_receipt.has_warranty_items = any_under_warranty(warranty_flags)

    except Exception as e:
        logger.error(f"PDF Pipeline crashed for receipt ID {receipt_id}: {str(e)}")

        await db.rollback()

        new_receipt = await db.get(Receipt, receipt_id)
        new_receipt.status = "FAILED"

    db.add(new_receipt)
    await db.commit()

    return await _get_user_receipt_or_404(receipt_id, user_id, db)


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

            warranty_flags: list[bool] = []
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
                warranty_flags.append(under_w)

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

            new_receipt.has_warranty_items = any_under_warranty(warranty_flags)
        else:
            new_receipt.has_warranty_items = False

        await db.commit()

    except IntegrityError:
        await db.rollback()

        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid request: The provided company_id or category_id does not exist in the database."
        )

    return await _get_user_receipt_or_404(receipt_id, user_id, db)


@router.post("/extract", response_model=ReceiptResponse, status_code=status.HTTP_201_CREATED)
async def extract_receipt_data(
        file: UploadFile = File(...),
        current_user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db)
):
    """
    POST /api/v1/receipts/extract
    Uploads a raw receipt image for AI preprocessing and database archival.
    Enforces a 10MB size limit and restricts to JPEG/PNG.
    Company/shop via smart resolution (DB cache first; Biała Lista only on NIP miss).
    """
    user_id = current_user.id

    # --- 1. MIME Type Validation ---
    ALLOWED_TYPES = ["image/jpeg", "image/png"]
    if file.content_type not in ALLOWED_TYPES:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Only JPEG and PNG images are allowed."
        )

    # --- 2. File Size Validation (10MB Limit) ---
    MAX_FILE_SIZE = 10 * 1024 * 1024

    file.file.seek(0, 2)
    file_size = file.file.tell()
    file.file.seek(0)

    if file_size > MAX_FILE_SIZE:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="File is too large. Maximum allowed size is 10MB."
        )

    # --- 3. Save File and Create Database Record ---
    file_url = await save_upload_file(file)

    new_receipt = Receipt(
        user_id=user_id,
        image_url=file_url,
        status="PROCESSING"
    )

    db.add(new_receipt)
    await db.commit()
    await db.refresh(new_receipt)
    receipt_id = new_receipt.id

    # --- 4. Run Async AI Pipeline ---
    try:
        logger.info(f"Starting AI Pipeline for receipt ID {receipt_id}")

        filename = file_url.split("/")[-1]
        local_path = str(BASE_DIR / "media" / "receipts" / filename)

        extracted_data = await run_in_threadpool(
            process_receipt_end_to_end,
            local_path,
            True
        )

        if "error" in extracted_data:
            raise RuntimeError(extracted_data["error"])

        logger.info(f"AI Pipeline completed for receipt ID {receipt_id}")

        new_receipt.status = extracted_data.get("status", "COMPLETED")

        if extracted_data.get("suma_calkowita") is not None:
            new_receipt.total_amount = float(extracted_data["suma_calkowita"])

        date_str = extracted_data.get("data")
        if date_str:
            try:
                new_receipt.purchase_date = datetime.strptime(date_str, "%Y-%m-%d").date()
            except ValueError:
                logger.warning(f"Could not parse date format: {date_str}")

        nip = extracted_data.get("nip")
        sklep_name = extracted_data.get("sklep")
        resolution = await resolve_company_and_shop(db, nip, sklep_name)
        new_receipt.company_id = resolution.company_id
        new_receipt.shop_name = resolution.shop_name
        # Where the purchase happened (from receipt OCR) — not Company legal address
        adres = extracted_data.get("adres")
        if isinstance(adres, str) and adres.strip():
            new_receipt.store_address = adres.strip()[:255]
        if resolution.needs_review:
            new_receipt.status = "NEEDS_HUMAN_REVIEW"

        pozycje = extracted_data.get("pozycje") or []
        warranty_flags: list[bool] = []
        for p in pozycje:
            raw_cena = p.get("cena")
            safe_cena = float(raw_cena) if raw_cena is not None else 0.0

            raw_ilosc = p.get("ilosc")
            safe_ilosc = float(raw_ilosc) if raw_ilosc is not None else 1.0

            category_id = await get_or_create_category_id(db, p.get("kategoria"))
            under_w, end_w = apply_warranty(
                bool(p.get("gwarancja")),
                new_receipt.purchase_date,
            )
            warranty_flags.append(under_w)

            new_item = ReceiptItem(
                receipt_id=receipt_id,
                name=p.get("nazwa") or "Unknown Item",
                quantity=safe_ilosc,
                price=safe_cena,
                category_id=category_id,
                is_under_warranty=under_w,
                warranty_end_date=end_w,
            )
            db.add(new_item)

        new_receipt.has_warranty_items = any_under_warranty(warranty_flags)

    except Exception as e:
        logger.error(f"AI Pipeline crashed for receipt ID {receipt_id}: {str(e)}")

        await db.rollback()

        new_receipt = await db.get(Receipt, receipt_id)
        new_receipt.status = "FAILED"

    # --- 5. Finalize Transaction and Return Data ---
    db.add(new_receipt)
    await db.commit()

    return await _get_user_receipt_or_404(receipt_id, user_id, db)


@router.get("/lookup/{nip}")
async def lookup_company_by_nip(nip: str):
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
        current_user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db)
):
    user_id = current_user.id
    filters = [Receipt.user_id == user_id]
    if has_warranty_items is not None:
        filters.append(Receipt.has_warranty_items == has_warranty_items)

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

        warranty_flags: list[bool] = []
        for item in payload.items:
            under_flag = item.is_under_warranty if item.is_under_warranty is not None else False
            under_w, end_w = apply_warranty(
                under_flag,
                receipt.purchase_date,
                item.warranty_end_date,
            )
            warranty_flags.append(under_w)
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

        receipt.has_warranty_items = any_under_warranty(warranty_flags)

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

    return receipt
