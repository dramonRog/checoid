import mimetypes
import shutil
import tempfile
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional
from urllib.parse import urlparse

from fastapi import HTTPException, UploadFile
from azure.storage.blob import BlobServiceClient, ContentSettings
from loguru import logger

from src.backend.core.config import settings


BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
MEDIA_DIR = BASE_DIR / "media" / "receipts"

MEDIA_DIR.mkdir(parents=True, exist_ok=True)


def azure_configured() -> bool:
    return bool(settings.AZURE_STORAGE_CONNECTION_STRING and settings.AZURE_CONTAINER_NAME)


def split_storage_enabled() -> bool:
    """
    When enabled: warranty receipts → Azure, non-warranty → local.
    STORAGE_BACKEND=local disables Azure migration; split/azure enables it.
    """
    backend = settings.STORAGE_BACKEND.lower()
    if backend == "local":
        return False
    return azure_configured()


def is_local_media_url(image_url: str) -> bool:
    return image_url.startswith("/media/receipts/")


def is_azure_blob_url(image_url: str) -> bool:
    return is_remote_image_url(image_url) and "blob.core.windows.net" in image_url


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


def _local_path_from_url(image_url: str) -> Path:
    filename = image_url.rsplit("/", 1)[-1]
    return MEDIA_DIR / filename


def _download_azure_blob_to_path(image_url: str, dest_path: Path) -> None:
    container, blob_name = _parse_azure_blob_location(image_url)
    blob_client = _azure_blob_service().get_blob_client(container=container, blob=blob_name)
    with open(dest_path, "wb") as handle:
        handle.write(blob_client.download_blob().readall())


def delete_local_file(image_url: str) -> None:
    if is_local_media_url(image_url):
        _local_path_from_url(image_url).unlink(missing_ok=True)


def delete_azure_blob(image_url: str) -> None:
    if not is_azure_blob_url(image_url):
        return
    container, blob_name = _parse_azure_blob_location(image_url)
    blob_client = _azure_blob_service().get_blob_client(container=container, blob=blob_name)
    blob_client.delete_blob()


def upload_local_file_to_azure(local_path: Path, unique_filename: Optional[str] = None) -> str:
    if not azure_configured():
        raise HTTPException(
            status_code=500,
            detail="Azure storage credentials are not fully configured in the environment variables.",
        )

    filename = unique_filename or local_path.name
    blob_name = f"receipts/{filename}"
    content_type = mimetypes.guess_type(str(local_path))[0] or "application/octet-stream"
    blob_client = _azure_blob_service().get_blob_client(
        container=settings.AZURE_CONTAINER_NAME,
        blob=blob_name,
    )
    with open(local_path, "rb") as handle:
        blob_client.upload_blob(
            handle,
            overwrite=True,
            content_settings=ContentSettings(content_type=content_type),
        )
    return blob_client.url


def migrate_local_to_azure(image_url: str) -> str:
    local_path = _local_path_from_url(image_url)
    if not local_path.is_file():
        raise HTTPException(
            status_code=500,
            detail=f"Local receipt file not found: {image_url}",
        )
    azure_url = upload_local_file_to_azure(local_path, local_path.name)
    delete_local_file(image_url)
    logger.info(f"Migrated receipt image to Azure: {local_path.name}")
    return azure_url


def migrate_azure_to_local(image_url: str) -> str:
    suffix = Path(urlparse(image_url).path).suffix or ".bin"
    unique_filename = f"{uuid.uuid4()}{suffix}"
    dest = MEDIA_DIR / unique_filename
    _download_azure_blob_to_path(image_url, dest)
    delete_azure_blob(image_url)
    local_url = f"/media/receipts/{unique_filename}"
    logger.info(f"Migrated receipt image to local: {unique_filename}")
    return local_url


def sync_receipt_image_storage(
    image_url: Optional[str],
    has_warranty_items: bool,
) -> Optional[str]:
    """
    Place receipt image on the correct backend:
    - has_warranty_items → Azure (sejf / durable storage)
    - otherwise → local disk
    """
    if not image_url or not split_storage_enabled():
        return image_url

    if has_warranty_items:
        if is_local_media_url(image_url):
            try:
                return migrate_local_to_azure(image_url)
            except Exception as ex:
                logger.warning(f"Could not migrate warranty receipt to Azure, keeping local: {ex}")
        return image_url

    if is_azure_blob_url(image_url):
        try:
            return migrate_azure_to_local(image_url)
        except Exception as ex:
            logger.warning(f"Could not migrate non-warranty receipt to local, keeping Azure: {ex}")
    return image_url


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
            if is_azure_blob_url(image_url):
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
    """
    Stage upload on local disk for pipeline processing.
    After warranty is determined, sync_receipt_image_storage moves warranty
    receipts to Azure when split storage is enabled.
    """
    file_extension = ""
    if file.filename and "." in file.filename:
        file_extension = f".{file.filename.split('.')[-1]}"

    unique_filename = f"{uuid.uuid4()}{file_extension}"
    return _save_to_local(file, unique_filename)


def _save_to_local(file: UploadFile, unique_filename: str) -> str:
    file_path = MEDIA_DIR / unique_filename
    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    return f"/media/receipts/{unique_filename}"
