import sys
from fastapi import FastAPI, Depends, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pathlib import Path
from loguru import logger

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.backend.api.routers import receipts, auth, users, warranties, categories, statistics
from src.backend.core.config import settings
from src.backend.core.exceptions import validation_exception_handler, global_exception_handler
from src.backend.core.middleware import RequestLoggingMiddleware
from src.backend.db.database import get_db

# --- 1. Configure Global Logger ---
# Remove the default Loguru handler and add a clean, formatted one
logger.remove()
logger.add(sys.stderr,
           format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>")

# --- 2. Initialize FastAPI with metadata ---
app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.VERSION,
    description=settings.DESCRIPTION,
)

# --- 3. Configure CORS ---
if settings.BACKEND_CORS_ORIGINS:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.BACKEND_CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

# --- 4. Add Middlewares ---
# Added last so it wraps the entire application, including CORS
app.add_middleware(RequestLoggingMiddleware)

# --- 5. Add Exception Handlers ---
app.add_exception_handler(RequestValidationError, validation_exception_handler)
app.add_exception_handler(Exception, global_exception_handler)

# --- 6. App Routers ---
app.include_router(auth.router, prefix="/api/v1")
app.include_router(receipts.router, prefix="/api/v1")
app.include_router(users.router, prefix="/api/v1")
app.include_router(warranties.router, prefix="/api/v1")
app.include_router(categories.router, prefix="/api/v1")
app.include_router(statistics.router, prefix="/api/v1")

# --- 7. Mount Static Files for Media ---
# This allows browsers and mobile apps to access the saved images via URL
BASE_DIR = Path(__file__).resolve().parent.parent.parent
media_path = BASE_DIR / "media"
media_path.mkdir(exist_ok=True)
app.mount("/media", StaticFiles(directory=str(media_path)), name="media")

# --- 8. Health Check Endpoint ---
@app.get("/health", tags=["System"])
async def health_check(db: AsyncSession = Depends(get_db)):  # FIXED: Injected the db dependency
    """
    Verifies the API is running and the database connection is active.
    """
    try:
        # Execute a raw SQL query to test the async connection
        result = await db.execute(text("SELECT 1"))

        is_database_connected = result.scalar() == 1

        if not is_database_connected:
            raise HTTPException(status_code=500, detail="Database responded, but SELECT 1 failed.")

        return {
            "status": "ok",
            "version": settings.VERSION,
            "database": "connected"
        }
    except Exception as e:
        raise HTTPException(
            status_code=503,
            detail=f"Service Unavailable: Database connection failed. Details: {str(e)}"
        )