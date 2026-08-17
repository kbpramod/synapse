import json
import os
import time
import urllib.request
from datetime import datetime
from uuid import uuid4
from typing import Optional, List, Dict, Any

import jwt
from sqlalchemy.orm import Session

from src.models.github_installation import GithubInstallation
from src.models.repository import GithubRepository, Repository


def parse_github_timestamp(ts_str: str | None) -> datetime:
    """Parses GitHub ISO-8601 timestamp string into datetime."""
    if not ts_str:
        return datetime.utcnow()
    try:
        # Handles ISO format like 2026-08-17T12:00:00Z or with millis
        cleaned = ts_str.replace("Z", "+00:00")
        dt = datetime.fromisoformat(cleaned)
        return dt.replace(tzinfo=None)
    except Exception:
        return datetime.utcnow()


def get_github_app_jwt() -> str:
    """Generates RS256 signed JWT for GitHub App authentication."""
    app_id = os.getenv("GITHUB_APP_ID")
    pem_path = os.getenv("GITHUB_PRIVATE_KEY_PATH", "tzylo-synapse.2026-04-26.private-key.pem")

    # If pem_path is relative, resolve from current working directory or ai-service root
    if not os.path.isabs(pem_path):
        possible_paths = [
            pem_path,
            os.path.join(os.getcwd(), pem_path),
            os.path.join(os.path.dirname(__file__), "..", "..", pem_path)
        ]
        for p in possible_paths:
            if os.path.exists(p):
                pem_path = p
                break

    if not os.path.exists(pem_path):
        raise FileNotFoundError(f"GitHub App private key not found at {pem_path}")

    with open(pem_path, "r", encoding="utf-8") as f:
        private_key = f.read()

    now = int(time.time())
    payload = {
        "iat": now - 60,
        "exp": now + (10 * 60),
        "iss": str(app_id) if app_id else "1"
    }

    return jwt.encode(payload, private_key, algorithm="RS256")


def get_installation_access_token(installation_id: str) -> str:
    """Obtains temporary Installation Access Token from GitHub API."""
    try:
        app_jwt = get_github_app_jwt()
    except Exception as exc:
        print(f"[GITHUB API ERROR] Could not create App JWT: {exc}")
        return ""

    url = f"https://api.github.com/app/installations/{installation_id}/access_tokens"

    req = urllib.request.Request(
        url,
        method="POST",
        headers={
            "Authorization": f"Bearer {app_jwt}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "Tzylo-App"
        }
    )

    try:
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data["token"]
    except Exception as exc:
        print(f"[GITHUB API ERROR] Could not get installation token for {installation_id}: {exc}")
        return ""


def fetch_installation_repositories(installation_id: str) -> list[dict]:
    """Fetches list of all repositories granted to this GitHub installation via REST API."""
    token = get_installation_access_token(installation_id)
    if not token:
        return []

    url = "https://api.github.com/installation/repositories"
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "Tzylo-App"
        }
    )

    try:
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data.get("repositories", [])
    except Exception as exc:
        print(f"[GITHUB API ERROR] Could not fetch installation repositories: {exc}")
        return []


def extract_installation_repo_info(payload: dict) -> Optional[Dict[str, Any]]:
    """
    Extracts action, installation metadata, and affected repositories
    from an installation_repositories webhook payload.
    """
    try:
        action = payload.get("action")
        if action not in ("added", "removed"):
            return None

        repositories = (
            payload.get("repositories_added", [])
            if action == "added"
            else payload.get("repositories_removed", [])
        )

        installation = payload.get("installation", {})
        account = installation.get("account", {})
        sender = payload.get("sender", {})

        # Extract GitHub's event timestamp if present
        event_time_str = (
            payload.get("created_at")
            or installation.get("updated_at")
            or installation.get("created_at")
        )
        event_time = parse_github_timestamp(event_time_str)

        return {
            "action": action,
            "installationId": str(installation.get("id")) if installation.get("id") else None,
            "account": account.get("login"),
            "accountId": str(account.get("id")) if account.get("id") else None,
            "accountType": account.get("type"),
            "sender": sender.get("login"),
            "repositories": [
                {
                    "id": str(repo.get("id")),
                    "name": repo.get("name"),
                    "full_name": repo.get("full_name") or repo.get("name"),
                    "private": bool(repo.get("private", False))
                }
                for repo in repositories
            ],
            "event_time": event_time,
            "received_at": datetime.utcnow()
        }
    except Exception as err:
        print(f"[ERROR] extracting installation_repositories payload: {err}")
        return None


