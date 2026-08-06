import re
import json
import ollama
from typing import Dict, Any, Optional

from src.backend.services.categories import category_names_for_prompt


def get_receipt_system_prompt() -> str:
    category_list = category_names_for_prompt()
    return f"""You are a deterministic parser for Polish fiscal / store receipts (paragon).
Convert noisy OCR text into ONE JSON object matching the schema. No markdown. No commentary.

═══════════════════════════════════════
RECEIPT ZONES (read top → bottom)
═══════════════════════════════════════
1) HEADER (shop identity)
   - Brand / chain name near the top (e.g. Biedronka, Lidl, Żabka, Auchan).
   - Often followed by slogan lines — ignore slogans for "sklep".
   - "sklep" = trade/brand name only, not the full company legal name unless brand is missing.

2) ADDRESS / LEGAL BLOCK (context only — do NOT invent extra JSON fields)
   - Lines with ul., al., pl., Sklep <id>, city + postal code, company S.A. / Sp. z o.o.
   - Use only to disambiguate the seller; do not put address into output fields.

3) NIP
   - Look for "NIP" then 10 digits, possibly grouped with spaces/dashes (779-10-11-327).
   - Output "nip" as exactly 10 digits, no separators.
   - If missing/unreadable → null.

4) DATE / TIME
   - Prefer purchase date near header or near the end (DD.MM.YYYY or DD-MM-YYYY).
   - Output "data" as YYYY-MM-DD.
   - Ignore time-of-day. If only a garbled date → null.

5) ITEM TABLE (products / services)
   - Header-like words: Nazwa, PTU, Ilość, Cena, Wartość.
   - A product line usually has: name, VAT letter (A/B/C/D/E), quantity, unit price, line value.
   - OCR may glue words (MasłoExtrOsełk500g) — keep the OCR name, do not invent nicer spelling.
   - "ilosc" = quantity number (1, 1.000, 0.536). Default 1 if clearly one item but qty missing.
   - Polish decimals use comma: 23,99 → 23.99 ; 1,000 may mean thousand-separator OR 1.000 qty — prefer qty forms like 1.000 / 1,000 next to "x".
   - Lines can be services too (bilet, parking, paliwo, film/kino) — still emit as pozycje.

6) RABAT / DISCOUNT (critical)
   - After a product you may see:
        Rabat   -12,00
        11,99
     Meaning: discount applied; final paid for THAT product is the small amount after the rabat (11.99), NOT 23.99.
   - "cena" MUST be the final amount paid for the line AFTER discount.
   - Do NOT create a separate pozycja named "Rabat".
   - Do NOT use "Wartość" before discount if a post-rabat amount is present.
   - If several identical products each have their own rabat block, emit several pozycje (one per block).

7) FOOTER / TAX / PAYMENT (not products)
   IGNORE as pozycje: NIEFISKALNY, Sprzedaż opodatkowana, PTU A/B/C %, Suma PTU,
   Kasa, Kasjer, Numer transakcji, Karta płatnicza, Strona X z Y, long barcode-like digit strings.
   - "suma_calkowita" = final amount paid. Prefer "Suma PLN", else payment amount (Karta płatnicza / Gotówka / Zapłacono).
   - Never use Suma PTU or a single VAT base as the total.

═══════════════════════════════════════
FIELD RULES
═══════════════════════════════════════
- NUMERICAL FIDELITY: copy amounts from OCR; never invent prices or totals.
- If a value is unreadable → null (or omit bad item rather than guess).
- "pozycje[].kategoria" MUST be exactly one name from this catalog:
  {category_list}
- Mapping hints: butter/milk → Nabiał; dental/soap → Chemia i higiena; petrol/fuel → Paliwo;
  cinema/movie ticket → Rozrywka i kultura; bus/train ticket → Bilety i transport; if unsure → Inne.
- Prefer a catalog name. Only invent a new short Polish category label if nothing fits.

═══════════════════════════════════════
OUTPUT
═══════════════════════════════════════
Return ONLY JSON with keys:
sklep, nip, data, suma_calkowita, pozycje[{{nazwa, ilosc, cena, kategoria}}]"""


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
                        "nazwa": {"type": "string"},
                        "ilosc": {"type": "number"},
                        "cena": {"type": ["number", "null"]},
                        "kategoria": {"type": "string"}
                    },
                    "required": ["nazwa", "ilosc", "cena", "kategoria"]
                }
            }
        },
        "required": ["sklep", "nip", "data", "suma_calkowita", "pozycje"]
    }


