import hashlib
import hmac
import os
from datetime import datetime
from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy.orm import Session

from src.db.session import get_db
from src.models.doc_page import DocPage
from src.models.knowledge_node import KnowledgeNode
from src.models.repository import Repository
from src.models.user import User
from src.models.github_installation import GithubInstallation
from src.schemas.event import EventCreate
from src.services.event_service import event_service
from src.services.identity_service import identity_service
from src.services.change_analyzer_service import change_analyzer_service

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


@router.post("/api/webhooks/github")
@router.post("/api/webhooks/github/")
@router.post("/api/webhook/github")
@router.post("/api/webhook/github/")
@router.post("/webhook/github")
@router.post("/webhooks/github")
async def handle_github_webhook(
    request: Request,
    x_github_event: str | None = Header(None, alias="X-GitHub-Event"),
    x_hub_signature_256: str | None = Header(None, alias="X-Hub-Signature-256"),
    db: Session = Depends(get_db)
):
    """
    Receives and processes GitHub App Webhook events:
    - pull_request: Analyzes diff, generates Change Record, correlates with meeting commitments & identity
    - pull_request_review: Records review feedback, changes requested, or approvals
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

    if event == "ping":
        return {"received": True, "event": "ping", "zen": body.get("zen")}

    # Pull Request Events
    if event == "pull_request":
        action = body.get("action")
        pr = body.get("pull_request", {})
        repo_data = body.get("repository", {})
        repo_name = repo_data.get("name") or "default-repo"
        repo_id = repo_name.lower().replace(" ", "-")

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
            timestamp=datetime.utcnow(),
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
                "html_url": pr.get("html_url")
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
                print(f"[KNOWLEDGE INDEX ERROR] {err}")

        return {
            "received": True,
            "event": "pull_request",
            "action": action,
            "pr_number": pr_number,
            "change_summary": change_record["summary"]
        }

    # Pull Request Review Events
    if event in ("pull_request_review", "pull_request_review_comment"):
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
            timestamp=datetime.utcnow(),
            actor=actor,
            entity_type="pull_request",
            entity_id=f"PR #{pr_number}",
            content=content,
            reference_id=ref_id,
            metadata_json={
                "pr_number": pr_number,
                "review_state": state,
                "action": action
            }
        )
        await event_service.ingest_event(db, event_in)

        return {"received": True, "event": event, "event_type": event_type}

    return {"received": True, "event": event, "status": "acknowledged"}
