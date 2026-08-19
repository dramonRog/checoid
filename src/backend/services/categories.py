"""Category catalog: JSON seed + DB get-or-create. Seed file is never written."""
from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.backend.db.models import Category

logger = logging.getLogger(__name__)

CATEGORIES_JSON_PATH = Path(__file__).resolve().parent.parent / "data" / "categories.json"


@lru_cache(maxsize=1)
def load_category_catalog() -> List[Dict[str, Any]]:
    with CATEGORIES_JSON_PATH.open(encoding="utf-8") as f:
        payload = json.load(f)
    return list(payload.get("categories") or [])


def get_canonical_category_names() -> List[str]:
    return [entry["name"] for entry in load_category_catalog() if entry.get("name")]


def build_alias_lookup() -> Dict[str, str]:
    """Map normalized alias/name -> canonical category name."""
    lookup: Dict[str, str] = {}
    for entry in load_category_catalog():
        canonical = entry["name"]
        keys = [canonical, *(entry.get("aliases") or [])]
        for key in keys:
            lookup[_normalize_label(key)] = canonical
    return lookup


def _normalize_label(value: str) -> str:
    return " ".join(str(value).strip().lower().replace("ł", "l").replace("Ł", "l").split())


def resolve_canonical_category_name(raw_name: Optional[str]) -> str:
    if not raw_name or not str(raw_name).strip():
        return "Inne"
    lookup = build_alias_lookup()
    hit = lookup.get(_normalize_label(raw_name))
    if hit:
        return hit
    # Soft contains match against canonical names / aliases
    needle = _normalize_label(raw_name)
    for key, canonical in lookup.items():
        if needle in key or key in needle:
            return canonical
    return "Inne"


async def seed_categories(db: AsyncSession) -> int:
    """Insert missing seeded categories. Returns number of newly inserted rows."""
    catalog = load_category_catalog()
    if not catalog:
        logger.warning("Category catalog is empty; skip seeding.")
        return 0

    existing = (await db.execute(select(Category))).scalars().all()
    existing_by_norm = {_normalize_label(c.name): c for c in existing}

    created = 0
    for entry in catalog:
        name = entry["name"]
        if _normalize_label(name) in existing_by_norm:
            continue
        db.add(Category(name=name))
        created += 1

    if created:
        await db.commit()
        logger.info("Seeded %s categories from %s", created, CATEGORIES_JSON_PATH.name)
    else:
        await db.rollback()
    return created


async def get_or_create_category_id(db: AsyncSession, raw_name: Optional[str]) -> Optional[int]:
    """Resolve a label to a Category row. Unknown labels are inserted in DB only."""
    label = (raw_name or "").strip() or "Inne"
    lookup = build_alias_lookup()
    catalog_hit = lookup.get(_normalize_label(label))

    if catalog_hit:
        target_name = catalog_hit
    else:
        soft = resolve_canonical_category_name(label)
        if soft != "Inne" or _normalize_label(label) in {"inne", "other", "unknown", "misc"}:
            target_name = soft
        else:
            target_name = label[:100]

    stmt = select(Category).where(Category.name == target_name)
    category = (await db.execute(stmt)).scalars().first()
    if category:
        return category.id

    existing = (await db.execute(select(Category))).scalars().all()
    for row in existing:
        if _normalize_label(row.name) == _normalize_label(target_name):
            return row.id

    category = Category(name=target_name)
    db.add(category)
    await db.flush()
    return category.id


def category_names_for_prompt() -> str:
    names = get_canonical_category_names()
    return ", ".join(names)

