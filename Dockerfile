# syntax=docker/dockerfile:1
# CPU-oriented API image for thesis demo / local compose.
# For GPU OCR on the host, run uvicorn outside Docker and use compose for Postgres only.

FROM python:3.11-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    OCR_DEVICE=cpu \
    STORAGE_BACKEND=local \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    libgl1 \
    libglib2.0-0 \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt requirements-dev.txt ./
# Prefer CPU Paddle in containers (requirements pins GPU build for host demos)
RUN sed 's/paddlepaddle-gpu/paddlepaddle/g' requirements.txt > /tmp/requirements.docker.txt \
    && pip install --upgrade pip \
    && pip install -r /tmp/requirements.docker.txt \
    && pip install -r requirements-dev.txt

COPY . .

RUN mkdir -p /app/media/receipts /app/src/models

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=90s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/health/live || exit 1

CMD ["uvicorn", "src.backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
