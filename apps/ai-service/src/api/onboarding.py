from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from src.api.deps import require_auth
from src.db.session import get_db
from src.models.project import Project
from src.models.user import User
from src.schemas.onboarding import OnboardingRequest, OnboardingResponse

router = APIRouter(prefix="/api/onboarding", tags=["Onboarding"])


@router.post("", response_model=OnboardingResponse, status_code=status.HTTP_201_CREATED)
@router.post("/", response_model=OnboardingResponse, status_code=status.HTTP_201_CREATED)
async def create_onboarding_record(
    payload: OnboardingRequest,
    user: User = Depends(require_auth),
    db: Session = Depends(get_db)
):
    """Submits initial user onboarding parameters upon workspace setup."""
    # Update user display name
    user.name = payload.displayName
    db.add(user)

    # Check if default project exists for user; if not, create one
    existing_project = db.query(Project).filter(Project.user_id == user.id).first()
    if not existing_project:
        default_project = Project(
            user_id=user.id,
            name="Main Production Stack"
        )
        db.add(default_project)

    db.commit()

    return OnboardingResponse(
        success=True,
        message="Onboarding completed successfully."
    )
