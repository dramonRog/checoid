"""In-process extraction metrics and structured job logging."""
from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime
from threading import Lock
from typing import Any, Optional

from loguru import logger


@dataclass
class ExtractionMetrics:
    queued_total: int = 0
    succeeded_total: int = 0
    failed_total: int = 0
    cancelled_total: int = 0
    retried_total: int = 0
    stale_marked_total: int = 0
    recovered_on_startup_total: int = 0
    duration_sum_ms: float = 0.0
    duration_count: int = 0
    last_success_at: Optional[datetime] = None
    last_failure_at: Optional[datetime] = None
    last_failure_error: Optional[str] = None

    def snapshot(self, active_jobs: int) -> dict[str, Any]:
        avg = (
            round(self.duration_sum_ms / self.duration_count, 1)
            if self.duration_count
            else None
        )
        return {
            "active_jobs": active_jobs,
            "queued_total": self.queued_total,
            "succeeded_total": self.succeeded_total,
            "failed_total": self.failed_total,
            "cancelled_total": self.cancelled_total,
            "retried_total": self.retried_total,
            "stale_marked_total": self.stale_marked_total,
            "recovered_on_startup_total": self.recovered_on_startup_total,
            "avg_duration_ms": avg,
            "last_success_at": self.last_success_at,
            "last_failure_at": self.last_failure_at,
            "last_failure_error": self.last_failure_error,
        }


_metrics = ExtractionMetrics()
_lock = Lock()


def get_extraction_metrics_snapshot(active_jobs: int) -> dict[str, Any]:
    with _lock:
        return _metrics.snapshot(active_jobs)


def _log_event(event: str, **fields: Any) -> None:
    """Structured key=value log line for extraction jobs (easy to grep)."""
    parts = [f"event={event}"]
    for key, value in fields.items():
        if value is None:
            continue
        text = str(value).replace("\n", " ").replace('"', "'")
        if " " in text or "=" in text:
            parts.append(f'{key}="{text}"')
        else:
            parts.append(f"{key}={text}")
    logger.info("extraction | " + " ".join(parts))


def record_queued(receipt_id: int, mode: str, *, recovered: bool = False) -> None:
    with _lock:
        _metrics.queued_total += 1
        if recovered:
            _metrics.recovered_on_startup_total += 1
    _log_event(
        "queued",
        receipt_id=receipt_id,
        mode=mode,
        recovered=recovered,
    )


def record_attempt_start(receipt_id: int, mode: str, attempt: int, max_retries: int) -> float:
    """Returns monotonic start time for duration tracking."""
    if attempt > 1:
        with _lock:
            _metrics.retried_total += 1
    _log_event(
        "attempt_start",
        receipt_id=receipt_id,
        mode=mode,
        attempt=attempt,
        max_retries=max_retries,
    )
    return time.monotonic()


def record_attempt_success(
    receipt_id: int,
    mode: str,
    attempt: int,
    started_at: float,
    final_status: str,
) -> None:
    duration_ms = (time.monotonic() - started_at) * 1000.0
    now = datetime.utcnow()
    with _lock:
        _metrics.succeeded_total += 1
        _metrics.duration_sum_ms += duration_ms
        _metrics.duration_count += 1
        _metrics.last_success_at = now
    _log_event(
        "attempt_success",
        receipt_id=receipt_id,
        mode=mode,
        attempt=attempt,
        duration_ms=round(duration_ms, 1),
        status=final_status,
    )


def record_attempt_failure(
    receipt_id: int,
    mode: str,
    attempt: int,
    max_retries: int,
    started_at: float,
    error: str,
) -> None:
    duration_ms = (time.monotonic() - started_at) * 1000.0
    _log_event(
        "attempt_failure",
        receipt_id=receipt_id,
        mode=mode,
        attempt=attempt,
        max_retries=max_retries,
        duration_ms=round(duration_ms, 1),
        error=error[:200],
    )


def record_job_failed(receipt_id: int, mode: str, error: str) -> None:
    now = datetime.utcnow()
    with _lock:
        _metrics.failed_total += 1
        _metrics.last_failure_at = now
        _metrics.last_failure_error = error[:200]
    _log_event("job_failed", receipt_id=receipt_id, mode=mode, error=error[:200])


def record_cancelled(receipt_id: int, mode: str, reason: str) -> None:
    with _lock:
        _metrics.cancelled_total += 1
    _log_event("cancelled", receipt_id=receipt_id, mode=mode, reason=reason)


def record_stale_marked(receipt_id: int, minutes: int) -> None:
    with _lock:
        _metrics.stale_marked_total += 1
    _log_event("stale_marked", receipt_id=receipt_id, stale_minutes=minutes)
