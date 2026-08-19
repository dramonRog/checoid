"""Seed JSON catalogs must stay read-only at runtime."""
from __future__ import annotations

from copy import deepcopy

import pytest

from src.backend.db.models import Category
from src.backend.services import brands
from src.backend.services.categories import (
    CATEGORIES_JSON_PATH,
    get_or_create_category_id,
)


@pytest.mark.asyncio
async def test_unknown_category_creates_db_row_not_json(db_session):
    before = CATEGORIES_JSON_PATH.read_bytes()
    cat_id = await get_or_create_category_id(db_session, "UnikalnaKategoriaTestowa123")
    await db_session.commit()

    assert cat_id is not None
    assert CATEGORIES_JSON_PATH.read_bytes() == before
    row = await db_session.get(Category, cat_id)
    assert row is not None
    assert row.name == "UnikalnaKategoriaTestowa123"


@pytest.mark.asyncio
async def test_seeded_alias_reuses_canonical_category(db_session, app):
    """app fixture seeds categories.json into the shared test DB."""
    before = CATEGORIES_JSON_PATH.read_bytes()
    cat_id = await get_or_create_category_id(db_session, "Masło")
    await db_session.commit()

    assert CATEGORIES_JSON_PATH.read_bytes() == before
    row = await db_session.get(Category, cat_id)
    assert row is not None
    assert row.name == "Nabiał"


def test_ensure_brand_updates_memory_not_file():
    original = deepcopy(brands.load_brand_catalog())
    fingerprint = brands.BRANDS_JSON_PATH.read_bytes()
    unique = "TestShopRuntimeOnlyXYZ"
    try:
        brands.ensure_brand_in_catalog(
            brand_name=unique,
            nip="5293610748",
            legal_alias="Test Shop Sp. z o.o.",
        )
        assert brands.BRANDS_JSON_PATH.read_bytes() == fingerprint
        assert brands.resolve_brand_name(shop_hint=unique) == unique
        assert brands.resolve_brand_name(nip="5293610748") == unique
    finally:
        live = brands.load_brand_catalog()
        live.clear()
        live.extend(original)
