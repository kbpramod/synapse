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
from src.models.github_event import GithubEvent
from src.models.github_user import GithubUser
from src.models.pull_request import PullRequest
from src.models.commit import Commit
from src.models.person import Person
from src.services.identity_service import identity_service

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


def fetch_pull_request_files(
    installation_id: str,
    owner: str,
    repo: str,
    pull_number: int
) -> List[Dict[str, Any]]:
    """
    Fetches the list of changed files with diff patches for a given PR from GitHub REST API:
    GET /repos/{owner}/{repo}/pulls/{pull_number}/files
    """
    token = get_installation_access_token(installation_id)
    if not token:
        return []

    url = f"https://api.github.com/repos/{owner}/{repo}/pulls/{pull_number}/files?per_page=100"
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
            return json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        print(f"[GITHUB API ERROR] Could not fetch PR files for {owner}/{repo}#{pull_number}: {exc}")
        return []


def post_pull_request_comment(
    installation_id: str,
    owner: str,
    repo: str,
    pull_number: int,
    comment_body: str
) -> Dict[str, Any]:
    """
    Posts an automated comment on a GitHub PR (Issue) via GitHub REST API:
    POST /repos/{owner}/{repo}/issues/{pull_number}/comments
    """
    token = get_installation_access_token(installation_id)
    if not token:
        print(f"[GITHUB API WARNING] Cannot post PR comment without valid installation token for {installation_id}")
        return {"success": False, "error": "missing_token"}

    url = f"https://api.github.com/repos/{owner}/{repo}/issues/{pull_number}/comments"
    payload_bytes = json.dumps({"body": comment_body}).encode("utf-8")

    req = urllib.request.Request(
        url,
        data=payload_bytes,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
            "User-Agent": "Tzylo-App"
        }
    )

    try:
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return {"success": True, "comment_id": data.get("id"), "url": data.get("html_url")}
    except Exception as exc:
        print(f"[GITHUB API ERROR] Could not post comment to {owner}/{repo}#{pull_number}: {exc}")
        return {"success": False, "error": str(exc)}


def fetch_pull_request_commits(
    installation_id: str,
    owner: str,
    repo: str,
    pull_number: int
) -> List[Dict[str, Any]]:
    """
    Fetches the list of commits for a given PR from GitHub REST API:
    GET /repos/{owner}/{repo}/pulls/{pull_number}/commits
    """
    token = get_installation_access_token(installation_id)
    if not token:
        return []

    url = f"https://api.github.com/repos/{owner}/{repo}/pulls/{pull_number}/commits?per_page=100"
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
            return json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        print(f"[GITHUB API ERROR] Could not fetch PR commits for {owner}/{repo}#{pull_number}: {exc}")
        return []


def record_github_event(
    db: Session,
    event_type: str,
    payload: Dict[str, Any],
    installation_id: Optional[str] = None,
    repository_id: Optional[str] = None,
    github_event_id: Optional[str] = None,
    github_created_at: Optional[datetime] = None,
    received_at: Optional[datetime] = None
) -> GithubEvent:
    """Records an immutable webhook event in the github_events audit timeline."""
    event = GithubEvent(
        id=str(uuid4()),
        installation_id=installation_id,
        repository_id=repository_id,
        event_type=event_type,
        github_event_id=github_event_id,
        github_created_at=github_created_at or datetime.utcnow(),
        received_at=received_at or datetime.utcnow(),
        payload=payload
    )
    db.add(event)
    db.flush()
    return event


def sync_github_user(
    db: Session,
    user_data: Dict[str, Any],
    person_id: Optional[str] = None
) -> GithubUser:
    """Upserts a GitHub user identity and links with a Person entity."""
    gh_user_id = str(user_data.get("id") or "")
    username = user_data.get("login") or user_data.get("username") or "unknown"
    avatar_url = user_data.get("avatar_url")
    email = user_data.get("email")
    display_name = user_data.get("name")

    gh_user = db.query(GithubUser).filter(
        (GithubUser.github_user_id == gh_user_id) |
        (GithubUser.username == username)
    ).first()

    if not gh_user:
        gh_user = GithubUser(
            id=str(uuid4()),
            github_user_id=gh_user_id,
            username=username,
            display_name=display_name,
            email=email,
            avatar_url=avatar_url,
            person_id=person_id,
            created_at=datetime.utcnow()
        )
        db.add(gh_user)
    else:
        gh_user.username = username
        if display_name:
            gh_user.display_name = display_name
        if email:
            gh_user.email = email
        if avatar_url:
            gh_user.avatar_url = avatar_url
        if person_id:
            gh_user.person_id = person_id

    db.flush()
    return gh_user


