from fastapi import APIRouter, Depends, UploadFile, File, HTTPException, status
from fastapi.concurrency import run_in_threadpool
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from pathlib import Path

from src.backend.db.database import get_db
from src.backend.db.models import User, Receipt
from src.backend.api.deps import get_current_user
from src.backend.core.storage import save_upload_file
from src.backend.schemas import ReceiptResponse

from src.ai_pipeline import process_receipt_end_to_end

router = APIRouter(prefix="/receipts", tags=["Receipts"])
BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent.parent

@router.post("/upload", response_model=ReceiptResponse, status_code=status.HTTP_201_CREATED)
async def upload_receipt_image(
        file: UploadFile = File(...),
        current_user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db)
):
    """
    Uploads a raw receipt image.
    Requires a valid JWT token.
    Processes it via AI pipeline for receipt processing.
    """

    if not file.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="File provided is not an image.")

    file_url = await save_upload_file(file)

    new_receipt = Receipt(
        user_id=current_user.id,
        image_url=file_url,
        status="PROCESSING"
    )

    db.add(new_receipt)
    await db.commit()
    await db.refresh(new_receipt)

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

    except Exception as e:
        logger.error(f"AI Pipeline crashed for receipt ID {new_receipt.id}: {str(e)}")
        new_receipt.status = "FAILED"

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