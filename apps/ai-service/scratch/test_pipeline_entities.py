import asyncio
import os
import sys
from datetime import datetime
from uuid import uuid4
from dotenv import load_dotenv

# Add workspace root to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

load_dotenv()
from src.db.database import SessionLocal
import models
from models import (
    User,
    Organization,
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
from src.api.webhooks import handle_pull_request_event


async def run_pipeline_entity_tests():
    db = SessionLocal()

    try:
        print("=== 1. Setting Up Test Installation & Repository ===")
        test_inst_id = f"inst_test_{uuid4().hex[:8]}"
        test_repo_id = str(uuid4())
        gh_repo_num_id = "98765432"

        inst = GithubInstallation(
            id=str(uuid4()),
            github_installation_id=test_inst_id,
            github_account_id="111222",
            github_account_login="acme-corp",
            github_account_type="Organization",
            status="active"
        )
        db.add(inst)

        repo = GithubRepository(
            id=test_repo_id,
            installation_id=inst.id,
            github_repository_id=gh_repo_num_id,
            name="discussions-api",
            full_name="acme-corp/discussions-api",
            private=False,
            active=True
        )
        db.add(repo)
        db.commit()
        print(f"Created Test Repo: id={repo.id}, full_name={repo.full_name}")

        print("\n=== 2. Testing PR Opened Webhook with Full Entity Pipeline ===")
        pr_number = 182
        sample_pr_payload = {
            "action": "opened",
            "number": pr_number,
            "delivery_id": f"deliv_{uuid4().hex[:8]}",
            "installation": {"id": test_inst_id},
            "pull_request": {
                "id": 88877766,
                "number": pr_number,
                "title": "Add Redis caching for Discussions API",
                "body": "Introduces Redis cache for reads and handles cache invalidation when discussions are updated.",
                "created_at": "2026-08-17T14:30:00Z",
                "updated_at": "2026-08-17T14:30:00Z",
                "head": {
                    "ref": "feature/add-redis-cache",
                    "sha": "abc1234567890abcdef1234567890abcdef12"
                },
                "base": {
                    "ref": "develop",
                    "sha": "def4567890abcdef1234567890abcdef123456"
                },
                "user": {
                    "id": 554433,
                    "login": "smartcoder",
                    "name": "Pramod SmartCoder",
                    "email": "pramod@smartcoder.dev",
                    "avatar_url": "https://avatars.githubusercontent.com/u/554433"
                },
                "files": [
                    {
                        "filename": "src/discussions/service.ts",
                        "patch": "@@ -10,3 +10,12 @@\n+import { redisClient } from '../cache/redis';\n+export async function getDiscussion(id: string) { ... }",
                        "additions": 12,
                        "deletions": 0
                    },
                    {
                        "filename": "src/cache/redis.ts",
                        "patch": "@@ -0,0 +1,30 @@\n+export class RedisCache { ... }",
                        "additions": 30,
                        "deletions": 0
                    },
                    {
                        "filename": "package-lock.json",
                        "patch": "@@ -1,500 +1,550 @@\n+ lockfile diff noise ...",
                        "additions": 50,
                        "deletions": 0
                    }
                ],
                "html_url": "https://github.com/acme-corp/discussions-api/pull/182"
            },
            "commits": [
                {
                    "sha": "abc1234567890abcdef1234567890abcdef12",
                    "commit": {
                        "message": "feat: add RedisCache implementation and integrate discussion reads",
                        "author": {"date": "2026-08-17T14:25:00Z"}
                    },
                    "author": {
                        "id": 554433,
                        "login": "smartcoder"
                    }
                }
            ],
            "repository": {
                "id": gh_repo_num_id,
                "name": "discussions-api",
                "full_name": "acme-corp/discussions-api",
                "owner": {"login": "acme-corp"}
            },
            "sender": {"login": "smartcoder"}
        }

        event_time = datetime(2026, 8, 17, 14, 30, 0)
        received_at = datetime.utcnow()

        result = await handle_pull_request_event(sample_pr_payload, event_time, received_at, db)
        print("Webhook Pipeline Result:", result)

        print("\n=== 3. Verifying All 5 Separate Database Entities ===")

        # 1. Verify github_events
        gh_ev = db.query(GithubEvent).filter(
            GithubEvent.repository_id == test_repo_id,
            GithubEvent.event_type == "pull_request.opened"
        ).first()
        assert gh_ev is not None, "Expected GithubEvent to be recorded in github_events"
        assert gh_ev.github_event_id == sample_pr_payload["delivery_id"]
        assert "pull_request" in gh_ev.payload
        print(f"[PASSED] 1. github_events: id={gh_ev.id}, event_type={gh_ev.event_type}, delivery_id={gh_ev.github_event_id}")

        # 2. Verify github_users
        gh_u = db.query(GithubUser).filter(GithubUser.username == "smartcoder").first()
        assert gh_u is not None, "Expected GithubUser to be created in github_users"
        assert gh_u.github_user_id == "554433"
        assert gh_u.person_id is not None, "Expected GithubUser to be linked to a Person"
        person = db.query(Person).filter(Person.id == gh_u.person_id).first()
        assert person is not None
        print(f"[PASSED] 2. github_users: id={gh_u.id}, username={gh_u.username} -> Person: id={person.id}, name={person.canonical_name}")

        # 3. Verify pull_requests
        pr_entity = db.query(PullRequest).filter(
            PullRequest.repository_id == test_repo_id,
            PullRequest.number == pr_number
        ).first()
        assert pr_entity is not None, "Expected PullRequest entity in pull_requests"
        assert pr_entity.source_branch == "feature/add-redis-cache"
        assert pr_entity.target_branch == "develop"
        assert pr_entity.head_sha == "abc1234567890abcdef1234567890abcdef12"
        assert pr_entity.base_sha == "def4567890abcdef1234567890abcdef123456"
        assert pr_entity.status == "open"
        assert pr_entity.author_github_user_id == gh_u.id
        print(f"[PASSED] 3. pull_requests: id={pr_entity.id}, PR #{pr_entity.number} [{pr_entity.source_branch} -> {pr_entity.target_branch}], head_sha={pr_entity.head_sha[:8]}")

        # 4. Verify commits
        commits = db.query(Commit).filter(Commit.pull_request_id == pr_entity.id).all()
        assert len(commits) >= 1, "Expected commits to be recorded in commits table"
        commit = commits[0]
        assert commit.github_commit_sha == "abc1234567890abcdef1234567890abcdef12"
        assert commit.author_github_user_id == gh_u.id
        print(f"[PASSED] 4. commits: count={len(commits)}, first sha={commit.github_commit_sha[:8]}, message='{commit.message[:35]}...'")

        # 5. Verify facts
        facts = db.query(Fact).filter(
            Fact.source_type == "github_pr",
            Fact.source_id == pr_entity.id
        ).all()
        assert len(facts) >= 2, f"Expected at least 2 facts (granular + summary), found {len(facts)}"
        for f in facts:
            assert f.fact_status == "ACTIVE", f"Expected fact_status='ACTIVE', got {f.fact_status}"
            assert f.work_status == "IN_PROGRESS", f"Expected work_status='IN_PROGRESS', got {f.work_status}"
            assert f.person_id == person.id
            assert f.repository_id == test_repo_id
            assert f.embedding is not None, "Expected fact embedding vector to be computed"
            print(f"    - Fact: id={f.id[:8]}, fact_status={f.fact_status}, work_status={f.work_status}, content='{f.content[:50]}...'")

        print(f"[PASSED] 5. facts: count={len(facts)} with vector embeddings and separated status")

        print("\n=== 4. Testing Idempotency on Re-delivery ===")
        dup_result = await handle_pull_request_event(sample_pr_payload, event_time, received_at, db)
        assert dup_result["status"] == "already_processed"
        print("[PASSED] Idempotency: Duplicate delivery ignored safely.")

        # Clean up test rows
        db.query(Fact).filter(Fact.source_id == pr_entity.id).delete()
        db.query(Commit).filter(Commit.pull_request_id == pr_entity.id).delete()
        db.query(PullRequest).filter(PullRequest.id == pr_entity.id).delete()
        db.query(GithubEvent).filter(GithubEvent.repository_id == test_repo_id).delete()
        db.query(KnowledgeNode).filter(KnowledgeNode.repository_id == test_repo_id).delete()
        db.query(EventNode).filter(EventNode.reference_id == f"gh_pr_{test_repo_id}_{pr_number}_opened").delete()
        db.delete(repo)
        db.delete(inst)
        db.commit()

        print("\nAll 5-Entity Pipeline Tests Passed 100% Successfully!")

    except Exception as e:
        db.rollback()
        print(f"Pipeline Entity Test Failed: {e}")
        raise
    finally:
        db.close()


if __name__ == "__main__":
    asyncio.run(run_pipeline_entity_tests())