def upsert_github_installation(
    db: Session,
    installation_data: dict,
    organization_id: Optional[str] = None,
    user_id: Optional[str] = None,
    status: str = "active"
) -> GithubInstallation:
    """
    Creates or updates a GithubInstallation record.
    Works whether initiated with or without Tzylo authentication.
    """
    inst_id_str = str(installation_data.get("id") or installation_data.get("github_installation_id"))
    account = installation_data.get("account", {})
    account_id = str(account.get("id") or installation_data.get("github_account_id") or "")
    account_login = account.get("login") or installation_data.get("github_account_login")
    account_type = account.get("type") or installation_data.get("github_account_type")
    
    created_at_val = parse_github_timestamp(
        installation_data.get("created_at") or installation_data.get("installed_at")
    )

    installation = db.query(GithubInstallation).filter(
        GithubInstallation.github_installation_id == inst_id_str
    ).first()

    if not installation:
        installation = GithubInstallation(
            id=str(uuid4()),
            github_installation_id=inst_id_str,
            github_account_id=account_id if account_id else None,
            github_account_login=account_login,
            github_account_type=account_type,
            organization_id=organization_id,
            user_id=user_id,
            status=status,
            installed_at=created_at_val
        )
        db.add(installation)
    else:
        if account_id:
            installation.github_account_id = account_id
        if account_login:
            installation.github_account_login = account_login
        if account_type:
            installation.github_account_type = account_type
        if organization_id:
            installation.organization_id = organization_id
        if user_id:
            installation.user_id = user_id
        installation.status = status
        if status == "active" and not installation.installed_at:
            installation.installed_at = created_at_val

    db.flush()
    return installation


def upsert_github_repositories(
    db: Session,
    installation_db_id: str,
    repos_data: List[Dict[str, Any]],
    active: bool = True,
    user_id: Optional[str] = None
) -> List[GithubRepository]:
    """
    Upserts repositories associated with a given installation.
    Sets active status according to whether repos are present or added.
    """
    results: List[GithubRepository] = []

    for r_data in repos_data:
        repo_gh_id = str(r_data.get("id"))
        repo_name = r_data.get("name")
        full_name = r_data.get("full_name") or repo_name
        is_private = bool(r_data.get("private", False))
        owner = r_data.get("owner", {}).get("login") if isinstance(r_data.get("owner"), dict) else None
        html_url = r_data.get("html_url") or f"https://github.com/{full_name}"

        # Match existing repository by github_repository_id or full_name within this installation
        repo = db.query(GithubRepository).filter(
            GithubRepository.installation_id == installation_db_id,
            (
                (GithubRepository.github_repository_id == repo_gh_id) |
                (GithubRepository.full_name == full_name) |
                (GithubRepository.name == repo_name)
            )
        ).first()

        if not repo:
            repo = GithubRepository(
                id=str(uuid4()),
                installation_id=installation_db_id,
                github_repository_id=repo_gh_id,
                name=repo_name,
                full_name=full_name,
                private=is_private,
                active=active,
                user_id=user_id,
                owner=owner,
                status="Ready",
                last_sync="Just now",
                knowledge_nodes_count=0,
                doc_pages_count=0,
                github_url=html_url,
                connected_at=datetime.utcnow().strftime("%Y-%m-%d")
            )
            db.add(repo)
        else:
            repo.github_repository_id = repo_gh_id
            repo.name = repo_name
            repo.full_name = full_name
            repo.private = is_private
            repo.active = active
            if user_id and not repo.user_id:
                repo.user_id = user_id
            if owner:
                repo.owner = owner
            if html_url:
                repo.github_url = html_url

        db.flush()
        results.append(repo)

    return results


def deactivate_github_repositories(
    db: Session,
    installation_db_id: str,
    repos_data: List[Dict[str, Any]]
) -> List[GithubRepository]:
    """
    Marks repositories as inactive (active = False) when removed from an installation,
    preserving historical records, PR relationships, and knowledge nodes.
    """
    updated: List[GithubRepository] = []

    for r_data in repos_data:
        repo_gh_id = str(r_data.get("id"))
        repo_name = r_data.get("name")
        full_name = r_data.get("full_name") or repo_name

        repo = db.query(GithubRepository).filter(
            GithubRepository.installation_id == installation_db_id,
            (
                (GithubRepository.github_repository_id == repo_gh_id) |
                (GithubRepository.full_name == full_name) |
                (GithubRepository.name == repo_name)
            )
        ).first()

        if repo:
            repo.active = False
            db.flush()
            updated.append(repo)

    return updated


def sync_installation_all_repositories(
    db: Session,
    installation: GithubInstallation,
    payload_repos: Optional[List[Dict[str, Any]]] = None,
    user_id: Optional[str] = None
) -> List[GithubRepository]:
    """
    Performs full initial synchronization of repositories for an installation:
    1. Queries GitHub REST API for all currently accessible repositories.
    2. Falls back to repositories included in webhook payload if available.
    3. Upserts all repositories with active = True.
    """
    real_repos = fetch_installation_repositories(installation.github_installation_id)

    if not real_repos and payload_repos:
        real_repos = payload_repos

    if real_repos:
        return upsert_github_repositories(
            db=db,
            installation_db_id=installation.id,
            repos_data=real_repos,
            active=True,
            user_id=user_id or installation.user_id
        )

    return []
