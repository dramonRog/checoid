"""Unit tests for extraction failure helpers and observability metrics."""
from src.backend.services.extraction_observability import (
    get_extraction_metrics_snapshot,
    record_attempt_failure,
    record_attempt_start,
    record_attempt_success,
    record_queued,
)
from src.backend.services.receipt_extraction import is_pipeline_failure, is_stale_processing
from src.backend.db.models import Receipt
from datetime import datetime, timedelta


def test_pipeline_failure_failed_statuses():
    assert is_pipeline_failure({"status": "FAILED_PARSING", "error": "boom"}) is True
    assert is_pipeline_failure({"status": "FAILED_SCHEMA", "error": "bad"}) is True
    assert is_pipeline_failure({"error": "YOLO failed"}) is True


def test_pipeline_success_statuses():
    assert is_pipeline_failure({"status": "VERIFIED_COMPLETED", "pozycje": []}) is False
    assert is_pipeline_failure({"status": "NEEDS_HUMAN_REVIEW", "pozycje": []}) is False
    assert is_pipeline_failure({"status": "COMPLETED"}) is False


def test_stale_processing_detection():
    receipt = Receipt(
        id=1,
        user_id=1,
        status="PROCESSING",
        created_at=datetime.utcnow() - timedelta(hours=3),
        extraction_started_at=datetime.utcnow() - timedelta(hours=3),
        has_warranty_items=False,
        extraction_attempts=1,
    )
    assert is_stale_processing(receipt, now=datetime.utcnow()) is True

    fresh = Receipt(
        id=2,
        user_id=1,
        status="PROCESSING",
        created_at=datetime.utcnow(),
        extraction_started_at=datetime.utcnow(),
        has_warranty_items=False,
        extraction_attempts=0,
    )
    assert is_stale_processing(fresh, now=datetime.utcnow()) is False


def test_observability_metrics_counters():
    record_queued(101, "image")
    started = record_attempt_start(101, "image", 1, 3)
    record_attempt_success(101, "image", 1, started, "VERIFIED_COMPLETED")
    record_attempt_failure(102, "pdf", 1, 3, started, "timeout")

    snap = get_extraction_metrics_snapshot(active_jobs=1)
    assert snap["active_jobs"] == 1
    assert snap["queued_total"] >= 1
    assert snap["succeeded_total"] >= 1
    assert snap["avg_duration_ms"] is not None
