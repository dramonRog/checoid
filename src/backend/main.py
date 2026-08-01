from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from src.backend.core.config import settings

# 1. Initialize FastAPI with metadata
app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.VERSION,
    description=settings.DESCRIPTION,
)

# 1. Initialize FastAPI with metadata
if settings.BACKEND_CORS_ORIGINS:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.BACKEND_CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

# 3. Health Check Endpoint
@app.get("/health", tags=["System"])
async def health_check():
    """
    Verify server uptime and basic health.
    """
    return {
        "status": "OK",
        "message": f"{settings.PROJECT_NAME} v{settings.VERSION} is running smoothly.",
    }