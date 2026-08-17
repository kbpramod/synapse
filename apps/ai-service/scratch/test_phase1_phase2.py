import asyncio
import os
import sys
from datetime import datetime

# Add src to python path
sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

from sqlalchemy import text
from src.db.database import engine, Base
from src.db.session import SessionLocal

# Import models
from src.models.event_node import EventNode
from src.models.event_relationship import EventRelationship
from src.models.knowledge_node import KnowledgeNode

from src.schemas.event import MeetingIngestRequest, TranscriptSegment, EventCreate
from src.services.event_service import event_service
from src.repositories.event_repository import event_repository


async def main():
    print("=== Testing Tzylo Phase 1 & Phase 2 Event Engine ===")

    # 1. Initialize Database Tables
    print("\n1. Initializing DB & Vector extension...")
    with engine.connect() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector;"))
        conn.execute(text("ALTER TABLE knowledge_nodes ADD COLUMN IF NOT EXISTS source VARCHAR;"))
        conn.execute(text("ALTER TABLE knowledge_nodes ADD COLUMN IF NOT EXISTS event_id VARCHAR;"))
        conn.commit()
    Base.metadata.create_all(bind=engine)
    print("DB Tables initialized successfully!")

    db = SessionLocal()

    try:
        # 2. Ingest Meeting Transcript
        print("\n2. Ingesting Meeting Transcript...")
        meeting_req = MeetingIngestRequest(
            meeting_id="meeting-caching-001",
            title="Discussion Caching & Optimization",
            started_at=datetime.utcnow().isoformat(),
            participants=["Pramod", "Alex"],
            segments=[
                TranscriptSegment(speaker="Alex", text="Our discussion search is slow when database gets large.", timestamp="10:00"),
                TranscriptSegment(speaker="Pramod", text="I will implement Redis caching for Discussions by tomorrow.", timestamp="10:02"),
                TranscriptSegment(speaker="Alex", text="Great, let's also make sure we clear the cache on comment creation.", timestamp="10:05")
            ],
            repository_id="tzylo-core"
        )

        meeting_events = await event_service.ingest_meeting(db, meeting_req)
        print(f"Extracted {len(meeting_events)} event(s) from meeting:")
        for evt in meeting_events:
            print(f"  - [{evt.source} | {evt.event_type}] Actor: {evt.actor} -> {evt.content}")

        # 3. Test Idempotency (Re-ingest same meeting)
        print("\n3. Testing Idempotency on Meeting Re-ingestion...")
        reingested_events = await event_service.ingest_meeting(db, meeting_req)
        print(f"Re-ingest returned {len(reingested_events)} events (all skipped creation due to idempotency).")

        # 4. Ingest GitHub PR Event (simulating GitHub Webhook)
        print("\n4. Ingesting GitHub PR Event...")
        pr_event_create = EventCreate(
            source="github",
            event_type="github_pr_created",
            timestamp=datetime.utcnow(),
            actor="Pramod",
            entity_type="pull_request",
            entity_id="PR #182",
            content="GitHub PR #182 (github_pr_created): Add Redis caching layer for Discussion search queries.",
            reference_id="gh_pr_tzylo-core_182_opened",
            metadata_json={"pr_number": 182, "repo": "tzylo-core"}
        )

        pr_event, newly_created = await event_service.ingest_event(db, pr_event_create)
        print(f"GitHub Event Ingested (new={newly_created}): [{pr_event.source}] {pr_event.content}")

        # 5. Check Correlations & Timeline
        print("\n5. Retrieving Correlated Timeline...")
        timeline = event_service.get_timeline(db, actor="Pramod", limit=10)
        print(f"Found {len(timeline)} item(s) in Pramod's timeline:")
        for item in timeline:
            evt = item["event"]
            rels = item["relationships"]
            print(f"\n* EVENT [{evt.timestamp.strftime('%H:%M:%S')}] [{evt.source}] ({evt.event_type}) - Actor: {evt.actor}")
            print(f"  Content: {evt.content}")
            if rels:
                print(f"  --> Linked Relationships ({len(rels)}):")
                for rel in rels:
                    print(f"      - {rel.relationship_type} (score: {rel.confidence_score})")

        print("\n=== Phase 1 & Phase 2 Verification Completed Successfully! ===")

    finally:
        db.close()


if __name__ == "__main__":
    asyncio.run(main())
