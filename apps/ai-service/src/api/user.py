from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from src.api.deps import require_auth
from src.db.session import get_db
from models.user import User
from src.schemas.user import UserMeResponse, UserProfileResponse, UserProfileUpdate

router = APIRouter(prefix="/api/user", tags=["User & Identity"])


@router.get("/me", response_model=UserMeResponse)
async def get_current_user_me(user: User = Depends(require_auth)):
    """Return basic authentication identity and verification status."""
    return UserMeResponse(
        id=user.id,
        email=user.email,
        name=user.name,
        isVerified=user.is_verified
    )


@router.get("/profile", response_model=UserProfileResponse)
async def get_user_profile(user: User = Depends(require_auth)):
    """Fetch extended user profile settings."""
    return UserProfileResponse(displayName=user.name or "")


@router.patch("/profile", response_model=UserProfileResponse)
async def update_user_profile(
    payload: UserProfileUpdate,
    user: User = Depends(require_auth),
    db: Session = Depends(get_db)
):
    """Update user profile details."""
    user.name = payload.displayName
    db.commit()
    db.refresh(user)

    return UserProfileResponse(displayName=user.name or "")
