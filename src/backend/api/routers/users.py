from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from src.backend.db.database import get_db
from src.backend.db.models import User, Receipt
from src.backend.api.deps import get_current_user
from src.backend.schemas import UserResponse, UserUpdate
from src.backend.core.security import get_password_hash
from src.backend.core.storage import delete_receipt_image

router = APIRouter(prefix="/users", tags=["Users"])

@router.get("/me", response_model=UserResponse)

async def get_current_user_profile(current_user: User = Depends(get_current_user)):
    """
    Returns the profile of the currently logged-in user.
    Requires a valid JWT Bearer token in the request headers.
    """
    return current_user


@router.put("/me", response_model=UserResponse)
async def update_current_user_profile(
        payload: UserUpdate,
        current_user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db)
):
    if payload.first_name is not None:
        current_user.first_name = payload.first_name
    if payload.last_name is not None:
        current_user.last_name = payload.last_name
    if payload.email is not None:
        current_user.email = payload.email
    if payload.password is not None:
        current_user.password_hash = get_password_hash(payload.password)

    db.add(current_user)
    await db.commit()
    await db.refresh(current_user)

    return current_user


@router.delete("/me", status_code=status.HTTP_204_NO_CONTENT)
async def delete_current_user(
        current_user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db)
):
    result = await db.execute(
        select(Receipt.image_url).where(Receipt.user_id == current_user.id)
    )
    for image_url in result.scalars().all():
        delete_receipt_image(image_url)

    await db.delete(current_user)
    await db.commit()
    return None