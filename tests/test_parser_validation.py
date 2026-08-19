"""Unit tests for parser.validate_and_clean_payload (no YOLO / Paddle / Ollama).

conftest.py stubs src.ai_pipeline.* so the API suite stays light. This module
loads parser.py by path so tests hit the real validation firewall.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest.mock import MagicMock

_PARSER_PATH = Path(__file__).resolve().parents[1] / "src" / "ai_pipeline" / "parser.py"

# Biedronka — valid modulo-11 NIP used across the project.
VALID_NIP = "7791011327"


def _load_real_parser():
    sys.modules.setdefault("ollama", MagicMock())
    spec = importlib.util.spec_from_file_location("checoid_parser_under_test", _PARSER_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


_parser = _load_real_parser()
validate_and_clean_payload = _parser.validate_and_clean_payload


def _item(nazwa="Chleb", ilosc=1, cena=4.99, kategoria="Pieczywo", gwarancja=False):
    return {
        "nazwa": nazwa,
        "ilosc": ilosc,
        "cena": cena,
        "kategoria": kategoria,
        "gwarancja": gwarancja,
    }


def _payload(**overrides):
    data = {
        "sklep": "Biedronka",
        "nip": VALID_NIP,
        "adres": "ul. Testowa 1",
        "data": "2026-01-15",
        "suma_calkowita": 4.99,
        "pozycje": [_item()],
    }
    data.update(overrides)
    return data


def test_failed_schema_on_non_dict():
    result = validate_and_clean_payload(["not", "an", "object"], "")
    assert result["status"] == "FAILED_SCHEMA"
    assert "error" in result


def test_valid_nip_is_kept():
    result = validate_and_clean_payload(_payload(), "")
    assert result["nip"] == VALID_NIP


def test_nip_digits_and_dashes_normalized():
    result = validate_and_clean_payload(_payload(nip="779-10-11-327"), "")
    assert result["nip"] == VALID_NIP


def test_invalid_nip_checksum_cleared():
    result = validate_and_clean_payload(_payload(nip="7791011328"), "")
    assert result["nip"] is None


def test_nip_recovered_from_ocr_when_llm_missing():
    ocr = "Biedronka\nNIP: 779-10-11-327\nSuma PLN 4,99"
    result = validate_and_clean_payload(_payload(nip=None), ocr)
    assert result["nip"] == VALID_NIP


def test_nip_recovered_from_ocr_when_letter_n_dropped():
    ocr = "Sklep\nIP 7791011327\nSuma PLN 4,99"
    result = validate_and_clean_payload(_payload(nip=""), ocr)
    assert result["nip"] == VALID_NIP


def test_short_nip_without_ocr_hit_becomes_none():
    result = validate_and_clean_payload(_payload(nip="12345"), "no tax id here")
    assert result["nip"] is None


def test_adres_nullish_strings_become_none():
    for value in ("", "null", "None", "n/a", "unknown"):
        result = validate_and_clean_payload(_payload(adres=value), "")
        assert result["adres"] is None, value


def test_adres_truncated_to_255():
    result = validate_and_clean_payload(_payload(adres="A" * 300), "")
    assert result["adres"] == "A" * 255


def test_rabat_pozycja_is_dropped():
    result = validate_and_clean_payload(
        _payload(
            suma_calkowita=11.99,
            pozycje=[
                _item(nazwa="Masło", cena=11.99),
                _item(nazwa="Rabat", cena=-12.00),
            ],
        ),
        "",
    )
    names = [p["nazwa"] for p in result["pozycje"]]
    assert names == ["Masło"]
    assert result["status"] == "VERIFIED_COMPLETED"


def test_plu_sku_stripped_from_product_name():
    result = validate_and_clean_payload(
        _payload(pozycje=[_item(nazwa="CHLEB Zk0C 552187C")]),
        "",
    )
    assert result["pozycje"][0]["nazwa"] == "CHLEB"


def test_size_and_percent_tokens_kept_in_name():
    result = validate_and_clean_payload(
        _payload(
            suma_calkowita=9.98,
            pozycje=[
                _item(nazwa="Mleko 3.2% 1.5L", cena=4.99),
                _item(nazwa="MasłoExtrOsełk500g", cena=4.99),
            ],
        ),
        "",
    )
    names = [p["nazwa"] for p in result["pozycje"]]
    assert names == ["Mleko 3.2% 1.5L", "MasłoExtrOsełk500g"]


def test_polish_decimal_price_and_qty_parsed():
    result = validate_and_clean_payload(
        _payload(
            suma_calkowita="12,50",
            pozycje=[_item(ilosc="1,000", cena="12,50")],
        ),
        "",
    )
    item = result["pozycje"][0]
    assert item["ilosc"] == 1.0
    assert item["cena"] == 12.50
    assert result["suma_calkowita"] == 12.50
    assert result["status"] == "VERIFIED_COMPLETED"


def test_missing_qty_defaults_to_one():
    result = validate_and_clean_payload(
        _payload(pozycje=[_item(ilosc=None)]),
        "",
    )
    assert result["pozycje"][0]["ilosc"] == 1.0


def test_missing_kategoria_defaults_to_inne():
    result = validate_and_clean_payload(
        _payload(pozycje=[_item(kategoria="")]),
        "",
    )
    assert result["pozycje"][0]["kategoria"] == "Inne"


def test_gwarancja_cast_to_bool():
    result = validate_and_clean_payload(
        _payload(pozycje=[_item(gwarancja=1)]),
        "",
    )
    assert result["pozycje"][0]["gwarancja"] is True


def test_non_dict_pozycje_are_skipped():
    result = validate_and_clean_payload(
        _payload(pozycje=["bad", _item(), None]),
        "",
    )
    assert len(result["pozycje"]) == 1
    assert result["pozycje"][0]["nazwa"] == "Chleb"


def test_verified_when_line_sum_matches_total():
    result = validate_and_clean_payload(
        _payload(
            suma_calkowita=60.43,
            pozycje=[
                _item(nazwa="Masło", cena=4.96),
                _item(nazwa="Mleko", cena=55.47),
            ],
        ),
        "",
    )
    assert result["status"] == "VERIFIED_COMPLETED"
    assert result["suma_calkowita"] == 60.43


def test_needs_review_when_line_sum_mismatches_total():
    result = validate_and_clean_payload(
        _payload(
            suma_calkowita=99.00,
            pozycje=[_item(cena=4.99)],
        ),
        "",
    )
    assert result["status"] == "NEEDS_HUMAN_REVIEW"
    assert result["suma_calkowita"] == 99.00


def test_small_rounding_gap_still_verified():
    # tolerance = max(0.05, 1% of total) → 0.05 for a 4.99 ticket
    result = validate_and_clean_payload(
        _payload(suma_calkowita=4.99, pozycje=[_item(cena=4.95)]),
        "",
    )
    assert result["status"] == "VERIFIED_COMPLETED"


def test_one_percent_tolerance_on_larger_total():
    result = validate_and_clean_payload(
        _payload(suma_calkowita=100.00, pozycje=[_item(cena=99.00)]),
        "",
    )
    assert result["status"] == "VERIFIED_COMPLETED"

    result = validate_and_clean_payload(
        _payload(suma_calkowita=100.00, pozycje=[_item(cena=98.99)]),
        "",
    )
    assert result["status"] == "NEEDS_HUMAN_REVIEW"


def test_ocr_suma_pln_used_when_llm_total_missing():
    ocr = "Chleb 4,99\nSuma PLN 4,99"
    result = validate_and_clean_payload(
        _payload(suma_calkowita=None, pozycje=[_item(cena=4.99)]),
        ocr,
    )
    assert result["suma_calkowita"] == 4.99
    assert result["status"] == "VERIFIED_COMPLETED"


def test_ocr_suma_preferred_when_closer_to_line_sum():
    ocr = "Masło 4,96\nMleko 55,47\nSuma PLN 60,43"
    result = validate_and_clean_payload(
        _payload(
            suma_calkowita=99.00,
            pozycje=[
                _item(nazwa="Masło", cena=4.96),
                _item(nazwa="Mleko", cena=55.47),
            ],
        ),
        ocr,
    )
    assert result["suma_calkowita"] == 60.43
    assert result["status"] == "VERIFIED_COMPLETED"


def test_needs_review_when_no_total_anywhere():
    result = validate_and_clean_payload(
        _payload(suma_calkowita=None, pozycje=[_item(cena=4.99)]),
        "no footer totals on this OCR",
    )
    assert result["suma_calkowita"] is None
    assert result["status"] == "NEEDS_HUMAN_REVIEW"
