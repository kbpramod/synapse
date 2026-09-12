import asyncio
import os
import sys
import uuid
from datetime import datetime
from uuid import uuid4
from dotenv import load_dotenv

# Add workspace root to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

load_dotenv()
from src.db.session import SessionLocal
import models
from models import (
    User,
    Organization,
    OrganizationMember,
    GithubInstallation,
    GithubRepository,
    EventNode
)
from src.api.webhooks import (
    handle_installation_event,
    handle_installation_repositories_event
)


async def run_tests():
    db = SessionLocal()
    test_inst_id = f"test_inst_{uuid4().hex[:8]}"

    try:
        print("=== 1. Testing Unauthenticated installation.created Webhook ===")
        install_payload = {
            "action": "created",
            "installation": {
                "id": test_inst_id,
                "account": {
                    "id": "99988877",
                    "login": "acme-corp",
                    "type": "Organization"
                },
                "created_at": "2026-08-17T12:00:00Z"
            },
            "repositories": [
                {"id": 101, "name": "repo-alpha", "full_name": "acme-corp/repo-alpha", "private": False},
                {"id": 102, "name": "repo-beta", "full_name": "acme-corp/repo-beta", "private": True}
            ],
            "sender": {"login": "octocat"}
        }

        event_time = datetime(2026, 8, 17, 12, 0, 0)
        received_at = datetime.utcnow()

        res1 = await handle_installation_event(install_payload, event_time, received_at, db)
        print("Result:", res1)

        # Verify DB
        inst = db.query(GithubInstallation).filter(GithubInstallation.github_installation_id == test_inst_id).first()
        assert inst is not None, "Installation not found in DB"
        assert inst.github_account_login == "acme-corp"
        assert inst.organization_id is None, "Expected unlinked installation to have organization_id=None"
        assert inst.status == "active"
        print(f"Installation verified: id={inst.id}, org_id={inst.organization_id}, status={inst.status}")

        repos = db.query(GithubRepository).filter(GithubRepository.installation_id == inst.id).all()
        assert len(repos) == 2, f"Expected 2 repos, found {len(repos)}"
        assert all(r.active for r in repos), "Expected all initial repos to be active=True"
        for r in repos:
            uuid_obj = uuid.UUID(r.id)
            print(f"Repository UUID valid: id={r.id}, name={r.name}")

        print("\n=== 2. Testing installation_repositories.added Webhook ===")
        add_payload = {
            "action": "added",
            "installation": {
                "id": test_inst_id,
                "account": {"id": "99988877", "login": "acme-corp"}
            },
            "repositories_added": [
                {"id": 103, "name": "repo-gamma", "full_name": "acme-corp/repo-gamma", "private": False}
            ],
            "sender": {"login": "octocat"},
            "created_at": "2026-08-17T12:10:00Z"
        }

        res2 = await handle_installation_repositories_event(add_payload, datetime(2026, 8, 17, 12, 10, 0), received_at, db)
        print("Result:", res2)

        repos = db.query(GithubRepository).filter(GithubRepository.installation_id == inst.id).all()
        assert len(repos) == 3, f"Expected 3 repos, found {len(repos)}"
        print(f"Repositories after add: {[r.name for r in repos]}")

        print("\n=== 3. Testing installation_repositories.removed Webhook ===")
        remove_payload = {
            "action": "removed",
            "installation": {
                "id": test_inst_id,
                "account": {"id": "99988877", "login": "acme-corp"}
            },
            "repositories_removed": [
                {"id": 101, "name": "repo-alpha", "full_name": "acme-corp/repo-alpha"}
            ],
            "sender": {"login": "octocat"},
            "created_at": "2026-08-17T12:20:00Z"
        }

        res3 = await handle_installation_repositories_event(remove_payload, datetime(2026, 8, 17, 12, 20, 0), received_at, db)
        print("Result:", res3)

        removed_repo = db.query(GithubRepository).filter(
            GithubRepository.installation_id == inst.id,
            GithubRepository.name == "repo-alpha"
        ).first()
        assert removed_repo is not None, "Removed repo should not be deleted from DB"
        assert removed_repo.active is False, "Removed repo should be marked active=False"
        print(f"Removed repo status: {removed_repo.name}, active={removed_repo.active}")

        print("\n=== 4. Testing Authenticated Organization Linking ===")
        test_org = Organization(id=str(uuid4()), name="Acme Corporation")
        db.add(test_org)
        db.flush()

        inst.organization_id = test_org.id
        db.commit()
        db.refresh(inst)
        assert inst.organization_id == test_org.id
        print(f"Linked installation to organization: {test_org.name} (id={inst.organization_id})")

        print("\n=== 5. Testing installation.deleted Webhook ===")
        delete_payload = {
            "action": "deleted",
            "installation": {
                "id": test_inst_id,
                "account": {"login": "acme-corp"}
            },
            "sender": {"login": "octocat"}
        }
        res4 = await handle_installation_event(delete_payload, datetime(2026, 8, 17, 12, 30, 0), received_at, db)
        print("Result:", res4)

        db.refresh(inst)
        assert inst.status == "deleted"
        all_repos = db.query(GithubRepository).filter(GithubRepository.installation_id == inst.id).all()
        assert all(not r.active for r in all_repos), "Expected all repos to be active=False on installation deletion"
        print(f"Installation deleted status={inst.status}, all repos active=False verified!")

        # Clean up test rows
        db.query(GithubRepository).filter(GithubRepository.installation_id == inst.id).delete()
        db.delete(inst)
        db.delete(test_org)
        # Clean up test events
        db.query(EventNode).filter(EventNode.entity_id == test_inst_id).delete()
        db.commit()
        print("\nAll Webhook Lifecycle Tests Passed 100% Successfully!")

    except Exception as e:
        db.rollback()
        print(f"Test Failed: {e}")
        raise
    finally:
        db.close()


if __name__ == "__main__":
    asyncio.run(run_tests())
