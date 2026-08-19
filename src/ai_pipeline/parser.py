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

2) ADDRESS / LEGAL BLOCK
   - Lines with ul., al., pl., Sklep <id>, city + postal code (NN-NNN), company S.A. / Sp. z o.o.
   - "adres" = the store location where the purchase happened (street + number, optional city/postal),
     as printed on the receipt. Example: "ul. Żniwna 5, 62-025 Kostrzyn".
     Do not invent; if unreadable → null. This is NOT the corporation HQ address.
   - Legal company name lines may help disambiguate "sklep"; do not put legal name into "adres".

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
   - OCR may glue words (MasłoExtrOsełk500g) — keep readable product text; do not invent nicer spelling.
   - STRIP from "nazwa": internal store codes / PLU / SKU / article ids glued after the name
     (e.g. "CHLEB Zk0C 552187C" → "CHLEB"; "Mleko 3.2% A12 998877" → "Mleko 3.2%").
     Keep size/weight tokens (500g, 1.5L, 3.2%). Never put bare code tokens into "nazwa".
   - "ilosc" = printed quantity (1, 1.000, 0.536). Informational only. Default 1 if clearly one item but qty missing.
   - "cena" = amount PAID for this line after discount (Wartość after rabat), NOT unit price.
     Example: 2 × 4.99 with no rabat → ilosc=2, cena=9.98 (never 4.99).
     Never output unit price. Never multiply cena by ilosc yourself in later math — cena already is the line total.
   - Polish decimals use comma: 23,99 → 23.99 ; 1,000 may mean thousand-separator OR 1.000 qty — prefer qty forms like 1.000 / 1,000 next to "x".
   - Lines can be services too (bilet, parking, paliwo, film/kino) — still emit as pozycje.

6) RABAT / DISCOUNT (critical)
   - After a product you may see:
        Rabat   -12,00
        11,99
     Meaning: discount applied; final paid for THAT product is the small amount after the rabat (11.99), NOT 23.99.
   - "cena" MUST be that paid line total AFTER discount (11.99), not the pre-rabat unit or Wartość.
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
- MONEY RULE: suma_calkowita MUST equal the sum of pozycje[].cena (NOT suma of cena*ilosc).
- If a value is unreadable → null (or omit bad item rather than guess).
- "pozycje[].kategoria" MUST be exactly one name from this catalog:
  {category_list}
- Mapping hints: butter/milk → Nabiał; dental/soap → Chemia i higiena; petrol/fuel → Paliwo;
  cinema/movie ticket → Rozrywka i kultura; bus/train ticket → Bilety i transport; if unsure → Inne.
- Prefer a catalog name. Only invent a new short Polish category label if nothing fits.
- "pozycje[].gwarancja" = true if the product is a durable good typically covered by
  EU/Polish 2-year consumer guarantee (elektronika, RTV/AGD, odzież, obuwie, narzędzia,
  meble, sport, zabawki trwałe, etc.). false for food, drinks, consumables, cosmetics,
  chemia, paliwo, bilety, usługi jednorazowe, kwiaty cięte, etc. If unsure → false.

