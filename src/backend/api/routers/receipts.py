from fastapi import APIRouter, Depends, UploadFile, File, HTTPException, status
from fastapi.concurrency import run_in_threadpool
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from pathlib import Path
from datetime import datetime

from src.backend.db.database import get_db
from src.backend.db.models import User, Receipt, Company, ReceiptItem
from src.backend.api.deps import get_current_user
from src.backend.core.storage import save_upload_file
from src.backend.schemas import ReceiptResponse

from src.ai_pipeline import process_receipt_end_to_end

router = APIRouter(prefix="/receipts", tags=["Receipts"])
BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent.parent


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
    """

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
        user_id=current_user.id,
        image_url=file_url,
        status="PROCESSING"
    )

    db.add(new_receipt)
    await db.commit()
    await db.refresh(new_receipt)

    # --- 4. Run Async AI Pipeline ---
    try:
        logger.info(f"Starting AI Pipeline for receipt ID {new_receipt.id}")

        filename = file_url.split("/")[-1]
        local_path = str(BASE_DIR / "media" / "receipts" / filename)

        extracted_data = await run_in_threadpool(
            process_receipt_end_to_end,
            local_path,
            True
        )

        if "error" in extracted_data:
            raise RuntimeError(extracted_data["error"])

        logger.info(f"AI Pipeline completed for receipt ID {new_receipt.id}")

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
        sklep_name = extracted_data.get("sklep") or "Unknown Company"

        if nip:
            stmt = select(Company).where(Company.nip == nip)
            existing_company = (await db.execute(stmt)).scalars().first()

            if existing_company:
                new_receipt.company_id = existing_company.id
            else:
                new_company = Company(nip=nip, name=sklep_name)
                db.add(new_company)
                await db.flush()
                new_receipt.company_id = new_company.id

        pozycje = extracted_data.get("pozycje") or []
        for p in pozycje:
            raw_cena = p.get("cena")
            safe_cena = float(raw_cena) if raw_cena is not None else 0.0

            raw_ilosc = p.get("ilosc")
            safe_ilosc = float(raw_ilosc) if raw_ilosc is not None else 1.0

            new_item = ReceiptItem(
                receipt_id=new_receipt.id,
                name=p.get("nazwa") or "Unknown Item",
                quantity=safe_ilosc,
                price=safe_cena
            )
            db.add(new_item)

    except Exception as e:
        safe_receipt_id = new_receipt.id

        logger.error(f"AI Pipeline crashed for receipt ID {safe_receipt_id}: {str(e)}")

        await db.rollback()

        new_receipt = await db.get(Receipt, safe_receipt_id)
        new_receipt.status = "FAILED"

    # --- 5. Finalize Transaction and Return Data ---
    db.add(new_receipt)
    await db.commit()

    query = (
        select(Receipt)
        .where(Receipt.id == new_receipt.id)
        .options(
            selectinload(Receipt.items),
            selectinload(Receipt.company)
        )
    )
    result = await db.execute(query)
    full_receipt = result.scalars().one()

    return full_receipt