import asyncio
import json
import os
import sys
from datetime import datetime, timedelta

# Ensure python path includes apps/ai-service
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from sqlalchemy import text
from src.db.database import engine, Base
from src.db.session import SessionLocal
from src.services.event_service import event_service
from src.services.identity_service import identity_service
from src.services.query_service import query_service
from src.schemas.event import MeetingIngestRequest, TranscriptSegment, EventCreate


async def run_simulation():
    print("=" * 80)
    print("  TZYLO — ENGINEERING MEMORY ASSISTANT LIFECYCLE SIMULATION  ")
    print("=" * 80)

    # Initialize Database Tables & Migrations
    with engine.connect() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector;"))
        conn.execute(text("ALTER TABLE event_nodes ADD COLUMN IF NOT EXISTS person_id VARCHAR;"))
        conn.execute(text("ALTER TABLE event_nodes ADD COLUMN IF NOT EXISTS work_item_id VARCHAR;"))
        conn.commit()
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()


    try:
        # -------------------------------------------------------------------------
        # DAY 1: Meeting Transcript Ingestion (Human Intent)
        # -------------------------------------------------------------------------
        print("\n[DAY 1] Ingesting Synthetic Meeting Transcript...")
        meeting_req = MeetingIngestRequest(
            meeting_id="meeting-caching-001",
            title="Discussions API Optimization",
            started_at=(datetime.utcnow() - timedelta(days=3)).isoformat(),
            participants=["Alex", "Pramod"],
            segments=[
                TranscriptSegment(
                    speaker="Alex",
                    text="We've been seeing repeated reads and high database load on Discussions.",
                    timestamp="10:02"
                ),
                TranscriptSegment(
                    speaker="Pramod",
                    text="I can add Redis caching for Discussion reads.",
                    timestamp="10:04"
                ),
                TranscriptSegment(
                    speaker="Alex",
                    text="Okay. Make sure it is tested and reviewed before release.",
                    timestamp="10:05"
                ),
                TranscriptSegment(
                    speaker="Pramod",
                    text="I'll raise the PR tomorrow.",
                    timestamp="10:06"
                )
            ]
        )

        meeting_events = await event_service.ingest_meeting(db, meeting_req)
        print(f"-> Ingested {len(meeting_events)} meeting events.")

        # -------------------------------------------------------------------------
        # DAY 1: GitHub PR Created (Observable Execution Begins)
        # -------------------------------------------------------------------------
        print("\n[DAY 1] Simulating GitHub PR Creation (#182 by 'smartcoder')...")
        from src.services.change_analyzer_service import change_analyzer_service
        
        pr_title = "add redis cache"
        pr_body = "changes"
        diff_snippet = """
        + class DiscussionCacheService {
        +     async getDiscussion(id) {
        +         const cached = await redis.get(`discussion:${id}`);
        +         if (cached) return JSON.parse(cached);
        +         const data = await db.discussions.findById(id);
        +         await redis.set(`discussion:${id}`, JSON.stringify(data), 'EX', 3600);
        +         return data;
        +     }
        + }
        """

        change_record = await change_analyzer_service.analyze_pr(
            title=pr_title,
            body=pr_body,
            changed_files=["src/services/discussion_cache.js"],
            diff_snippet=diff_snippet
        )
        print(f"-> Change Analyzer produced summary: '{change_record['summary']}'")

        pr_created_event = EventCreate(
            source="github",
            event_type="pr_created",
            timestamp=datetime.utcnow() - timedelta(days=2),
            actor="smartcoder",
            entity_type="pull_request",
            entity_id="PR #182",
            content=f"GitHub PR #182 created by smartcoder: {change_record['summary']}",
            reference_id="gh_pr_182_created",
            metadata_json={
                "pr_number": 182,
                "change_record": change_record
            }
        )
        pr_event_node, _ = await event_service.ingest_event(db, pr_created_event)
        print(f"-> Ingested PR #182 event (ID: {pr_event_node.id}).")

        # -------------------------------------------------------------------------
        # DAY 2: GitHub Code Review (Changes Requested)
        # -------------------------------------------------------------------------
        print("\n[DAY 2] Simulating GitHub Code Review (Changes Requested by 'alex-teamlead')...")
        review_event = EventCreate(
            source="github",
            event_type="changes_requested",
            timestamp=datetime.utcnow() - timedelta(days=1),
            actor="alex-teamlead",
            entity_type="pull_request",
            entity_id="PR #182",
            content="GitHub Review on PR #182 [changes_requested]: Please add cache invalidation logic on discussion updates.",
            reference_id="gh_review_182_changes",
            metadata_json={
                "pr_number": 182,
                "review_state": "changes_requested"
            }
        )
        await event_service.ingest_event(db, review_event)
        print("-> Ingested Review event: Changes Requested.")

        # -------------------------------------------------------------------------
        # DAY 3: GitHub PR Approved & Merged
        # -------------------------------------------------------------------------
        print("\n[DAY 3] Simulating GitHub PR Approval & Merge...")
        approval_event = EventCreate(
            source="github",
            event_type="pr_approved",
            timestamp=datetime.utcnow() - timedelta(hours=12),
            actor="alex-teamlead",
            entity_type="pull_request",
            entity_id="PR #182",
            content="GitHub Review on PR #182 [pr_approved]: Cache invalidation looks good!",
            reference_id="gh_review_182_approved"
        )
        await event_service.ingest_event(db, approval_event)

        merge_event = EventCreate(
            source="github",
            event_type="pr_merged",
            timestamp=datetime.utcnow() - timedelta(hours=6),
            actor="smartcoder",
            entity_type="pull_request",
            entity_id="PR #182",
            content="GitHub PR #182 merged into main by smartcoder.",
            reference_id="gh_pr_182_merged"
        )
        await event_service.ingest_event(db, merge_event)
        print("-> Ingested PR #182 Merge event.")

        # -------------------------------------------------------------------------
        # QUERY EVALUATION: Reconstruct Engineering Work Lifecycle
        # -------------------------------------------------------------------------
        print("\n" + "=" * 80)
        print("  EXECUTING NATURAL LANGUAGE STATE RECONSTRUCTION QUERY  ")
        print("=" * 80)

        user_query = "What happened to the caching work we discussed?"
        print(f"\nUser Query: '{user_query}'\n")

        query_response = await query_service.answer_query(
            db=db,
            query=user_query,
            time_window_days=7
        )

        print("\n--- RECONSTRUCTED STATE ANSWER FROM TZYLO ---")
        print(query_response["answer"])

        print("\n--- TRACKED WORK ITEMS ---")
        print(json.dumps(query_response["work_items"], indent=2))

        print("\n--- RECOVERED TIMELINE EVIDENCE COUNT ---")
        print(f"Evidence items: {query_response['evidence_count']}")

        print("\nSUCCESS: Simulation completed successfully.")

    finally:
        db.close()


if __name__ == "__main__":
    asyncio.run(run_simulation())
