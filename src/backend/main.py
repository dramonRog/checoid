import sys
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger

from src.backend.core.config import settings
from src.backend.core.exceptions import validation_exception_handler, global_exception_handler
from src.backend.core.middleware import RequestLoggingMiddleware


# --- 1. Configure Global Logger ---
# Remove the default Loguru handler and add a clean, formatted one
logger.remove()
logger.add(sys.stderr, format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>")


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


# --- 6. Health Check Endpoint ---
@app.get("/health", tags=["System"])
async def health_check():
    """
    Verify server uptime and basic health.
    """
    # raise ValueError("This is simulated crash to test the exception handler!")

    return {
        "status": "OK",
        "message": f"{settings.PROJECT_NAME} v{settings.VERSION} is running smoothly.",
    }