from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import List

class Settings(BaseSettings):
    # These variables will automatically be populated from the .env file
    PROJECT_NAME: str
    VERSION: str
    DESCRIPTION: str
    DATABASE_URL: str

    # CORS Origins - Allowing local development and Expo/React Native default ports
    BACKEND_CORS_ORIGINS: List[str] = [
        "http://localhost:8000",
        "http://localhost:3000",
        "http://localhost:8081",
        "http://127.0.0.1:8000"
    ]

    model_config = SettingsConfigDict(env_file=".env", env_ignore_empty=True, extra="ignore")

settings = Settings()