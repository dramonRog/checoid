"""Unit tests for OCR line grouping (no Paddle / YOLO / Ollama).

conftest.py stubs src.ai_pipeline.* so the API suite stays light. This module
loads ocr.py by path so tests hit the real reconstruct_layout_text.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

_OCR_PATH = Path(__file__).resolve().parents[1] / "src" / "ai_pipeline" / "ocr.py"


def _load_real_ocr():
    spec = importlib.util.spec_from_file_location("checoid_ocr_under_test", _OCR_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


_ocr = _load_real_ocr()
reconstruct_layout_text = _ocr.reconstruct_layout_text


def _box(x: float, y: float, w: float = 80, h: float = 20):
    """Axis-aligned 4-point polygon: TL, TR, BR, BL."""
    return [
        [x, y],
        [x + w, y],
        [x + w, y + h],
        [x, y + h],
    ]


def _frag(text: str, x: float, y: float, conf: float = 0.9, w: float = 80, h: float = 20):
    return [_box(x, y, w=w, h=h), (text, conf)]


def test_name_left_price_right_same_line():
    results = [
        _frag("CHLEB", x=10, y=100),
        _frag("4,99", x=220, y=102),
    ]
    assert reconstruct_layout_text(results) == "CHLEB   4,99"


def test_price_before_name_in_input_still_left_to_right():
    results = [
        _frag("4,99", x=220, y=100),
        _frag("CHLEB", x=10, y=101),
    ]
    assert reconstruct_layout_text(results) == "CHLEB   4,99"


def test_two_lines_split_when_y_gap_is_large():
    results = [
        _frag("CHLEB", x=10, y=100),
        _frag("4,99", x=220, y=102),
        _frag("MLEKO", x=10, y=160),
        _frag("3,29", x=220, y=161),
    ]
    assert reconstruct_layout_text(results) == "CHLEB   4,99\nMLEKO   3,29"


def test_bottom_line_first_in_input_still_top_to_bottom():
    results = [
        _frag("MLEKO", x=10, y=160),
        _frag("3,29", x=220, y=161),
        _frag("CHLEB", x=10, y=100),
        _frag("4,99", x=220, y=102),
    ]
    assert reconstruct_layout_text(results) == "CHLEB   4,99\nMLEKO   3,29"


def test_close_y_boxes_join_even_with_three_columns():
    results = [
        _frag("Masło extra", x=10, y=80, w=120),
        _frag("A", x=150, y=82, w=20),
        _frag("1", x=180, y=81, w=20),
        _frag("8,99", x=240, y=83, w=50),
    ]
    assert reconstruct_layout_text(results) == "Masło extra   A   1   8,99"


def test_low_confidence_fragment_is_dropped():
    results = [
        _frag("CHLEB", x=10, y=100, conf=0.95),
        _frag("NOISE", x=120, y=100, conf=0.20),
        _frag("4,99", x=220, y=101, conf=0.90),
    ]
    assert reconstruct_layout_text(results) == "CHLEB   4,99"


def test_custom_min_conf_keeps_borderline_score():
    results = [
        _frag("CHLEB", x=10, y=100, conf=0.45),
        _frag("4,99", x=220, y=100, conf=0.45),
    ]
    assert reconstruct_layout_text(results, min_conf=0.40) == "CHLEB   4,99"
    assert reconstruct_layout_text(results, min_conf=0.50) == ""


def test_empty_and_blank_text_ignored():
    results = [
        _frag("CHLEB", x=10, y=100),
        _frag("   ", x=120, y=100),
        _frag("4,99", x=220, y=101),
    ]
    assert reconstruct_layout_text(results) == "CHLEB   4,99"


def test_malformed_items_skipped():
    results = [
        None,
        [],
        [_box(0, 0)],
        _frag("CHLEB", x=10, y=100),
        _frag("4,99", x=220, y=101),
    ]
    assert reconstruct_layout_text(results) == "CHLEB   4,99"


def test_empty_results_return_empty_string():
    assert reconstruct_layout_text([]) == ""
    assert reconstruct_layout_text([None, []]) == ""