def _parse_pl_number(value: Any) -> Optional[float]:
    """Parse float from LLM/OCR Polish or English number text."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(" ", "").replace("\u00a0", "")
    if not text or text.lower() in {"null", "none"}:
        return None
    if re.match(r"^-?\d{1,3}(\.\d{3})+(,\d+)?$", text):
        text = text.replace(".", "").replace(",", ".")
    elif "," in text and "." in text:
        if text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    elif "," in text:
        text = text.replace(",", ".")
    try:
        return float(text)
    except ValueError:
        return None


def _extract_suma_pln_from_ocr(raw_ocr: str) -> Optional[float]:
    patterns = [
        r"Suma\s*PLN\s*[:=]?\s*(-?\d{1,3}(?:[\s.]\d{3})*,\d{2}|-?\d+,\d{2}|-?\d+\.\d{2})",
        r"(?:Zapłacono|Do\s*zapłaty|RAZEM|SUMA)\s*[:=]?\s*(-?\d{1,3}(?:[\s.]\d{3})*,\d{2}|-?\d+,\d{2}|-?\d+\.\d{2})",
    ]
    for pattern in patterns:
        match = re.search(pattern, raw_ocr, re.IGNORECASE)
        if match:
            return _parse_pl_number(match.group(1))
    return None


def validate_and_clean_payload(data: Dict[str, Any], raw_ocr: str) -> Dict[str, Any]:
    if not isinstance(data, dict):
        return {"status": "FAILED_SCHEMA"}

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

    # Normalize line prices and sum
    calc_sum = 0.0
    cleaned_items = []
    for item in data.get("pozycje", []) or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("nazwa") or "").strip()
        if not name or name.lower() == "rabat":
            continue

        qty = _parse_pl_number(item.get("ilosc"))
        price = _parse_pl_number(item.get("cena"))
        item["ilosc"] = qty if qty is not None else 1.0
        if price is not None:
            item["cena"] = round(price, 2)
            calc_sum += item["cena"]
        else:
            item["cena"] = None

        if not item.get("kategoria"):
            item["kategoria"] = "Inne"
        cleaned_items.append(item)

    data["pozycje"] = cleaned_items

    total = _parse_pl_number(data.get("suma_calkowita"))
    ocr_total = _extract_suma_pln_from_ocr(raw_ocr)

    # Prefer OCR Suma PLN when LLM total is missing or clearly inconsistent with line sum
    if ocr_total is not None:
        if total is None or (calc_sum > 0 and abs(total - calc_sum) > abs(ocr_total - calc_sum) + 0.01):
            total = ocr_total

    if total is not None:
        data["suma_calkowita"] = round(total, 2)
        if abs(data["suma_calkowita"] - calc_sum) <= max(0.05, 0.01 * abs(data["suma_calkowita"])):
            data["status"] = "VERIFIED_COMPLETED"
        else:
            data["status"] = "NEEDS_HUMAN_REVIEW"
    else:
        data["suma_calkowita"] = None
        data["status"] = "NEEDS_HUMAN_REVIEW"

    return data


def parse_with_llm(ocr_text: str, model_name: str = "qwen2.5:7b") -> Dict[str, Any]:
    try:
        response = ollama.chat(
            model=model_name,
            messages=[
                {"role": "system", "content": get_receipt_system_prompt()},
                {"role": "user", "content": f"Parse this Polish receipt OCR into the JSON schema.\n\nOCR:\n{ocr_text}"}
            ],
            format=get_pipeline_schema(),
            options={
                "temperature": 0.0,
                "seed": 42,
                "num_ctx": 4096,
            }
        )
        raw_json = response['message']['content']
        clean_str = re.sub(r"^```json\s*|\s*```$", "", raw_json.strip(), flags=re.IGNORECASE)
        return validate_and_clean_payload(json.loads(clean_str), ocr_text)
    except Exception as e:
        return {"status": "FAILED_PARSING", "error": str(e)}
