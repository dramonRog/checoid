from typing import List
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from src.backend.db.database import get_db
from src.backend.db.models import Category, User
from src.backend.api.deps import get_current_user
from src.backend.schemas import CategoryResponse

router = APIRouter(prefix="/categories", tags=["Categories"])

@router.get("", response_model=List[CategoryResponse])
async def list_categories(
        current_user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db)
):
    """
    GET /api/v1/categories
    Retrieves a lightweight list of all available categories.
    Ordered by name for frontend UI dropdowns.
    """

    stmt = select(Category).order_by(Category.name)
    result = await db.execute(stmt)
    categories = result.scalars().all()

    return categories