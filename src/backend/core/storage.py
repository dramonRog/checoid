import shutil
import tempfile
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional
from urllib.parse import urlparse

from fastapi import HTTPException, UploadFile
from azure.storage.blob import BlobServiceClient, ContentSettings

from src.backend.core.config import settings


BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
MEDIA_DIR = BASE_DIR / "media" / "receipts"

MEDIA_DIR.mkdir(parents=True, exist_ok=True)


def is_local_media_url(image_url: str) -> bool:
    return image_url.startswith("/media/receipts/")


def is_remote_image_url(image_url: str) -> bool:
    return image_url.startswith("http://") or image_url.startswith("https://")


def resolve_public_image_url(image_url: Optional[str]) -> Optional[str]:
    """
    Return a client-usable image URL.
    Local relative paths become absolute when PUBLIC_API_BASE_URL is set.
    Azure / other absolute URLs are returned unchanged.
    """
    if not image_url:
        return None
    if is_remote_image_url(image_url):
        return image_url
    if settings.PUBLIC_API_BASE_URL and image_url.startswith("/"):
        return f"{settings.PUBLIC_API_BASE_URL.rstrip('/')}{image_url}"
    return image_url


def _azure_blob_service() -> BlobServiceClient:
    if not settings.AZURE_STORAGE_CONNECTION_STRING:
        raise HTTPException(
            status_code=500,
            detail="Azure storage credentials are not fully configured in the environment variables.",
        )
    return BlobServiceClient.from_connection_string(settings.AZURE_STORAGE_CONNECTION_STRING)


def _parse_azure_blob_location(image_url: str) -> tuple[str, str]:
    parsed = urlparse(image_url)
    parts = parsed.path.lstrip("/").split("/", 1)
    if len(parts) != 2:
        raise HTTPException(
            status_code=500,
            detail=f"Could not parse Azure blob URL: {image_url}",
        )
    return parts[0], parts[1]


def _download_azure_blob_to_path(image_url: str, dest_path: Path) -> None:
    container, blob_name = _parse_azure_blob_location(image_url)
    blob_client = _azure_blob_service().get_blob_client(container=container, blob=blob_name)
    with open(dest_path, "wb") as handle:
        handle.write(blob_client.download_blob().readall())


def prepare_local_path_for_pipeline(image_url: str) -> tuple[str, bool]:
    """
    Resolve stored image_url to a local filesystem path for the AI pipeline.
    Returns (path, is_temp). Caller must delete temp files when is_temp is True.
    """
    if is_local_media_url(image_url):
        filename = image_url.rsplit("/", 1)[-1]
        return str(MEDIA_DIR / filename), False

    if is_remote_image_url(image_url):
        suffix = Path(urlparse(image_url).path).suffix or ".bin"
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
        tmp_path = Path(tmp.name)
        tmp.close()
        try:
            if "blob.core.windows.net" in image_url:
                _download_azure_blob_to_path(image_url, tmp_path)
            else:
                raise HTTPException(
                    status_code=500,
                    detail="Unsupported remote image URL for pipeline processing.",
                )
        except Exception:
            tmp_path.unlink(missing_ok=True)
            raise
        return str(tmp_path), True

    # Legacy: bare filename saved only under media dir
    legacy_path = MEDIA_DIR / image_url
    if legacy_path.is_file():
        return str(legacy_path), False

    raise HTTPException(
        status_code=500,
        detail=f"Could not resolve image path for pipeline: {image_url}",
    )


@contextmanager
def local_pipeline_path(image_url: str) -> Iterator[str]:
    path, is_temp = prepare_local_path_for_pipeline(image_url)
    try:
        yield path
    finally:
        if is_temp:
            Path(path).unlink(missing_ok=True)


async def save_upload_file(file: UploadFile) -> str:
    """Save upload to local disk or Azure Blob Storage; return stored URL."""
    file_extension = ""
    if file.filename and "." in file.filename:
        file_extension = f".{file.filename.split('.')[-1]}"

    unique_filename = f"{uuid.uuid4()}{file_extension}"

    if settings.STORAGE_BACKEND.lower() == "azure":
        return _save_to_azure(file, unique_filename)
    return _save_to_local(file, unique_filename)


def _save_to_local(file: UploadFile, unique_filename: str) -> str:
    file_path = MEDIA_DIR / unique_filename
    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    return f"/media/receipts/{unique_filename}"


def _save_to_azure(file: UploadFile, unique_filename: str) -> str:
    if not settings.AZURE_STORAGE_CONNECTION_STRING or not settings.AZURE_CONTAINER_NAME:
        raise HTTPException(
            status_code=500,
            detail="Azure storage credentials are not fully configured in the environment variables.",
        )

    try:
        blob_service_client = _azure_blob_service()
        blob_name = f"receipts/{unique_filename}"
        blob_client = blob_service_client.get_blob_client(
            container=settings.AZURE_CONTAINER_NAME,
            blob=blob_name,
        )

        content_settings = ContentSettings(content_type=file.content_type)
        blob_client.upload_blob(
            file.file,
            overwrite=True,
            content_settings=content_settings,
        )

        return blob_client.url

    except HTTPException:
        raise
    except Exception as ex:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to upload image to Azure Blob Storage: {str(ex)}",
        )
