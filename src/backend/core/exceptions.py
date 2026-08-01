from fastapi import Request, status
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from loguru import logger

async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """
    Overrides the default FastAPI 422 validation error.
    Returns a cleaner, flattened JSON structure for the mobile app.
    """

    # Flatten the error locations and messages
    errors = [{"field": ".".join(map(str, err["loc"])), "message": err["msg"]} for err in exc.errors()]

    logger.warning(f"Validation error on {request.method} {request.url.path}: {errors}")

    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={
            "error": "Validation Error",
            "details": errors
        },
    )


async def global_exception_handler(request: Request, exc: Exception):
    """
    Catches ALL unhandled exceptions (500s)
    Logs the full stack trace on the server, but returns a safe, generic message to the client.
    """

    logger.exception(f"Unhandled exception on {request.method} {request.url.path}")

    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "error": "Internal Server Error",
            "message": "An unexpected error occurred on the server. Please try again later."
        },
    )