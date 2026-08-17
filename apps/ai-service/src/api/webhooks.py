import hashlib
import hmac
import logging
import os
from datetime import datetime
from typing import Optional, Dict, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy.orm import Session

from src.db.session import get_db
from src.models.doc_page import DocPage
from src.models.knowledge_node import KnowledgeNode
from src.models.repository import Repository, GithubRepository
from src.models.user import User
from src.models.github_installation import GithubInstallation
from src.schemas.event import EventCreate
from src.services.event_service import event_service
from src.services.identity_service import identity_service
from src.services.change_analyzer_service import change_analyzer_service
from src.services.github_service import (
    parse_github_timestamp,
    extract_installation_repo_info,
    upsert_github_installation,
    upsert_github_repositories,
    deactivate_github_repositories,
    sync_installation_all_repositories,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Webhooks"])


def verify_github_signature(payload_bytes: bytes, signature_header: str | None) -> bool:
    """Verifies HMAC SHA-256 signature from GitHub webhook request."""
    secret = os.getenv("GITHUB_WEBHOOK_SECRET")
    if not secret:
        return True

    if not signature_header or not signature_header.startswith("sha256="):
        return False

    expected_signature = hmac.new(
        secret.encode("utf-8"),
        payload_bytes,
        hashlib.sha256
    ).hexdigest()

    received_signature = signature_header.split("sha256=")[1]
    return hmac.compare_digest(expected_signature, received_signature)


async def handle_installation_event(
    body: dict,
    event_time: datetime,
    received_at: datetime,
    db: Session
) -> Dict[str, Any]:
    """
    Handles the 'installation' webhook event (initial app install, uninstall, suspend, unsuspend).
    Synchronizes all accessible repositories via GitHub Installation API.
    Does NOT require a pre-existing authenticated Tzylo user.
    """
    action = body.get("action")
    installation_data = body.get("installation", {})
    inst_id_str = str(installation_data.get("id"))
    account = installation_data.get("account", {})
    account_login = account.get("login") or "unknown"
    sender = body.get("sender", {}).get("login") or "github"

    logger.info(f"[GITHUB WEBHOOK] installation event: action={action}, installation_id={inst_id_str}, account={account_login}")

    if action == "created":
        # 1. Upsert installation in github_installations table
        installation = upsert_github_installation(
            db=db,
            installation_data=installation_data,
            status="active"
        )

        # 2. Perform initial repository synchronization via GitHub REST API
        payload_repos = body.get("repositories", [])
        synced_repos = sync_installation_all_repositories(
            db=db,
            installation=installation,
            payload_repos=payload_repos
        )
        db.commit()

        # 3. Emit Event Pipeline Record with temporal timestamps
        ref_id = f"gh_inst_{inst_id_str}_created"
        content = f"GitHub App installed by {account_login} ({len(synced_repos)} repositories synced)"

        event_in = EventCreate(
            source="github",
            event_type="installation_created",
            timestamp=event_time,
            actor=sender,
            entity_type="installation",
            entity_id=inst_id_str,
            content=content,
            reference_id=ref_id,
            metadata_json={
                "action": action,
                "installation_id": inst_id_str,
                "account": account_login,
                "repositories_count": len(synced_repos),
                "repositories": [r.name for r in synced_repos],
                "event_time": event_time.isoformat(),
                "received_at": received_at.isoformat()
            }
        )
        await event_service.ingest_event(db, event_in)

        return {
            "received": True,
            "event": "installation",
            "action": action,
            "installation_id": inst_id_str,
            "repositories_synced": len(synced_repos)
        }

    elif action == "deleted":
        # Mark installation and associated repositories inactive
        installation = db.query(GithubInstallation).filter(
            GithubInstallation.github_installation_id == inst_id_str
        ).first()

        if installation:
            installation.status = "deleted"
            installation.uninstalled_at = event_time

            # Deactivate all repositories for this installation
            db.query(GithubRepository).filter(
                GithubRepository.installation_id == installation.id
            ).update({"active": False})
            db.commit()

        # Emit deletion event
        ref_id = f"gh_inst_{inst_id_str}_deleted"
        event_in = EventCreate(
            source="github",
            event_type="installation_deleted",
            timestamp=event_time,
            actor=sender,
            entity_type="installation",
            entity_id=inst_id_str,
            content=f"GitHub App uninstalled by {account_login}",
            reference_id=ref_id,
            metadata_json={
                "action": action,
                "installation_id": inst_id_str,
                "account": account_login,
                "event_time": event_time.isoformat(),
                "received_at": received_at.isoformat()
            }
        )
        await event_service.ingest_event(db, event_in)

        return {
            "received": True,
            "event": "installation",
            "action": action,
            "installation_id": inst_id_str
        }

    elif action in ("suspend", "unsuspend"):
        is_active = (action == "unsuspend")
        new_status = "active" if is_active else "suspended"

        installation = db.query(GithubInstallation).filter(
            GithubInstallation.github_installation_id == inst_id_str
        ).first()

        if installation:
            installation.status = new_status
            db.query(GithubRepository).filter(
                GithubRepository.installation_id == installation.id
            ).update({"active": is_active})
            db.commit()

        ref_id = f"gh_inst_{inst_id_str}_{action}"
        event_in = EventCreate(
            source="github",
            event_type=f"installation_{action}",
            timestamp=event_time,
            actor=sender,
            entity_type="installation",
            entity_id=inst_id_str,
            content=f"GitHub App {action}ed by {account_login}",
            reference_id=ref_id,
            metadata_json={
                "action": action,
                "installation_id": inst_id_str,
                "status": new_status,
                "event_time": event_time.isoformat(),
                "received_at": received_at.isoformat()
            }
        )
        await event_service.ingest_event(db, event_in)

        return {
            "received": True,
            "event": "installation",
            "action": action,
            "status": new_status
        }

    return {"received": True, "event": "installation", "action": action, "status": "acknowledged"}


async def handle_installation_repositories_event(
    body: dict,
    event_time: datetime,
    received_at: datetime,
    db: Session
) -> Dict[str, Any]:
    """
    Handles 'installation_repositories' events (incremental sync when repos are added or removed
    from an existing GitHub installation).
    """
    info = extract_installation_repo_info(body)
    if not info:
        return {"received": True, "event": "installation_repositories", "status": "ignored"}

    action = info["action"]
    inst_id_str = info["installationId"]
    account_login = info["account"] or "unknown"
    sender = info["sender"] or "github"

    logger.info(f"[GITHUB WEBHOOK] installation_repositories event: action={action}, installation_id={inst_id_str}, repos_count={len(info['repositories'])}")

    # 1. Ensure installation record exists
    installation = upsert_github_installation(
        db=db,
        installation_data=body.get("installation", {}),
        status="active"
    )

    # 2. Incremental Sync
    if action == "added":
        updated_repos = upsert_github_repositories(
            db=db,
            installation_db_id=installation.id,
            repos_data=info["repositories"],
            active=True
        )
        db.commit()

        ref_id = f"gh_inst_repos_{inst_id_str}_{action}_{int(received_at.timestamp())}"
        content = f"GitHub Repositories added ({len(updated_repos)}): {', '.join(r.name for r in updated_repos)}"

        event_in = EventCreate(
            source="github",
            event_type="installation_repositories_added",
            timestamp=event_time,
            actor=sender,
            entity_type="installation",
            entity_id=inst_id_str,
            content=content,
            reference_id=ref_id,
            metadata_json={
                "action": action,
                "installation_id": inst_id_str,
                "account": account_login,
                "repositories": [r.name for r in updated_repos],
                "event_time": event_time.isoformat(),
                "received_at": received_at.isoformat()
            }
        )
        await event_service.ingest_event(db, event_in)

        return {
            "received": True,
            "event": "installation_repositories",
            "action": "added",
            "count": len(updated_repos)
        }

    elif action == "removed":
        # Mark removed repositories as inactive instead of deleting to preserve history
        deactivated = deactivate_github_repositories(
            db=db,
            installation_db_id=installation.id,
            repos_data=info["repositories"]
        )
        db.commit()

        ref_id = f"gh_inst_repos_{inst_id_str}_{action}_{int(received_at.timestamp())}"
        content = f"GitHub Repositories removed ({len(deactivated)} marked inactive): {', '.join(r.name for r in deactivated)}"

        event_in = EventCreate(
            source="github",
            event_type="installation_repositories_removed",
            timestamp=event_time,
            actor=sender,
            entity_type="installation",
            entity_id=inst_id_str,
            content=content,
            reference_id=ref_id,
            metadata_json={
                "action": action,
                "installation_id": inst_id_str,
                "account": account_login,
                "repositories": [r.name for r in deactivated],
                "event_time": event_time.isoformat(),
                "received_at": received_at.isoformat()
            }
        )
        await event_service.ingest_event(db, event_in)

        return {
            "received": True,
            "event": "installation_repositories",
            "action": "removed",
            "count": len(deactivated)
        }

    return {"received": True, "event": "installation_repositories", "action": action, "status": "acknowledged"}


async def handle_pull_request_event(
    body: dict,
    event_time: datetime,
    received_at: datetime,
    db: Session
) -> Dict[str, Any]:
    """Handles pull_request events: diff analysis, change record, identity resolution, knowledge embedding."""
    action = body.get("action")
    pr = body.get("pull_request", {})
    repo_data = body.get("repository", {})
    repo_name = repo_data.get("name") or "default-repo"
    repo_gh_id = str(repo_data.get("id")) if repo_data.get("id") else None
    repo_full_name = repo_data.get("full_name") or repo_name

    db_repo = db.query(GithubRepository).filter(
        (GithubRepository.github_repository_id == repo_gh_id) |
        (GithubRepository.full_name == repo_full_name) |
        (GithubRepository.name == repo_name)
    ).first()
    repo_id = db_repo.id if db_repo else repo_name.lower().replace(" ", "-")

    pr_number = pr.get("number", 1)
    pr_title = pr.get("title", "")
    pr_body = pr.get("body") or ""
    is_merged = pr.get("merged", False)
    actor = pr.get("user", {}).get("login") or "github-user"

    # Resolve actor identity
    person, identity_link = identity_service.resolve_identity(
        db=db,
        raw_identity=actor,
        provider="github"
    )

    # Classify PR event_type
    if action == "opened":
        event_type = "pr_created"
    elif action == "closed" and is_merged:
        event_type = "pr_merged"
    elif action == "closed" and not is_merged:
        event_type = "pr_closed"
    else:
        event_type = f"pr_{action}"

    # Analyze PR diff and construct Change Record
    changed_files = [f.get("filename") for f in pr.get("files", [])] if "files" in pr else []
    diff_snippet = pr.get("patch") or pr_body

    change_record = await change_analyzer_service.analyze_pr(
        title=pr_title,
        body=pr_body,
        changed_files=changed_files,
        diff_snippet=diff_snippet
    )

    ref_id = f"gh_pr_{repo_id}_{pr_number}_{action}"
    content = f"GitHub PR #{pr_number} [{event_type}]: {change_record['summary']}"

    event_in = EventCreate(
        source="github",
        event_type=event_type,
        timestamp=event_time,
        actor=actor,
        entity_type="pull_request",
        entity_id=f"PR #{pr_number}",
        content=content,
        reference_id=ref_id,
        metadata_json={
            "pr_number": pr_number,
            "repo": repo_name,
            "action": action,
            "merged": is_merged,
            "change_record": {
                "summary": change_record["summary"],
                "changes": change_record["changes"],
                "areas": change_record["areas"]
            },
            "html_url": pr.get("html_url"),
            "event_time": event_time.isoformat(),
            "received_at": received_at.isoformat()
        }
    )

    created_node, created = await event_service.ingest_event(db, event_in)

    # Index into Knowledge Base for pgvector RAG
    if created and change_record.get("embedding"):
        try:
            kn = KnowledgeNode(
                repository_id=repo_id,
                section="GitHub PRs",
                topic=f"PR #{pr_number} - {pr_title}",
                fact=change_record["text_representation"],
                embedding=change_record["embedding"],
                source="github",
                event_id=created_node.id
            )
            db.add(kn)
            db.commit()
        except Exception as err:
            db.rollback()
            logger.error(f"[KNOWLEDGE INDEX ERROR] {err}")

    return {
        "received": True,
        "event": "pull_request",
        "action": action,
        "pr_number": pr_number,
        "change_summary": change_record["summary"]
    }


async def handle_pull_request_review_event(
    body: dict,
    event_time: datetime,
    received_at: datetime,
    db: Session
) -> Dict[str, Any]:
    """Handles pull_request_review and pull_request_review_comment events."""
    action = body.get("action")
    review = body.get("review", {})
    pr = body.get("pull_request", {})
    pr_number = pr.get("number", 1)
    state = review.get("state", "").lower()
    actor = review.get("user", {}).get("login") or "reviewer"

    if state == "approved":
        event_type = "pr_approved"
    elif state == "changes_requested":
        event_type = "changes_requested"
    else:
        event_type = "review_submitted"

    ref_id = f"gh_review_{pr_number}_{review.get('id', datetime.utcnow().timestamp())}"
    content = f"GitHub Review on PR #{pr_number} [{event_type}]: {review.get('body', state)}"

    event_in = EventCreate(
        source="github",
        event_type=event_type,
        timestamp=event_time,
        actor=actor,
        entity_type="pull_request",
        entity_id=f"PR #{pr_number}",
        content=content,
        reference_id=ref_id,
        metadata_json={
            "pr_number": pr_number,
            "review_state": state,
            "action": action,
            "event_time": event_time.isoformat(),
            "received_at": received_at.isoformat()
        }
    )
    await event_service.ingest_event(db, event_in)

    return {"received": True, "event": "pull_request_review", "event_type": event_type}



@router.post("/github")
async def handle_github_webhook(
    request: Request,
    x_github_event: str | None = Header(None, alias="X-GitHub-Event"),
    x_hub_signature_256: str | None = Header(None, alias="X-Hub-Signature-256"),
    db: Session = Depends(get_db)
):
    """
    Primary GitHub Webhook Endpoint:
    - installation: Initial install sync (via GitHub REST API) & lifecycle events
    - installation_repositories: Incremental additions and removals of repositories
    - pull_request: PR diff analysis, change record, identity & meeting correlation
    - pull_request_review: PR review approvals, requests, and comments
    """
    payload_bytes = await request.body()

    if not verify_github_signature(payload_bytes, x_hub_signature_256):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid GitHub webhook signature"
        )

    try:
        body = await request.json()
    except Exception:
        body = {}

    event = x_github_event or "ping"
    received_at = datetime.utcnow()

    # Extract GitHub's event timestamp or fallback to received_at
    event_time_str = (
        body.get("created_at")
        or body.get("pull_request", {}).get("updated_at")
        or body.get("pull_request", {}).get("created_at")
        or body.get("review", {}).get("submitted_at")
        or body.get("installation", {}).get("created_at")
    )
    event_time = parse_github_timestamp(event_time_str)

    if event == "ping":
        return {"received": True, "event": "ping", "zen": body.get("zen")}

    elif event == "installation":
        return await handle_installation_event(body, event_time, received_at, db)

    elif event == "installation_repositories":
        return await handle_installation_repositories_event(body, event_time, received_at, db)

    elif event == "pull_request":
        return await handle_pull_request_event(body, event_time, received_at, db)

    elif event in ("pull_request_review", "pull_request_review_comment"):
        return await handle_pull_request_review_event(body, event_time, received_at, db)

    return {"received": True, "event": event, "status": "acknowledged"}
