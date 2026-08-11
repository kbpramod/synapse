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

router = APIRouter(tags=["Webhooks"])


def verify_github_signature(payload_bytes: bytes, signature_header: str | None) -> bool:
    """Verifies HMAC SHA-256 signature from GitHub webhook request."""
    secret = os.getenv("GITHUB_WEBHOOK_SECRET")
    if not secret:
        # Secret not configured; skip signature check in dev mode
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
    - installation / installation_repositories: Registers connected repos
    - pull_request: Extracts facts from PR merges, generates documentation & knowledge nodes
    - push: Updates repository sync status and commits knowledge
    """
    payload_bytes = await request.body()

    # 1. Verify GitHub Signature if secret is set
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

    # Ping Event
    if event == "ping":
        return {"received": True, "event": "ping", "zen": body.get("zen")}

    # Installation Events (App installed or repositories granted)
    if event in ("installation", "installation_repositories"):
        action = body.get("action")
        repositories = body.get("repositories", []) or body.get("repositories_added", [])
        installation_data = body.get("installation", {})
        installation_id = str(installation_data.get("id")) if installation_data.get("id") else None

        # Look up Tzylo user associated with this GitHub Installation
        target_user_id = None
        target_installation_id = None
        if installation_id:
            github_inst = db.query(GithubInstallation).filter(
                GithubInstallation.installation_id == installation_id
            ).first()
            if github_inst:
                target_user_id = github_inst.user_id
                target_installation_id = github_inst.id

        if not target_user_id:
            # Fallback to current first user if no installation link exists yet
            first_user = db.query(User).first()
            target_user_id = first_user.id if first_user else "system"

        for repo_info in repositories:
            full_name = repo_info.get("full_name") or repo_info.get("name")
            if full_name:
                repo_id = full_name.split("/")[-1].lower().replace(" ", "-")
                existing_repo = db.query(Repository).filter(Repository.id == repo_id).first()
                if not existing_repo:
                    new_repo = Repository(
                        id=repo_id,
                        user_id=target_user_id,
                        github_installation_id=target_installation_id,
                        name=full_name.split("/")[-1],
                        full_name=full_name,
                        status="Ready",
                        last_sync="Just now",
                        knowledge_nodes_count=1,
                        doc_pages_count=1,
                        github_url=f"https://github.com/{full_name}",
                        connected_at=datetime.utcnow().strftime("%Y-%m-%d")
                    )
                    db.add(new_repo)
                else:
                    existing_repo.user_id = target_user_id
                    if target_installation_id:
                        existing_repo.github_installation_id = target_installation_id
                db.commit()

        return {"received": True, "event": event, "action": action, "repos_processed": len(repositories)}

    # Pull Request Events
    if event == "pull_request":
        action = body.get("action")
        pr = body.get("pull_request", {})
        repo_data = body.get("repository", {})
        repo_name = repo_data.get("name") or "default-repo"
        repo_id = repo_name.lower().replace(" ", "-")

        # Extract PR details
        pr_title = pr.get("title", "Updated Architecture")
        pr_body = pr.get("body", "PR details extracted via webhook")
        is_merged = pr.get("merged", False)

        # Update Repository Status
        repo = db.query(Repository).filter(Repository.id == repo_id).first()
        if repo:
            repo.status = "Ready" if is_merged else "Indexing"
            repo.last_sync = "Just now"
            repo.knowledge_nodes_count += 1
            if is_merged:
                repo.doc_pages_count += 1
            db.commit()

            # Create an auto-generated DocPage upon merge
            if is_merged:
                new_doc = DocPage(
                    repository_id=repo.id,
                    title=f"PR #{pr.get('number')}: {pr_title}",
                    content=f"### Summary\n\n{pr_body}\n\n*Merged on {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC*"
                )
                db.add(new_doc)
                db.commit()

        return {"received": True, "event": "pull_request", "action": action, "merged": is_merged}

    # Push Events
    if event == "push":
        repo_data = body.get("repository", {})
        repo_name = repo_data.get("name") or "default-repo"
        repo_id = repo_name.lower().replace(" ", "-")

        repo = db.query(Repository).filter(Repository.id == repo_id).first()
        if repo:
            repo.last_sync = "Just now"
            repo.status = "Ready"
            db.commit()

        return {"received": True, "event": "push", "ref": body.get("ref")}

    return {"received": True, "event": event, "status": "acknowledged"}
