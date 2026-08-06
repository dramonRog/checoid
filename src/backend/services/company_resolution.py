"""
Smart company + shop_name resolution for OCR/AI extraction edge cases.

Cases:
  1) Neither NIP nor shop  → no company, no shop_name, needs_review
  2) NIP only              → DB/API company, shop from brands.json / legal name
  3) Shop only             → brands→NIP if unique, else past receipt / name match / create
  4) Both                  → company by NIP, shop_name from OCR

Company.address = legal/registered address from Biała Lista only (never store OCR).
Purchase/store location belongs on Receipt.store_address (set by the caller from OCR).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from src.backend.api.services.nip_lookup import fetch_company_by_nip
from src.backend.db.models import Company, Receipt
from src.backend.services.brands import (
    clean_nip,
    ensure_brand_in_catalog,
    find_nips_for_shop_hint,
    resolve_brand_name,
)

logger = logging.getLogger(__name__)

UNKNOWN_SHOP = "Unknown"


@dataclass
class CompanyResolutionResult:
    company_id: Optional[int]
    shop_name: Optional[str]
    needs_review: bool
    case: str


def _normalize_shop(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.lower() in {"unknown", "unknown company", "null", "none", "n/a"}:
        return None
    return text


async def _get_company_by_nip(db: AsyncSession, nip: str) -> Optional[Company]:
    stmt = select(Company).where(Company.nip == nip)
    return (await db.execute(stmt)).scalars().first()


async def _get_company_by_name(db: AsyncSession, name: str) -> Optional[Company]:
    stmt = select(Company).where(func.lower(Company.name) == name.lower())
    return (await db.execute(stmt)).scalars().first()


async def _find_company_via_past_shop_name(db: AsyncSession, shop_name: str) -> Optional[Company]:
    stmt = (
        select(Company)
        .join(Receipt, Receipt.company_id == Company.id)
        .where(func.lower(Receipt.shop_name) == shop_name.lower())
        .limit(1)
    )
    return (await db.execute(stmt)).scalars().first()


async def _enrich_legal_address_from_api(company: Company, nip: Optional[str]) -> None:
    """Fill Company.address from Biała Lista only when empty (one-time cache enrichment)."""
    if company.address or not nip or len(nip) != 10:
        return
    api = await fetch_company_by_nip(nip)
    if not api:
        return
    if api.get("address"):
        company.address = str(api["address"])[:255]
    if api.get("name") and (
        not company.name
        or company.name.strip().lower() in {"unknown", "unknown company"}
    ):
        company.name = api["name"]


async def _create_company_from_api_or_stub(
    db: AsyncSession,
    nip: str,
    fallback_name: str,
) -> Company:
    """Create company once. Prefer Biała Lista; fall back to stub. Caller must have checked DB cache."""
    api = await fetch_company_by_nip(nip)
    if api:
        company = Company(
            nip=api["nip"],
            name=api["name"],
            address=api.get("address"),
        )
    else:
        company = Company(nip=nip, name=fallback_name or UNKNOWN_SHOP, address=None)
    db.add(company)
    await db.flush()
    return company


async def get_or_create_company_by_nip(
    db: AsyncSession,
    nip_raw: Optional[str],
    fallback_name: Optional[str] = None,
) -> Optional[Company]:
    """
    DB-first company lookup by NIP. Hits Biała Lista only when NIP is not cached,
    or once more if cached company has empty legal address.
    """
    nip = clean_nip(nip_raw)
    if len(nip) != 10:
        return None
    company = await _get_company_by_nip(db, nip)
    if company:
        await _enrich_legal_address_from_api(company, nip)
        return company
    return await _create_company_from_api_or_stub(
        db,
        nip,
        fallback_name=fallback_name or UNKNOWN_SHOP,
    )


async def _resolve_with_nip(
    db: AsyncSession,
    nip: str,
    shop_ocr: Optional[str],
) -> CompanyResolutionResult:
    """Cases 2 and 4 (and shop-only upgraded via brand NIP)."""
    company = await _get_company_by_nip(db, nip)
    created = False
    legal_name: Optional[str] = None

    if company:
        legal_name = company.name
        await _enrich_legal_address_from_api(company, nip)
    else:
        company = await _create_company_from_api_or_stub(
            db,
            nip,
            fallback_name=shop_ocr or UNKNOWN_SHOP,
        )
        created = True
        legal_name = company.name

    if shop_ocr:
        shop_name = shop_ocr
        case = "both"
        ensure_brand_in_catalog(brand_name=shop_ocr, nip=nip, legal_alias=legal_name)
        needs_review = False
    else:
        mapped = resolve_brand_name(nip=nip, legal_name=legal_name)
        if mapped:
            shop_name = mapped
            ensure_brand_in_catalog(brand_name=mapped, nip=nip, legal_alias=legal_name)
            needs_review = False
        else:
            shop_name = legal_name
            if legal_name:
                ensure_brand_in_catalog(brand_name=legal_name, nip=nip, legal_alias=legal_name)
            needs_review = True
        case = "nip_only"

    logger.info(
        "Company resolve case=%s nip=%s company_id=%s shop=%s created=%s review=%s",
        case, nip, company.id, shop_name, created, needs_review,
    )
    return CompanyResolutionResult(
        company_id=company.id,
        shop_name=shop_name,
        needs_review=needs_review,
        case=case,
    )


async def resolve_company_and_shop(
    db: AsyncSession,
    nip_raw: Optional[str],
    shop_name_ocr: Optional[str],
) -> CompanyResolutionResult:
    """
    Main entry: map OCR NIP + shop into Company + Receipt.shop_name.
    """
    shop = _normalize_shop(shop_name_ocr)
    nip = clean_nip(nip_raw)

    if 7 <= len(nip) <= 9:
        stmt = select(Company).where(Company.nip.like(f"%{nip}%")).limit(2)
        matches = list((await db.execute(stmt)).scalars().all())
        if len(matches) == 1 and matches[0].nip:
            nip = matches[0].nip

    has_nip = len(nip) == 10
    has_shop = shop is not None

    if not has_nip and not has_shop:
        logger.info("Company resolve case=neither")
        return CompanyResolutionResult(
            company_id=None,
            shop_name=None,
            needs_review=True,
            case="neither",
        )

    if has_nip:
        return await _resolve_with_nip(db, nip, shop)

    assert shop is not None

    candidate_nips = find_nips_for_shop_hint(shop)
    if len(candidate_nips) == 1:
        logger.info("Company resolve case=shop_only upgraded via brand NIP=%s", candidate_nips[0])
        return await _resolve_with_nip(db, candidate_nips[0], shop)

    past_company = await _find_company_via_past_shop_name(db, shop)
    if past_company:
        logger.info("Company resolve case=shop_only via past receipt company_id=%s", past_company.id)
        await _enrich_legal_address_from_api(past_company, past_company.nip)
        return CompanyResolutionResult(
            company_id=past_company.id,
            shop_name=shop,
            needs_review=past_company.nip is None,
            case="shop_only_past",
        )

    named = await _get_company_by_name(db, shop)
    if named:
        logger.info("Company resolve case=shop_only via company name id=%s", named.id)
        await _enrich_legal_address_from_api(named, named.nip)
        return CompanyResolutionResult(
            company_id=named.id,
            shop_name=resolve_brand_name(shop_hint=shop) or shop,
            needs_review=named.nip is None,
            case="shop_only_name",
        )

    brand = resolve_brand_name(shop_hint=shop) or shop
    company = Company(nip=None, name=brand, address=None)
    db.add(company)
    await db.flush()
    ensure_brand_in_catalog(brand_name=brand, legal_alias=shop)
    logger.info("Company resolve case=shop_only created company_id=%s (no NIP)", company.id)
    return CompanyResolutionResult(
        company_id=company.id,
        shop_name=brand,
        needs_review=True,
        case="shop_only_new",
    )
