from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from sqlalchemy.exc import IntegrityError

from src.backend.db.database import get_db
from src.backend.db.models import User, Receipt
from src.backend.api.deps import get_current_user
from src.backend.schemas import UserResponse, UserUpdate
from src.backend.core.security import get_password_hash
from src.backend.core.storage import delete_receipt_image
from src.backend.services.receipt_extraction import cancel_receipt_extraction

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
        new_email = payload.email.strip()
        existing = await db.execute(
            select(User.id).where(
                func.lower(User.email) == new_email.lower(),
                User.id != current_user.id,
            )
        )
        if existing.scalar_one_or_none() is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Email already registered",
            )
        current_user.email = new_email
    if payload.password is not None:
        current_user.password_hash = get_password_hash(payload.password)

    db.add(current_user)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email already registered",
        )
    await db.refresh(current_user)

    return current_user


@router.delete("/me", status_code=status.HTTP_204_NO_CONTENT)
async def delete_current_user(
        current_user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db)
):
    result = await db.execute(
        select(Receipt.id, Receipt.image_url).where(Receipt.user_id == current_user.id)
    )
    for receipt_id, image_url in result.all():
        cancel_receipt_extraction(receipt_id)
        delete_receipt_image(image_url)

    await db.delete(current_user)
    await db.commit()
    return None