from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import List, Optional

class Settings(BaseSettings):
    # These variables will automatically be populated from the .env file
    PROJECT_NAME: str
    VERSION: str
    DESCRIPTION: str

    DATABASE_URL: str

    SECRET_KEY: str
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60

    STORAGE_BACKEND: str = "split"  # local = all local; split = warranty→Azure, rest→local

    AZURE_STORAGE_CONNECTION_STRING: Optional[str] = None
    AZURE_CONTAINER_NAME: Optional[str] = None
    # Optional: prefix for relative /media/... URLs in API responses (e.g. https://api.example.com)
    PUBLIC_API_BASE_URL: Optional[str] = None

    # Background receipt extraction (async upload → poll)
    EXTRACTION_MAX_RETRIES: int = 3
    EXTRACTION_RETRY_DELAY_SECONDS: float = 5.0
    EXTRACTION_STALE_MINUTES: int = 120
    EXTRACTION_WATCHDOG_INTERVAL_SECONDS: int = 900

    # PaddleOCR device: "gpu", "cpu", or "auto" (try gpu then fall back to cpu)
    OCR_DEVICE: str = "auto"


    # CORS Origins - Allowing local development and Expo/React Native default ports
    BACKEND_CORS_ORIGINS: List[str] = [
        "http://localhost:8000",
        "http://localhost:3000",
        "http://localhost:8081",
        "http://127.0.0.1:8000"
    ]

    model_config = SettingsConfigDict(env_file=".env", env_ignore_empty=True, extra="ignore")

settings = Settings()