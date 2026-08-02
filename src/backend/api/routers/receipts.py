from fastapi import APIRouter, Depends, UploadFile, File, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from src.backend.db.database import get_db
from src.backend.db.models import User, Receipt
from src.backend.api.deps import get_current_user
from src.backend.core.storage import save_upload_file

from src.backend.schemas import ReceiptResponse

router = APIRouter(prefix="/receipts", tags=["Receipts"])

@router.post("/upload", response_model=ReceiptResponse, status_code=status.HTTP_201_CREATED)
async def upload_receipt_image(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Uploads a raw receipt image.
    Requires a valid JWT token.
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