"""Unit tests for warranty date helpers (no DB / AI)."""
from datetime import date

from src.backend.services.warranty import (
    add_two_years_eu_standard,
    apply_warranty,
    matches_warranty_filter,
    resolve_warranty_end_date,
    warranty_lifecycle_status,
)


def test_add_two_years_normal_date():
    assert add_two_years_eu_standard(date(2024, 3, 15)) == date(2026, 3, 15)


def test_add_two_years_leap_day():
    assert add_two_years_eu_standard(date(2024, 2, 29)) == date(2026, 2, 28)


def test_resolve_warranty_end_prefers_explicit():
    assert resolve_warranty_end_date(date(2024, 1, 1), date(2030, 1, 1)) == date(2030, 1, 1)


def test_resolve_warranty_end_eu_fallback():
    assert resolve_warranty_end_date(date(2024, 1, 1), None) == date(2026, 1, 1)


def test_apply_warranty_clears_when_off():
    assert apply_warranty(False, date(2024, 1, 1), date(2026, 1, 1)) == (False, None)


def test_apply_warranty_sets_eu_when_on():
    assert apply_warranty(True, date(2024, 1, 1), None) == (True, date(2026, 1, 1))


def test_lifecycle_and_filters():
    assert warranty_lifecycle_status(60, 30) == "active"
    assert warranty_lifecycle_status(10, 30) == "expiring"
    assert warranty_lifecycle_status(-1, 30) == "expired"

    assert matches_warranty_filter(10, "active", 30) is True
    assert matches_warranty_filter(10, "expiring", 30) is True
    assert matches_warranty_filter(-1, "expired", 30) is True
    assert matches_warranty_filter(-1, "active", 30) is False
    assert matches_warranty_filter(10, "all", 30) is True
