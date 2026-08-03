import shutil
import uuid
from pathlib import Path
from fastapi import UploadFile, HTTPException
from azure.storage.blob import BlobServiceClient, ContentSettings
from src.backend.core.config import settings


BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
MEDIA_DIR = BASE_DIR / "media" / "receipts"

MEDIA_DIR.mkdir(parents=True, exist_ok=True)

async def save_upload_file(file: UploadFile) -> str:
    """Saves an uploaded file to the local disk or Azure Blob Storage(based on the STORAGE_BACKEND environment variable)
     and returns the relative URL."""

    file_extension = ""
    if file.filename and "." in file.filename:
        file_extension = f".{file.filename.split('.')[-1]}"

    unique_filename = f"{uuid.uuid4()}{file_extension}"

    # Switch logic based on .env configuration
    return _save_to_azure(file, unique_filename) if settings.STORAGE_BACKEND.lower() == "azure" else _save_to_local(file, unique_filename)


def _save_to_local(file: UploadFile, unique_filename: str) -> str:
    file_path = MEDIA_DIR / unique_filename
    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    return f"/media/receipts/{unique_filename}"


def _save_to_azure(file: UploadFile, unique_filename: str) -> str:
    if not settings.AZURE_STORAGE_CONNECTION_STRING or not settings.AZURE_CONTAINER_NAME:
        raise HTTPException(
            status_code=500,
            detail="Azure storage credentials are not fully configured in the environment variables."
        )

    try:
        blob_service_client = BlobServiceClient.from_connection_string(settings.AZURE_STORAGE_CONNECTION_STRING)

        blob_name = f"receipts/{unique_filename}"
        blob_client = blob_service_client.get_blob_client(container=settings.AZURE_CONTAINER_NAME, blob=blob_name)

        content_settings = ContentSettings(content_type=file.content_type)
        blob_client.upload_blob(
            file.file,
            overwrite=True,
            content_settings=content_settings
        )

        return blob_client.url

    except Exception as ex:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to upload image to Azure Blob Storage: {str(ex)}"
        )