"""Brand catalog: JSON seed + in-process overlay. Seed file is never written."""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

BRANDS_JSON_PATH = Path(__file__).resolve().parent.parent / "data" / "brands.json"
_catalog: Optional[List[Dict[str, Any]]] = None


def _normalize(value: str) -> str:
    text = str(value).strip().lower()
    for src, dst in (
        ("ą", "a"), ("ć", "c"), ("ę", "e"), ("ł", "l"), ("ń", "n"),
        ("ó", "o"), ("ś", "s"), ("ź", "z"), ("ż", "z"),
    ):
        text = text.replace(src, dst)
    return " ".join(text.split())


def clean_nip(nip: Optional[str]) -> str:
    if not nip:
        return ""
    return "".join(ch for ch in str(nip) if ch.isdigit())


def load_brand_catalog() -> List[Dict[str, Any]]:
    """Live in-memory catalog, initialized once from the seed file."""
    global _catalog
    if _catalog is None:
        with BRANDS_JSON_PATH.open(encoding="utf-8") as f:
            payload = json.load(f)
        _catalog = list(payload.get("brands") or [])
    return _catalog


def resolve_brand_name(
    *,
    nip: Optional[str] = None,
    legal_name: Optional[str] = None,
    shop_hint: Optional[str] = None,
) -> Optional[str]:
    """
    Resolve a friendly shop brand.
    Order: NIP → exact shop hint → legal/shop soft match on name+aliases.
    """
    catalog = load_brand_catalog()
    clean = clean_nip(nip)

    if len(clean) == 10:
        for entry in catalog:
            if clean in [clean_nip(n) for n in (entry.get("nips") or [])]:
                return entry["name"]

    if shop_hint and shop_hint.strip():
        hint_norm = _normalize(shop_hint)
        for entry in catalog:
            keys = [entry.get("name", ""), *(entry.get("legal_aliases") or [])]
            for key in keys:
                if key and _normalize(key) == hint_norm:
                    return entry["name"]

    haystacks: List[str] = []
    if legal_name:
        haystacks.append(_normalize(legal_name))
    if shop_hint:
        haystacks.append(_normalize(shop_hint))

    best: Optional[Tuple[int, str]] = None
    for entry in catalog:
        name = entry.get("name") or ""
        keys = [name, *(entry.get("legal_aliases") or [])]
        for key in keys:
            if not key:
                continue
            key_norm = _normalize(key)
            if len(key_norm) < 3:
                continue
            for hay in haystacks:
                if key_norm in hay or hay in key_norm:
                    score = len(key_norm)
                    if best is None or score > best[0]:
                        best = (score, name)
    return best[1] if best else None


def find_nips_for_shop_hint(shop_hint: Optional[str]) -> List[str]:
    """Return NIP list for a shop/brand hint (0, 1, or many)."""
    brand = resolve_brand_name(shop_hint=shop_hint)
    if not brand:
        return []
    for entry in load_brand_catalog():
        if entry.get("name") == brand:
            return [clean_nip(n) for n in (entry.get("nips") or []) if clean_nip(n)]
    return []


def ensure_brand_in_catalog(
    *,
    brand_name: str,
    nip: Optional[str] = None,
    legal_alias: Optional[str] = None,
) -> None:
    """
    Remember a brand mapping in process memory (not on disk).
    Company rows in PostgreSQL remain the durable store.
    """
    brand_name = (brand_name or "").strip()
    if not brand_name:
        return

    clean = clean_nip(nip)
    legal_alias = (legal_alias or "").strip() or None
    brands = load_brand_catalog()

    target = None
    for entry in brands:
        if _normalize(entry.get("name", "")) == _normalize(brand_name):
            target = entry
            break
        aliases = entry.get("legal_aliases") or []
        if any(_normalize(a) == _normalize(brand_name) for a in aliases if a):
            target = entry
            break

    if target is None:
        brands.append(
            {
                "name": brand_name,
                "nips": [clean] if len(clean) == 10 else [],
                "legal_aliases": [legal_alias] if legal_alias else [brand_name],
            }
        )
        logger.info("Cached in-memory brand=%s nip=%s", brand_name, clean or "-")
        return

    nips = list(target.get("nips") or [])
    if len(clean) == 10 and clean not in [clean_nip(n) for n in nips]:
        nips.append(clean)
        target["nips"] = nips
    aliases = list(target.get("legal_aliases") or [])
    if legal_alias and not any(_normalize(a) == _normalize(legal_alias) for a in aliases):
        aliases.append(legal_alias)
        target["legal_aliases"] = aliases
