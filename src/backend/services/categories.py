"""Category catalog: JSON seed + DB helpers for get-or-create resolution."""
from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

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
    label = (raw_name or "").strip() or "Inne"
    lookup = build_alias_lookup()
    catalog_hit = lookup.get(_normalize_label(label))

    # 1) Synonym / alias of seeded category → reuse, NO json write
    if catalog_hit:
        target_name = catalog_hit
        created_new = False
    else:
        soft = resolve_canonical_category_name(label)
        if soft != "Inne" or _normalize_label(label) in {"inne", "other", "unknown", "misc"}:
            # soft matched an existing catalog concept → reuse, NO json write
            target_name = soft
            created_new = False
        else:
            # truly unknown label from LLM
            target_name = label[:100]
            created_new = True

    # 2) Reuse DB row if present
    stmt = select(Category).where(Category.name == target_name)
    category = (await db.execute(stmt)).scalars().first()
    if category:
        return category.id

    existing = (await db.execute(select(Category))).scalars().all()
    for row in existing:
        if _normalize_label(row.name) == _normalize_label(target_name):
            return row.id

    # 3) Create in DB
    category = Category(name=target_name)
    db.add(category)
    await db.flush()

    # 4) Only if it was a brand-new LLM concept → append JSON
    if created_new and _normalize_label(target_name) != "inne":
        append_category_to_json(target_name)

    return category.id


def category_names_for_prompt() -> str:
    names = get_canonical_category_names()
    return ", ".join(names)


def append_category_to_json(name: str, aliases: list | None = None) -> None:
    """Persist a new canonical category into categories.json."""
    name = name.strip()
    if not name:
        return

    path = CATEGORIES_JSON_PATH
    with path.open(encoding="utf-8") as f:
        payload = json.load(f)

    categories = payload.setdefault("categories", [])
    norm = _normalize_label(name)

    # Already in JSON (name or alias)? Do nothing
    for entry in categories:
        keys = [entry.get("name", ""), *(entry.get("aliases") or [])]
        if any(_normalize_label(k) == norm for k in keys if k):
            return

    categories.append({"name": name, "aliases": aliases or []})

    with path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
        f.write("\n")

    load_category_catalog.cache_clear()

