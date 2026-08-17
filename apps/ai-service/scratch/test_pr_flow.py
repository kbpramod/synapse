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
from src.db.session import SessionLocal
import src.models
from src.models import (
    User,
    Organization,
    GithubInstallation,
    GithubRepository,
    EventNode,
    KnowledgeNode
)
from src.services.diff_filter_service import diff_filter_service
from src.services.change_analyzer_service import change_analyzer_service
from src.api.webhooks import handle_pull_request_event


async def run_pr_tests():
    db = SessionLocal()

    try:
        print("=== 1. Testing Diff Filter Service ===")
        sample_files = [
            {
                "filename": "src/cache/redis.ts",
                "patch": "@@ -0,0 +1,25 @@\n+export class RedisCache { ... }",
                "additions": 25,
                "deletions": 0
            },
            {
                "filename": "package.json",
                "patch": "@@ -15,2 +15,3 @@\n+  \"ioredis\": \"^5.3.2\",",
                "additions": 1,
                "deletions": 0
            },
            {
                "filename": "package-lock.json",
                "patch": "@@ -1,5000 +1,5500 @@\n+ huge lockfile diff ...",
                "additions": 500,
                "deletions": 0
            },
            {
                "filename": "dist/bundle.js",
                "patch": "@@ -1,1 +1,1 @@\n+ compiled code ...",
                "additions": 1,
                "deletions": 0
            },
            {
                "filename": "assets/logo.png",
                "patch": "Binary files differ",
                "additions": 0,
                "deletions": 0
            },
            {
                "filename": ".gitignore",
                "patch": "@@ -1,1 +1,2 @@\n+.env.local",
                "additions": 1,
                "deletions": 0
            }
        ]

        formatted_diff, relevant_files, stats = diff_filter_service.filter_and_format_diff(sample_files)
        print("Filter Stats:", stats)
        print("Relevant Files:", relevant_files)
        assert "src/cache/redis.ts" in relevant_files
        assert "package.json" in relevant_files
        assert "package-lock.json" not in relevant_files
        assert "dist/bundle.js" not in relevant_files
        assert "assets/logo.png" not in relevant_files
        assert stats["ignored_files"] == 3  # lockfile, dist, png
        print("Diff Filter Verification: PASSED!")

        print("\n=== 2. Testing Change Analyzer & Comment Markdown ===")
        change_record = await change_analyzer_service.analyze_pr(
            title="Add Redis caching to Discussions API",
            body="Implements caching for read queries and invalidation on discussion updates.",
            files_data=sample_files
        )
        print("Change Record Summary:", change_record["summary"])
        print("Changes Count:", len(change_record["changes"]))
        print("Impact:", change_record["impact"])
        print("\n--- Generated Comment Preview ---\n")
        print(change_record["comment_markdown"])
        print("\n--------------------------------\n")

        assert change_record["summary"] is not None
        assert len(change_record["changes"]) >= 1
        assert len(change_record["impact"]) >= 1
        assert " Tzylo Change Summary" in change_record["comment_markdown"]
        print("Change Analyzer Verification: PASSED!")

        print("\n=== 3. Testing PR Opened Webhook Flow & Database Storage ===")
        test_repo_id = str(uuid4())
        test_repo = GithubRepository(
            id=test_repo_id,
            github_repository_id="777666",
            name="discussions-service",
            full_name="tzylo/discussions-service",
            private=False,
            active=True
        )
        db.add(test_repo)
        db.commit()

        pr_payload = {
            "action": "opened",
            "number": 42,
            "pull_request": {
                "number": 42,
                "title": "Add Redis caching to Discussions API",
                "body": "Implements caching for read queries and invalidation on discussion updates.",
                "user": {"login": "pramod-dev"},
                "files": sample_files,
                "html_url": "https://github.com/tzylo/discussions-service/pull/42"
            },
            "repository": {
                "id": "777666",
                "name": "discussions-service",
                "full_name": "tzylo/discussions-service",
                "owner": {"login": "tzylo"}
            },
            "sender": {"login": "pramod-dev"}
        }

        event_time = datetime(2026, 8, 17, 12, 0, 0)
        received_at = datetime.utcnow()

        res1 = await handle_pull_request_event(pr_payload, event_time, received_at, db)
        print("Webhook Result 1 (Initial):", res1)
        assert res1["action"] == "opened"
        assert res1["pr_number"] == 42
        assert res1["changes_count"] >= 1

        # Verify EventNode was created
        expected_ref = f"gh_pr_{test_repo_id}_42_opened"
        ev = db.query(EventNode).filter(EventNode.reference_id == expected_ref).first()
        assert ev is not None, "EventNode not created in database"
        assert ev.event_type == "pr_created"
        assert ev.actor == "pramod-dev"
        assert "change_record" in ev.metadata_json
        print(f"EventNode verified: id={ev.id}, ref={ev.reference_id}")

        # Verify KnowledgeNode was indexed
        kn = db.query(KnowledgeNode).filter(KnowledgeNode.repository_id == test_repo_id).first()
        assert kn is not None, "KnowledgeNode not indexed in database"
        assert kn.section == "GitHub PRs"
        assert "PR #42" in kn.topic
        print(f"KnowledgeNode verified: id={kn.id}, topic={kn.topic}")

        print("\n=== 4. Testing Webhook Idempotency (Duplicate Delivery) ===")
        res2 = await handle_pull_request_event(pr_payload, event_time, received_at, db)
        print("Webhook Result 2 (Duplicate Retry):", res2)
        assert res2["status"] == "already_processed", "Expected idempotency duplicate check to succeed"

        # Verify no duplicate EventNodes
        ev_count = db.query(EventNode).filter(EventNode.reference_id == expected_ref).count()
        assert ev_count == 1, f"Expected exactly 1 EventNode, found {ev_count}"
        print(f"Idempotency verified: exactly {ev_count} EventNode exists in database.")

        # Clean up test rows
        db.query(KnowledgeNode).filter(KnowledgeNode.repository_id == test_repo_id).delete()
        db.query(EventNode).filter(EventNode.reference_id == expected_ref).delete()
        db.delete(test_repo)
        db.commit()
        print("\nAll V1 PR Flow Tests Passed 100% Successfully!")

    except Exception as e:
        db.rollback()
        print(f"PR Flow Test Failed: {e}")
        raise
    finally:
        db.close()


if __name__ == "__main__":
    asyncio.run(run_pr_tests())