═══════════════════════════════════════
OUTPUT
═══════════════════════════════════════
Return ONLY JSON with keys:
sklep, nip, adres, data, suma_calkowita, pozycje[{{nazwa, ilosc, cena, kategoria, gwarancja}}]"""


def get_pipeline_schema() -> Dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "sklep": {"type": ["string", "null"]},
            "nip": {"type": ["string", "null"]},
            "adres": {"type": ["string", "null"]},
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
                        "kategoria": {"type": "string"},
                        "gwarancja": {"type": "boolean"},
                    },
                    "required": ["nazwa", "ilosc", "cena", "kategoria", "gwarancja"],
                }
            }
        },
        "required": ["sklep", "nip", "adres", "data", "suma_calkowita", "pozycje"]
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


_SIZE_OR_PCT_TOKEN = re.compile(
    r"^("
    r"\d+([.,]\d+)?\s*(g|kg|mg|ml|l|cl|szt)"  # 500g, 1.5L — unit required
    r"|\d+[.,]\d+%?"  # 3.2 or 3.2%
    r"|\d+%"  # 3%
    r")$",
    re.IGNORECASE,
)
# Trailing PLU / SKU / article ids: 552187, Zk0C, 187C, CN27102011, A12B99
_PLU_SKU_TOKEN = re.compile(
    r"^("
    r"\d{4,}"
    r"|[A-Za-z]{1,4}\d+[A-Za-z0-9]*"
    r"|\d+[A-Za-z]{1,4}"
    r")$",
    re.IGNORECASE,
)


def _clean_product_name(name: str) -> str:
    """
    Drop trailing store identity codes from product names.
    Example: "CHLEB Zk0C 552187C" -> "CHLEB"
    Keeps size tokens like 500g / 1.5L / 3.2%.
    """
    raw = " ".join(str(name).split())
    if not raw:
        return raw

    tokens = raw.split(" ")
    while len(tokens) > 1:
        last = tokens[-1]
        if _SIZE_OR_PCT_TOKEN.match(last):
            break
        if _PLU_SKU_TOKEN.match(last):
            tokens.pop()
            continue
        break

    cleaned = " ".join(tokens).strip(" -–,;")
    return cleaned or raw


def validate_and_clean_payload(data: Dict[str, Any], raw_ocr: str) -> Dict[str, Any]:
    if not isinstance(data, dict):
        return {
            "status": "FAILED_SCHEMA",
            "error": "LLM returned a non-object payload",
        }

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

    adres = data.get("adres")
    if adres is not None:
        text = str(adres).strip()
        if not text or text.lower() in {"null", "none", "n/a", "unknown"}:
            data["adres"] = None
        else:
            # Company.address column is String(255)
            data["adres"] = text[:255]
    else:
        data["adres"] = None

    # cena / price = paid line total after discount (not unit price).
    # Verification and analytics SUM(cena); never cena * ilosc.
    calc_sum = 0.0
    cleaned_items = []
    for item in data.get("pozycje", []) or []:
        if not isinstance(item, dict):
            continue
        name = _clean_product_name(str(item.get("nazwa") or "").strip())
        if not name or name.lower() == "rabat":
            continue
        item["nazwa"] = name

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
        item["gwarancja"] = bool(item.get("gwarancja"))
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
        return {
            "status": "FAILED_PARSING",
            "error": f"LLM parse failed: {e}",
            "pozycje": [],
        }


def categorize_product_names(
    product_names: list[str],
    model_name: str = "qwen2.5:7b",
) -> Dict[str, Dict[str, Any]]:
    """
    Map product names -> {kategoria, gwarancja} via a small LLM call.
    Used for manual receipt create (category and/or warranty inference).
    """
    names = [str(n).strip() for n in product_names if str(n).strip()]
    if not names:
        return {}

    category_list = category_names_for_prompt()
    schema = {
        "type": "object",
        "properties": {
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "nazwa": {"type": "string"},
                        "kategoria": {"type": "string"},
                        "gwarancja": {"type": "boolean"},
                    },
                    "required": ["nazwa", "kategoria", "gwarancja"],
                },
            }
        },
        "required": ["items"],
    }
    system = (
        "You categorize Polish receipt product names and decide EU/Polish consumer guarantee.\n"
        f"Each kategoria MUST be exactly one of: {category_list}\n"
        "If unsure use Inne.\n"
        "gwarancja=true for durable goods (electronics, appliances, clothes, shoes, tools, "
        "furniture, sports gear). gwarancja=false for food, drinks, consumables, cosmetics, "
        "cleaning products, fuel, tickets, one-off services. If unsure → false.\n"
        "Return only JSON."
    )
    user = "Categorize these products:\n" + "\n".join(f"- {n}" for n in names)

    def _row(kat: str, gwarancja: bool) -> Dict[str, Any]:
        return {"kategoria": kat, "gwarancja": gwarancja}

    try:
        response = ollama.chat(
            model=model_name,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            format=schema,
            options={"temperature": 0.0, "seed": 42, "num_ctx": 2048},
        )
        raw_json = response["message"]["content"]
        clean_str = re.sub(r"^```json\s*|\s*```$", "", raw_json.strip(), flags=re.IGNORECASE)
        payload = json.loads(clean_str)
        mapping: Dict[str, Dict[str, Any]] = {}
        rows = payload.get("items") or []
        for row in rows:
            nazwa = str(row.get("nazwa") or "").strip()
            kat = str(row.get("kategoria") or "").strip() or "Inne"
            if nazwa:
                mapping[nazwa] = _row(kat, bool(row.get("gwarancja")))
        for i, name in enumerate(names):
            if name in mapping:
                continue
            if i < len(rows):
                mapping[name] = _row(
                    str(rows[i].get("kategoria") or "Inne").strip() or "Inne",
                    bool(rows[i].get("gwarancja")),
                )
            else:
                mapping[name] = _row("Inne", False)
        return mapping
    except Exception:
        return {name: _row("Inne", False) for name in names}
