from fastapi import APIRouter, Depends, UploadFile, File, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from src.backend.db.database import get_db
from src.backend.db.models import User
from src.backend.api.deps import get_current_user
from src.backend.core.storage import save_upload_file

router = APIRouter(prefix="/receipts", tags=["Receipts"])

@router.post("/upload")
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

    return {
        "status": "success",
        "original_filename": file.filename,
        "file_url": file_url
    }