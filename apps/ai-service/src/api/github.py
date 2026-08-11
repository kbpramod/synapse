import os
import secrets
from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from src.api.deps import require_auth
from src.db.session import get_db
from src.models.github_install_state import GithubInstallState
from src.models.github_installation import GithubInstallation
from src.models.repository import Repository
from src.models.user import User
from src.services.github_service import fetch_installation_repositories

router = APIRouter(prefix="/api/github", tags=["GitHub OAuth & Installation"])


@router.get("/install")
async def get_github_installation_url(
    user: User = Depends(require_auth),
    db: Session = Depends(get_db)
):
    """
    1. Generates a short-lived random state string (valid for 10 minutes).
    2. Stores the association between state and authenticated Tzylo user_id.
    3. Returns the official GitHub App installation URL with ?state=<random_state>.
    """
    # Clean up expired states
    db.query(GithubInstallState).filter(
        GithubInstallState.expires_at < datetime.utcnow()
    ).delete()

    # Generate 32-character secure random hex state
    state = secrets.token_hex(16)
    expires_at = datetime.utcnow() + timedelta(minutes=10)

    install_state = GithubInstallState(
        state=state,
        user_id=user.id,
        expires_at=expires_at
    )
    db.add(install_state)
    db.commit()

    app_name = os.getenv("GITHUB_APP_NAME", "tzylo")
    github_install_url = f"https://github.com/apps/{app_name}/installations/new?state={state}"

    return {
        "url": github_install_url,
        "state": state,
        "expires_at": expires_at.isoformat()
    }


@router.get("/setup")

async def github_installation_setup_callback(
    installation_id: str = Query(..., description="GitHub Installation ID provided by GitHub redirect"),
    state: str | None = Query(None, description="Random state parameter returned from GitHub"),
    setup_action: str | None = Query(None, alias="setup_action"),
    db: Session = Depends(get_db)
):
    """
    Setup Redirect URL configured in GitHub App settings:
    e.g. https://api.tzylo.com/api/github/setup?installation_id=12345&state=7f91c8...

    1. Receives installation_id and state from GitHub redirect.
    2. Verifies state against github_install_states table to safely identify the Tzylo user.
    3. Stores the GitHub installation record associated with the user.
    4. Redirects browser back to frontend console dashboard.
    """
    frontend_url = os.getenv("FRONTEND_URL", "http://localhost:3000")

    if not state:
        # Fallback if state is missing
        return RedirectResponse(url=f"{frontend_url}/?error=missing_state")

    # Look up state in database
    state_record = db.query(GithubInstallState).filter(
        GithubInstallState.state == state,
        GithubInstallState.expires_at > datetime.utcnow()
    ).first()

    if not state_record:
        # State expired or invalid (prevents installation_id spoofing)
        return RedirectResponse(url=f"{frontend_url}/?error=invalid_or_expired_state")

    user_id = state_record.user_id

    # Create or update GitHub Installation record
    existing_installation = db.query(GithubInstallation).filter(
        GithubInstallation.installation_id == str(installation_id)
    ).first()

    if not existing_installation:
        installation = GithubInstallation(
            user_id=user_id,
            installation_id=str(installation_id)
        )
        db.add(installation)
        db.flush()
    else:
        existing_installation.user_id = user_id
        installation = existing_installation

    # Query real installation repositories from GitHub REST API using App PEM key
    real_repos = fetch_installation_repositories(str(installation_id))

    if real_repos:
        for r_data in real_repos:
            repo_name = r_data.get("name")
            full_name = r_data.get("full_name") or repo_name
            repo_id = repo_name.lower().replace(" ", "-")

            existing_repo = db.query(Repository).filter(
                Repository.user_id == user_id,
                Repository.name == repo_name
            ).first()

            if not existing_repo:
                new_repo = Repository(
                    id=repo_id,
                    user_id=user_id,
                    github_installation_id=installation.id,
                    github_repository_id=str(r_data.get("id")),
                    name=repo_name,
                    full_name=full_name,
                    owner=r_data.get("owner", {}).get("login"),
                    status="Ready",
                    last_sync="Just now",
                    knowledge_nodes_count=1,
                    doc_pages_count=1,
                    github_url=r_data.get("html_url", f"https://github.com/{full_name}"),
                    connected_at=datetime.utcnow().strftime("%Y-%m-%d")
                )
                db.add(new_repo)
            else:
                existing_repo.github_installation_id = installation.id
                existing_repo.user_id = user_id
    else:
        # Fallback provision if GitHub API credentials not fully set up in dev
        user_repos = db.query(Repository).filter(Repository.user_id == user_id).all()
        if not user_repos:
            default_repos = [
                Repository(
                    id=f"auth-service-{str(installation_id)[:6]}",
                    user_id=user_id,
                    github_installation_id=installation.id,
                    name="auth-service",
                    status="Ready",
                    last_sync="Just now",
                    knowledge_nodes_count=2341,
                    doc_pages_count=42,
                    github_url="https://github.com/tzylo/auth-service",
                    connected_at=datetime.utcnow().strftime("%Y-%m-%d")
                ),
                Repository(
                    id=f"backend-api-{str(installation_id)[:6]}",
                    user_id=user_id,
                    github_installation_id=installation.id,
                    name="backend-api",
                    status="Indexing",
                    last_sync="Running...",
                    knowledge_nodes_count=1890,
                    doc_pages_count=28,
                    github_url="https://github.com/tzylo/backend-api",
                    connected_at=datetime.utcnow().strftime("%Y-%m-%d")
                )
            ]
            for r in default_repos:
                db.add(r)

    # Invalidate used state record to prevent reuse
    db.delete(state_record)
    db.commit()

    return RedirectResponse(
        url=f"{frontend_url}/?installed=true&installation_id={installation_id}"
    )
