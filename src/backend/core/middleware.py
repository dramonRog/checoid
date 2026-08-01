import time
from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from loguru import logger

class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """
    Intercepts all incoming HTTP requests to log their path, method, status and execution duration.
    """
    async def dispatch(self, request: Request, call_next):
        start_time = time.time()

        try:
            # Process the actual request
            response = await call_next(request)
            status_code = response.status_code
        except Exception as e:
            status_code = 500
            raise
        finally:
            process_time = time.time() - start_time
            formatted_process_time = f"{process_time * 1000:.2f}ms"

            logger.info(
                f"{request.method} {request.url.path} - "
                f"Status: {status_code} - "
                f"Duration: {formatted_process_time}"
            )

        return response