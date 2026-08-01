import re
import json
import ollama
from typing import Dict, Any


def get_receipt_system_prompt() -> str:
    return """You are a highly specialized, deterministic data extraction parser for Polish fiscal receipts.
Your ONLY task is to convert noisy OCR text into a single, valid JSON object.

RULES:
1. NUMERICAL FIDELITY: NEVER invent, calculate, round, or alter any numbers.
2. UNCERTAINTY = null: If a field is unreadable, set it to null.
3. OUTPUT: ONLY raw JSON. No markdown blocks.

FIELDS TO EXTRACT:
- "sklep": Trade/brand name of the seller.
- "nip": 10-digit Tax Identification Number.
- "data": Date in YYYY-MM-DD.
- "suma_calkowita": Final total amount paid (float).
- "pozycje": Array of purchased items containing "nazwa" (string), "ilosc" (number), "cena" (final price paid, float), and "kategoria" (string)."""


def get_pipeline_schema() -> Dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "sklep": {"type": ["string", "null"]},
            "nip": {"type": ["string", "null"]},
            "data": {"type": ["string", "null"]},
            "suma_calkowita": {"type": ["number", "null"]},
            "pozycje": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "nazwa": {"type": "string"}, "ilosc": {"type": "number"},
                        "cena": {"type": ["number", "null"]}, "kategoria": {"type": "string"}
                    },
                    "required": ["nazwa", "ilosc", "cena", "kategoria"]
                }
            }
        },
        "required": ["sklep", "nip", "data", "suma_calkowita", "pozycje"]
    }


def validate_and_clean_payload(data: Dict[str, Any], raw_ocr: str) -> Dict[str, Any]:
    if not isinstance(data, dict): return {"status": "FAILED_SCHEMA"}

    # Modulo-11 NIP Validation
    nip = re.sub(r"\D", "", str(data.get("nip", "")))
    if not nip or len(nip) != 10:
        match = re.search(r"N?IP[:\s]*([0-9\-\s]{10,14})", raw_ocr, re.IGNORECASE)
        nip = re.sub(r"\D", "", match.group(1)) if match else ""

    if len(nip) == 10:
        weights = [6, 5, 7, 2, 3, 4, 5, 6, 7]
        chk = sum(int(nip[i]) * weights[i] for i in range(9)) % 11
        data["nip"] = nip if (chk == 10 or chk == int(nip[9])) else None
    else:
        data["nip"] = None

    # Arithmetic Consistency Validation
    calc_sum = 0.0
    for item in data.get("pozycje", []):
        try:
            item["cena"] = round(float(item["cena"]), 2)
            calc_sum += item["cena"]
        except (ValueError, TypeError):
            continue

    total = data.get("suma_calkowita")
    if total is not None:
        try:
            total_val = float(total)
            data["suma_calkowita"] = round(total_val, 2)
            if abs(total_val - calc_sum) <= max(0.05, 0.01 * total_val):
                data["status"] = "VERIFIED_COMPLETED"
            else:
                data["status"] = "NEEDS_HUMAN_REVIEW"
        except ValueError:
            data["status"] = "NEEDS_HUMAN_REVIEW"
    else:
        data["status"] = "NEEDS_HUMAN_REVIEW"

    return data


def parse_with_llm(ocr_text: str, model_name: str = "qwen2.5:7b") -> Dict[str, Any]:
    try:
        response = ollama.chat(
            model=model_name,
            messages=[
                {"role": "system", "content": get_receipt_system_prompt()},
                {"role": "user", "content": f"Parse this receipt OCR:\n\n{ocr_text}"}
            ],
            format=get_pipeline_schema(),
            options={"temperature": 0.0, "seed": 42}
        )
        raw_json = response['message']['content']
        clean_str = re.sub(r"^```json\s*|\s*```$", "", raw_json.strip(), flags=re.IGNORECASE)
        return validate_and_clean_payload(json.loads(clean_str), ocr_text)
    except Exception as e:
        return {"status": "FAILED_PARSING", "error": str(e)}