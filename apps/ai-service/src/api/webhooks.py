import hashlib
import hmac
import logging
import os
from datetime import datetime
from typing import Optional, Dict, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy.orm import Session

from src.db.session import get_db
from models.doc_page import DocPage
from models.knowledge_node import KnowledgeNode
from models.repository import Repository, GithubRepository
from models.user import User
from models.github_installation import GithubInstallation
from src.schemas.event import EventCreate
from src.repositories.event_repository import event_repository
from src.services.event_service import event_service
from src.services.identity_service import identity_service
from src.services.change_analyzer_service import change_analyzer_service
from src.services.fact_service import fact_service
from src.services.github_service import (
    parse_github_timestamp,
    extract_installation_repo_info,
    upsert_github_installation,
    upsert_github_repositories,
    deactivate_github_repositories,
    sync_installation_all_repositories,
    fetch_pull_request_files,
    fetch_pull_request_commits,
    post_pull_request_comment,
    record_github_event,
    sync_github_user,
    sync_pull_request,
    sync_commits,
    ensure_github_repository,
    ensure_github_user,
    ensure_github_context,
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
    """
    V1 Pull Request Flow:
    1. Scope: Handles 'opened' PR events.
    2. Idempotency: Deduplicates against existing events to prevent duplicate comments on webhook retries.
    3. Diff Filtering: Fetches changed files/diffs and filters noise (lockfiles, generated dirs, size budget).
    4. AI Change Analyzer: Extracts structured changelog & impact JSON.
    5. Knowledge Storage: Stores EventNode & KnowledgeNode (pgvector RAG).
    6. Automated Comment: Posts structured Tzylo Change Summary to GitHub PR via REST API.
    """
    action = body.get("action")
    pr = body.get("pull_request", {})
    pr_number = pr.get("number", 1)
    pr_title = pr.get("title", "")
    pr_body = pr.get("body") or ""
    actor = pr.get("user", {}).get("login") or "github-user"

    # Only process 'opened' in V1 scope
    if action != "opened":
        logger.info(f"[GITHUB WEBHOOK] Skipping PR #{pr_number} with action='{action}' (V1 scope narrowed to 'opened')")
        return {
            "received": True,
            "event": "pull_request",
            "action": action,
            "pr_number": pr_number,
            "status": "ignored_non_opened_action"
        }

    repo_data = body.get("repository", {})
    repo_name = repo_data.get("name") or "default-repo"
    owner_login = repo_data.get("owner", {}).get("login") if isinstance(repo_data.get("owner"), dict) else repo_name.split("/")[0]

    # Auto-provision installation, repository, user, and person entities
    ctx = ensure_github_context(db=db, payload=body, event_time=event_time)
    db_inst = ctx["installation"]
    db_repo = ctx["repository"]
    gh_user = ctx["user"]
    person = ctx["person"]
    repo_id = ctx["repo_id"]
    inst_id_str = str(body.get("installation", {}).get("id") or "")

    # 1. Idempotency Check: Prevent duplicate processing and multiple comments
    ref_id = f"gh_pr_{repo_id}_{pr_number}_opened"
    existing_event = event_repository.get_by_reference_id(db, reference_id=ref_id, source="github")
    if existing_event:
        logger.info(f"[IDEMPOTENT SKIP] PR #{pr_number} opened event already processed (reference_id={ref_id}).")
        return {
            "received": True,
            "event": "pull_request",
            "action": "opened",
            "pr_number": pr_number,
            "status": "already_processed"
        }

    # 2. Record Immutable GitHub Event Audit Log
    delivery_id = body.get("delivery_id")
    gh_event = record_github_event(
        db=db,
        event_type="pull_request.opened",
        payload=body,
        installation_id=db_inst.id if db_inst else None,
        repository_id=repo_id,
        github_event_id=delivery_id,
        github_created_at=event_time,
        received_at=received_at
    )

    # 3. Upsert Pull Request Entity
    pull_request = sync_pull_request(
        db=db,
        pr_data=pr,
        repository_id=repo_id,
        author_github_user_id=gh_user.id if gh_user else None
    )

    # 5. Fetch PR Changed Files & Diffs from GitHub API (or fallback to webhook payload)
    files_data = []
    commits_data = []
    if inst_id_str:
        files_data = fetch_pull_request_files(
            installation_id=inst_id_str,
            owner=owner_login,
            repo=repo_name,
            pull_number=pr_number
        )
        commits_data = fetch_pull_request_commits(
            installation_id=inst_id_str,
            owner=owner_login,
            repo=repo_name,
            pull_number=pr_number
        )

    if not files_data and "files" in pr:
        files_data = pr.get("files", [])

    if not commits_data and "commits" in body:
        commits_data = body.get("commits", [])

    # If head commit exists in PR object, include it
    head_sha = pr.get("head", {}).get("sha")
    author_data = pr.get("user", {})
    if not commits_data and head_sha:
        commits_data = [{
            "sha": head_sha,
            "commit": {"message": pr_title, "author": {"date": event_time.isoformat()}},
            "author": author_data
        }]

    # 6. Sync Commits
    synced_commits = sync_commits(
        db=db,
        commits_data=commits_data,
        pull_request_id=pull_request.id,
        repository_id=repo_id,
        default_author_user_id=gh_user.id if gh_user else None
    )

    diff_snippet = pr.get("patch") or pr_body

    # 7. Analyze PR through DiffFilterService and AI Change Analyzer
    change_record = await change_analyzer_service.analyze_pr(
        title=pr_title,
        body=pr_body,
        files_data=files_data,
        diff_snippet=diff_snippet
    )

    # 8. Create Discrete Facts (fact_status='ACTIVE', work_status='IN_PROGRESS')
    base_fact_meta = {
        "pr_number": pr_number,
        "pr_title": pr_title,
        "source_branch": pull_request.source_branch,
        "target_branch": pull_request.target_branch,
        "head_sha": pull_request.head_sha,
        "author": actor
    }
    created_facts = await fact_service.create_facts_from_change_record(
        db=db,
        change_record=change_record,
        pull_request_id=pull_request.id,
        repository_id=repo_id,
        person_id=person.id if person else None,
        base_metadata=base_fact_meta
    )

    # 9. Post Automated PR Comment to GitHub
    comment_posted = False
    comment_res = None
    if inst_id_str and change_record.get("comment_markdown"):
        comment_res = post_pull_request_comment(
            installation_id=inst_id_str,
            owner=owner_login,
            repo=repo_name,
            pull_number=pr_number,
            comment_body=change_record["comment_markdown"]
        )
        comment_posted = comment_res.get("success", False)

    # 10. Ingest Event into Timeline Event Pipeline
    content = f"GitHub PR #{pr_number} [opened]: {change_record['summary']}"
    event_in = EventCreate(
        source="github",
        event_type="pr_created",
        timestamp=event_time,
        actor=actor,
        entity_type="pull_request",
        entity_id=f"PR #{pr_number}",
        content=content,
        reference_id=ref_id,
        metadata_json={
            "pr_number": pr_number,
            "pr_id": pull_request.id,
            "repo": repo_name,
            "source_branch": pull_request.source_branch,
            "target_branch": pull_request.target_branch,
            "action": "opened",
            "facts_count": len(created_facts),
            "commits_count": len(synced_commits),
            "change_record": {
                "summary": change_record["summary"],
                "changes": change_record["changes"],
                "impact": change_record["impact"],
                "files_changed": change_record["files_changed"]
            },
            "html_url": pr.get("html_url"),
            "event_time": event_time.isoformat(),
            "received_at": received_at.isoformat(),
            "comment_posted": comment_posted,
            "comment_details": comment_res
        }
    )

    created_node, created = await event_service.ingest_event(db, event_in)

    # 11. Index into Knowledge Base for backward-compatible pgvector RAG
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
        except Exception as err:
            logger.error(f"[KNOWLEDGE INDEX ERROR] {err}")

    db.commit()

    return {
        "received": True,
        "event": "pull_request",
        "action": "opened",
        "pr_id": pull_request.id,
        "pr_number": pr_number,
        "source_branch": pull_request.source_branch,
        "target_branch": pull_request.target_branch,
        "author": actor,
        "github_user_id": gh_user.id if gh_user else None,
        "person_id": person.id if person else None,
        "change_summary": change_record["summary"],
        "facts_count": len(created_facts),
        "commits_count": len(synced_commits),
        "comment_posted": comment_posted,
        "comment_markdown": change_record.get("comment_markdown")
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

    # Auto-provision installation, repository, user, and person entities
    ctx = ensure_github_context(db=db, payload=body, event_time=event_time)
    db_inst = ctx["installation"]
    repo_id = ctx["repo_id"]

    # Record immutable audit log
    delivery_id = body.get("delivery_id")
    record_github_event(
        db=db,
        event_type=f"pull_request_review.{action or state}",
        payload=body,
        installation_id=db_inst.id if db_inst else None,
        repository_id=repo_id,
        github_event_id=delivery_id,
        github_created_at=event_time,
        received_at=received_at
    )

    if state == "approved":
        event_type = "pr_approved"
    elif state == "changes_requested":
        event_type = "changes_requested"
    else:
        event_type = "review_submitted"

    ref_id = f"gh_review_{pr_number}_{review.get('id', int(received_at.timestamp()))}"
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
    db.commit()

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