def sync_pull_request(
    db: Session,
    pr_data: Dict[str, Any],
    repository_id: str,
    author_github_user_id: Optional[str] = None
) -> PullRequest:
    """Upserts a PullRequest entity with branch info, SHAs, and timestamps."""
    github_pr_id = str(pr_data.get("id") or "")
    pr_number = pr_data.get("number", 1)
    title = pr_data.get("title") or "Untitled PR"
    description = pr_data.get("body") or ""

    head = pr_data.get("head", {}) if isinstance(pr_data.get("head"), dict) else {}
    base = pr_data.get("base", {}) if isinstance(pr_data.get("base"), dict) else {}

    source_branch = head.get("ref") or "feature"
    target_branch = base.get("ref") or "main"
    head_sha = head.get("sha")
    base_sha = base.get("sha")

    status = "open"
    if pr_data.get("merged", False):
        status = "merged"
    elif pr_data.get("state") == "closed":
        status = "closed"

    opened_at = parse_github_timestamp(pr_data.get("created_at"))
    updated_at = parse_github_timestamp(pr_data.get("updated_at"))
    closed_at = parse_github_timestamp(pr_data.get("closed_at")) if pr_data.get("closed_at") else None
    merged_at = parse_github_timestamp(pr_data.get("merged_at")) if pr_data.get("merged_at") else None

    pr = db.query(PullRequest).filter(
        (PullRequest.repository_id == repository_id) &
        ((PullRequest.github_pr_id == github_pr_id) | (PullRequest.number == pr_number))
    ).first()

    if not pr:
        pr = PullRequest(
            id=str(uuid4()),
            repository_id=repository_id,
            github_pr_id=github_pr_id,
            number=pr_number,
            title=title,
            description=description,
            author_github_user_id=author_github_user_id,
            source_branch=source_branch,
            target_branch=target_branch,
            head_sha=head_sha,
            base_sha=base_sha,
            status=status,
            opened_at=opened_at,
            updated_at=updated_at,
            closed_at=closed_at,
            merged_at=merged_at,
            created_at=datetime.utcnow()
        )
        db.add(pr)
    else:
        pr.title = title
        pr.description = description
        pr.source_branch = source_branch
        pr.target_branch = target_branch
        pr.head_sha = head_sha
        pr.base_sha = base_sha
        pr.status = status
        pr.opened_at = opened_at
        pr.updated_at = updated_at
        pr.closed_at = closed_at
        pr.merged_at = merged_at
        if author_github_user_id:
            pr.author_github_user_id = author_github_user_id

    db.flush()
    return pr


def sync_commits(
    db: Session,
    commits_data: List[Dict[str, Any]],
    pull_request_id: str,
    repository_id: str,
    default_author_user_id: Optional[str] = None
) -> List[Commit]:
    """Upserts git commit records associated with a Pull Request."""
    results: List[Commit] = []

    for c_data in commits_data:
        sha = c_data.get("sha") or c_data.get("id") or ""
        if not sha:
            continue

        commit_obj = c_data.get("commit", {}) if isinstance(c_data.get("commit"), dict) else {}
        message = commit_obj.get("message") or c_data.get("message") or ""
        
        # Author details
        author_gh_user_id = default_author_user_id
        author_info = c_data.get("author")
        if isinstance(author_info, dict) and author_info.get("login"):
            gh_user = sync_github_user(db, author_info)
            author_gh_user_id = gh_user.id

        committed_at_str = commit_obj.get("author", {}).get("date") if isinstance(commit_obj.get("author"), dict) else None
        committed_at = parse_github_timestamp(committed_at_str)

        commit = db.query(Commit).filter(
            Commit.pull_request_id == pull_request_id,
            Commit.github_commit_sha == sha
        ).first()

        if not commit:
            commit = Commit(
                id=str(uuid4()),
                pull_request_id=pull_request_id,
                repository_id=repository_id,
                github_commit_sha=sha,
                author_github_user_id=author_gh_user_id,
                message=message,
                committed_at=committed_at,
                created_at=datetime.utcnow()
            )
            db.add(commit)
        else:
            commit.message = message
            commit.committed_at = committed_at
            if author_gh_user_id:
                commit.author_github_user_id = author_gh_user_id

        db.flush()
        results.append(commit)

    return results


