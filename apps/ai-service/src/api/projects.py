from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from src.api.deps import require_auth
from src.db.session import get_db
from models.project import Project
from models.user import User
from src.schemas.onboarding import ProjectResponse

router = APIRouter(prefix="/api/projects", tags=["Projects Workspace"])


@router.get("", response_model=list[ProjectResponse])
@router.get("/", response_model=list[ProjectResponse])
async def list_user_projects(
    user: User = Depends(require_auth),
    db: Session = Depends(get_db)
):
    """Fetches projects belonging to the logged-in user."""
    projects = db.query(Project).filter(Project.user_id == user.id).all()

    # If no projects exist yet, provision initial project
    if not projects:
        new_project = Project(user_id=user.id, name="Main Production Stack")
        db.add(new_project)
        db.commit()
        db.refresh(new_project)
        projects = [new_project]

    return [ProjectResponse(id=p.id, name=p.name) for p in projects]
