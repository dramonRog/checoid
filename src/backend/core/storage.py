import shutil
import uuid
from pathlib import Path
from fastapi import UploadFile


BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
MEDIA_DIR = BASE_DIR / "media" / "receipts"

MEDIA_DIR.mkdir(parents=True, exist_ok=True)

async def save_upload_file(file: UploadFile) -> str:
    """Saves an uploaded file to the local disk and returns the relative URL."""

    file_extension = ""
    if "." in file.filename:
        file_extension = f".{file.filename.split('.')[-1]}"

    unique_filename = f"{uuid.uuid4()}{file_extension}"
    file_path = MEDIA_DIR / unique_filename

    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    return f"/media/receipts/{unique_filename}"