def ensure_github_repository(
    db: Session,
    repo_data: Dict[str, Any],
    installation_db_id: Optional[str] = None,
    user_id: Optional[str] = None
) -> GithubRepository:
    """
    Ensures a GithubRepository exists in the database.
    If not present, auto-creates it with a new UUID and active=True.
    """
    repo_gh_id = str(repo_data.get("id") or "")
    repo_name = repo_data.get("name") or "default-repo"
    full_name = repo_data.get("full_name") or repo_name
    owner_info = repo_data.get("owner", {})
    owner = owner_info.get("login") if isinstance(owner_info, dict) else (full_name.split("/")[0] if "/" in full_name else None)
    is_private = bool(repo_data.get("private", False))
    html_url = repo_data.get("html_url") or f"https://github.com/{full_name}"

    repo = db.query(GithubRepository).filter(
        (GithubRepository.github_repository_id == repo_gh_id) |
        (GithubRepository.full_name == full_name) |
        (GithubRepository.name == repo_name)
    ).first()

    if not repo:
        repo = GithubRepository(
            id=str(uuid4()),
            installation_id=installation_db_id,
            github_repository_id=repo_gh_id if repo_gh_id else None,
            name=repo_name,
            full_name=full_name,
            private=is_private,
            active=True,
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
        if installation_db_id and not repo.installation_id:
            repo.installation_id = installation_db_id
        if repo_gh_id and not repo.github_repository_id:
            repo.github_repository_id = repo_gh_id
        if full_name:
            repo.full_name = full_name
        if owner and not repo.owner:
            repo.owner = owner

    db.flush()
    return repo


def ensure_github_user(
    db: Session,
    user_data: Dict[str, Any],
    person_id: Optional[str] = None
) -> GithubUser:
    """
    Ensures a GithubUser exists in the database.
    If not present, auto-creates it with a new UUID.
    """
    return sync_github_user(db=db, user_data=user_data, person_id=person_id)


def ensure_github_context(
    db: Session,
    payload: Dict[str, Any],
    event_time: Optional[datetime] = None
) -> Dict[str, Any]:
    """
    Auto-provisions and returns GitHub Installation, Repository, User, and Person
    whenever they are referenced in an incoming webhook payload but not yet in the DB.
    """
    event_time = event_time or datetime.utcnow()

    # 1. Installation / Account auto-provisioning
    installation_data = payload.get("installation", {})
    inst_gh_id = str(installation_data.get("id") or "") if installation_data else None

    db_inst = None
    if inst_gh_id:
        db_inst = db.query(GithubInstallation).filter(
            GithubInstallation.github_installation_id == inst_gh_id
        ).first()
        if not db_inst:
            account = installation_data.get("account", {}) or payload.get("repository", {}).get("owner", {})
            db_inst = upsert_github_installation(
                db=db,
                installation_data={
                    "id": inst_gh_id,
                    "account": account,
                    "created_at": event_time.isoformat()
                },
                status="active"
            )

    # 2. Repository auto-provisioning
    repo_data = payload.get("repository", {})
    db_repo = None
    if repo_data:
        db_repo = ensure_github_repository(
            db=db,
            repo_data=repo_data,
            installation_db_id=db_inst.id if db_inst else None
        )

    # 3. User & Person auto-provisioning (sync all user identities present in the payload)
    all_users_data = []
    if isinstance(payload.get("sender"), dict) and payload["sender"].get("login"):
        all_users_data.append(payload["sender"])
    if isinstance(payload.get("pull_request", {}).get("user"), dict) and payload["pull_request"]["user"].get("login"):
        all_users_data.append(payload["pull_request"]["user"])
    if isinstance(payload.get("review", {}).get("user"), dict) and payload["review"]["user"].get("login"):
        all_users_data.append(payload["review"]["user"])
    if isinstance(payload.get("comment", {}).get("user"), dict) and payload["comment"]["user"].get("login"):
        all_users_data.append(payload["comment"]["user"])

    primary_user = None
    primary_person = None

    for u_data in all_users_data:
        u_login = u_data.get("login") or u_data.get("username")
        if not u_login:
            continue
        p, _ = identity_service.resolve_identity(
            db=db,
            raw_identity=u_login,
            provider="github",
            email=u_data.get("email")
        )
        gh_u = sync_github_user(
            db=db,
            user_data=u_data,
            person_id=p.id if p else None
        )
        if not primary_user:
            primary_user = gh_u
            primary_person = p

    # If review has specific user, select it as primary user for review context
    if isinstance(payload.get("review", {}).get("user"), dict):
        rev_login = payload["review"]["user"].get("login")
        if rev_login:
            rev_gh_u = db.query(GithubUser).filter(GithubUser.username == rev_login).first()
            if rev_gh_u:
                primary_user = rev_gh_u
                if rev_gh_u.person_id:
                    primary_person = db.query(Person).filter(Person.id == rev_gh_u.person_id).first()

    return {
        "installation": db_inst,
        "repository": db_repo,
        "user": primary_user,
        "person": primary_person,
        "repo_id": db_repo.id if db_repo else None,
        "installation_id": db_inst.id if db_inst else None,
        "user_id": primary_user.id if primary_user else None,
        "person_id": primary_person.id if primary_person else None
    }



