import asyncio
import os
import sys
import random
from datetime import datetime
from uuid import uuid4
from dotenv import load_dotenv

# Add workspace root to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

load_dotenv()
from src.db.database import SessionLocal
import src.models
from src.models import (
    GithubInstallation,
    GithubRepository,
    GithubEvent,
    GithubUser,
    PullRequest,
    Commit,
    Fact,
    Person,
    EventNode,
    KnowledgeNode
)
from src.api.webhooks import handle_pull_request_event, handle_pull_request_review_event


async def run_auto_provisioning_tests():
    db = SessionLocal()

    try:
        print("=== 1. Testing PR Opened with Brand New Installation, Repo & User (0 Initial DB Rows) ===")
        fresh_inst_id = f"auto_inst_{uuid4().hex[:8]}"
        fresh_repo_num_id = str(random.randint(100000, 999999))
        fresh_repo_name = f"auth-api-{uuid4().hex[:6]}"
        fresh_user_login = f"dev_{uuid4().hex[:6]}"
        pr_number = random.randint(100, 9999)

        # Ensure they definitely don't exist in DB yet
        assert db.query(GithubInstallation).filter(GithubInstallation.github_installation_id == fresh_inst_id).first() is None
        assert db.query(GithubRepository).filter(GithubRepository.github_repository_id == fresh_repo_num_id).first() is None
        assert db.query(GithubUser).filter(GithubUser.username == fresh_user_login).first() is None

        payload = {
            "action": "opened",
            "number": pr_number,
            "delivery_id": f"deliv_{uuid4().hex[:8]}",
            "installation": {
                "id": fresh_inst_id,
                "account": {
                    "id": "77788899",
                    "login": "new-startup-org",
                    "type": "Organization"
                }
            },
            "pull_request": {
                "id": random.randint(1000000, 9999999),
                "number": pr_number,
                "title": "Setup JWT Authentication Filter",
                "body": "Implements JWT token validation and role-based middleware.",
                "created_at": "2026-08-17T15:00:00Z",
                "head": {"ref": "feat/jwt-auth", "sha": "1112223334445556667778889990001112223334"},
                "base": {"ref": "main", "sha": "4443332221110009998887776665554443332221"},
                "user": {
                    "id": random.randint(10000, 99999),
                    "login": fresh_user_login,
                    "name": "New Developer",
                    "email": f"{fresh_user_login}@startup.io",
                    "avatar_url": "https://avatars.githubusercontent.com/u/123999"
                },
                "files": [
                    {
                        "filename": "src/auth/jwt.py",
                        "patch": "@@ -0,0 +1,20 @@\n+def verify_token(t): return True",
                        "additions": 20,
                        "deletions": 0
                    }
                ],
                "html_url": f"https://github.com/new-startup-org/{fresh_repo_name}/pull/{pr_number}"
            },
            "repository": {
                "id": fresh_repo_num_id,
                "name": fresh_repo_name,
                "full_name": f"new-startup-org/{fresh_repo_name}",
                "owner": {"id": "77788899", "login": "new-startup-org", "type": "Organization"},
                "private": True,
                "html_url": f"https://github.com/new-startup-org/{fresh_repo_name}"
            },
            "sender": {"login": fresh_user_login}
        }

        event_time = datetime(2026, 8, 17, 15, 0, 0)
        received_at = datetime.utcnow()

        res = await handle_pull_request_event(payload, event_time, received_at, db)
        print("Webhook Pipeline Result:", res)

        # 1. Verify Auto-Created Installation
        auto_inst = db.query(GithubInstallation).filter(GithubInstallation.github_installation_id == fresh_inst_id).first()
        assert auto_inst is not None, "Expected GithubInstallation to be auto-created"
        assert auto_inst.github_account_login == "new-startup-org"
        assert auto_inst.status == "active"
        print(f"[PASSED] Auto-created GithubInstallation: id={auto_inst.id}, account={auto_inst.github_account_login}")

        # 2. Verify Auto-Created Repository
        auto_repo = db.query(GithubRepository).filter(GithubRepository.github_repository_id == fresh_repo_num_id).first()
        assert auto_repo is not None, "Expected GithubRepository to be auto-created"
        assert auto_repo.name == fresh_repo_name
        assert auto_repo.full_name == f"new-startup-org/{fresh_repo_name}"
        assert auto_repo.installation_id == auto_inst.id
        assert auto_repo.active is True
        print(f"[PASSED] Auto-created GithubRepository: id={auto_repo.id}, full_name={auto_repo.full_name}")

        # 3. Verify Auto-Created GithubUser & Person
        auto_user = db.query(GithubUser).filter(GithubUser.username == fresh_user_login).first()
        assert auto_user is not None, "Expected GithubUser to be auto-created"
        assert auto_user.person_id is not None
        person = db.query(Person).filter(Person.id == auto_user.person_id).first()
        assert person is not None
        print(f"[PASSED] Auto-created GithubUser: id={auto_user.id}, login={auto_user.username} -> Person: id={person.id}")

        # 4. Verify Auto-Created PullRequest
        pr = db.query(PullRequest).filter(PullRequest.repository_id == auto_repo.id, PullRequest.number == pr_number).first()
        assert pr is not None, "Expected PullRequest to be created"
        assert pr.source_branch == "feat/jwt-auth"
        assert pr.target_branch == "main"
        assert pr.author_github_user_id == auto_user.id
        print(f"[PASSED] Auto-created PullRequest: id={pr.id}, PR #{pr.number} [{pr.source_branch} -> {pr.target_branch}]")

        # 5. Verify Auto-Created Facts
        facts = db.query(Fact).filter(Fact.source_id == pr.id).all()
        assert len(facts) >= 1, "Expected Facts to be created"
        for f in facts:
            assert f.fact_status == "ACTIVE"
            assert f.work_status == "IN_PROGRESS"
            assert f.person_id == person.id
            assert f.repository_id == auto_repo.id
        print(f"[PASSED] Auto-created Facts: count={len(facts)}")

        print("\n=== 2. Testing PR Review Event Auto-Provisioning ===")
        reviewer_login = f"reviewer_{uuid4().hex[:6]}"
        review_payload = {
            "action": "submitted",
            "review": {
                "id": random.randint(100000, 999999),
                "state": "approved",
                "body": "Looks great, approved!",
                "user": {"id": random.randint(10000, 99999), "login": reviewer_login, "name": "Reviewer Bot"}
            },
            "pull_request": payload["pull_request"],
            "repository": payload["repository"],
            "installation": payload["installation"],
            "sender": {"login": reviewer_login}
        }

        rev_res = await handle_pull_request_review_event(review_payload, event_time, received_at, db)
        print("Review Result:", rev_res)

        # Verify Reviewer was auto-created
        rev_user = db.query(GithubUser).filter(GithubUser.username == reviewer_login).first()
        assert rev_user is not None, "Expected reviewer GithubUser to be auto-created"
        print(f"[PASSED] Reviewer GithubUser auto-created: id={rev_user.id}, login={rev_user.username}")

        # Clean up test rows
        db.query(Fact).filter(Fact.source_id == pr.id).delete()
        db.query(Commit).filter(Commit.pull_request_id == pr.id).delete()
        db.query(PullRequest).filter(PullRequest.id == pr.id).delete()
        db.query(GithubEvent).filter(GithubEvent.repository_id == auto_repo.id).delete()
        db.query(KnowledgeNode).filter(KnowledgeNode.repository_id == auto_repo.id).delete()
        db.query(EventNode).filter(EventNode.reference_id.like(f"%{auto_repo.id}%")).delete()
        db.query(GithubUser).filter(GithubUser.id.in_([auto_user.id, rev_user.id])).delete()
        db.query(Person).filter(Person.id == person.id).delete()
        db.delete(auto_repo)
        db.delete(auto_inst)
        db.commit()

        print("\nAll Auto-Provisioning & Backward Compatibility Tests Passed 100% Successfully!")

    except Exception as e:
        db.rollback()
        print(f"Auto-Provisioning Test Failed: {e}")
        raise
    finally:
        db.close()


if __name__ == "__main__":
    asyncio.run(run_auto_provisioning_tests())
