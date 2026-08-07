import asyncio
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Depends, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from loguru import logger
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.backend.api.routers import receipts, auth, users, warranties, categories, statistics
from src.backend.core.config import settings
from src.backend.core.exceptions import validation_exception_handler, global_exception_handler
from src.backend.core.middleware import RequestLoggingMiddleware
from src.backend.core.rate_limit import limiter
from src.backend.db.database import get_db, AsyncSessionLocal
from src.backend.services.categories import seed_categories
from src.backend.services.receipt_extraction import (
    recover_interrupted_extractions,
    extraction_watchdog_loop,
    get_extraction_metrics,
)
from src.backend.services.ai_health import collect_ai_component_health

# --- 1. Configure Global Logger ---
logger.remove()
logger.add(
    sys.stderr,
    format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | "
           "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>",
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with AsyncSessionLocal() as session:
        try:
            created = await seed_categories(session)
            if created:
                logger.info(f"Category seed inserted {created} new rows.")
            else:
                logger.info("Category catalog already present in database.")
        except Exception as e:
            logger.error(f"Failed to seed categories on startup: {e}")

    try:
        await recover_interrupted_extractions()
    except Exception as e:
        logger.error(f"Failed to recover interrupted extractions on startup: {e}")

    watchdog_task = asyncio.create_task(extraction_watchdog_loop())

    yield

    watchdog_task.cancel()
    try:
        await watchdog_task
    except asyncio.CancelledError:
        pass


# --- 2. Initialize FastAPI with metadata ---
app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.VERSION,
    description=settings.DESCRIPTION,
    lifespan=lifespan,
)
app.state.limiter = limiter
app.add_middleware(SlowAPIMiddleware)


async def _rate_limit_exceeded_handler(request: Request, exc: RateLimitExceeded):
    return JSONResponse(
        status_code=429,
        content={
            "error": "Rate limit exceeded",
            "message": f"Too many requests: {exc.detail}",
        },
    )


app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

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
BASE_DIR = Path(__file__).resolve().parent.parent.parent
media_path = BASE_DIR / "media"
media_path.mkdir(exist_ok=True)
app.mount("/media", StaticFiles(directory=str(media_path)), name="media")


# --- 8. Health Check Endpoints ---
@app.get("/health", tags=["System"])
async def health_check(db: AsyncSession = Depends(get_db)):
    """
    Liveness/readiness for Docker and load balancers.
    Returns 503 only when the database is unreachable.
    AI component status is informational (ok | degraded | unavailable).
    """
    try:
        result = await db.execute(text("SELECT 1"))
        if result.scalar() != 1:
            raise HTTPException(status_code=500, detail="Database responded, but SELECT 1 failed.")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=503,
            detail=f"Service Unavailable: Database connection failed. Details: {str(e)}",
        )

    ai = await collect_ai_component_health()
    overall = "ok" if ai["status"] == "ok" else "degraded"

    return {
        "status": overall,
        "version": settings.VERSION,
        "database": "connected",
        "ai": ai,
        "extraction": get_extraction_metrics(),
        "rate_limit_enabled": settings.RATE_LIMIT_ENABLED,
    }


@app.get("/health/live", tags=["System"])
async def health_live():
    """Minimal liveness probe (process is up)."""
    return {"status": "ok"}
