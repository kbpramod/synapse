import unittest
from unittest.mock import patch, AsyncMock
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import models
from main import app
from db.database import Base
from db.session import get_db
from models.meeting import Meeting


SQLALCHEMY_DATABASE_URL = "sqlite:///:memory:"
test_engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)


def override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


app.dependency_overrides[get_db] = override_get_db


class TestLiveMeetingsAPI(unittest.TestCase):

    def setUp(self):
        Base.metadata.create_all(bind=test_engine)
        self.client = TestClient(app)
        self.db = TestingSessionLocal()

        # Seed meeting
        self.meeting = Meeting(
            id="meet-live-101",
            native_meeting_id="xyz-uvwx-rst",
            title="Weekly Engineering Architecture Sync",
            status="joining",
            participants_json=["Alice"],
            decisions_json=[],
            action_items_json=[],
            metadata_json={}
        )
        self.db.add(self.meeting)
        self.db.commit()

    def tearDown(self):
        self.db.close()
        Base.metadata.drop_all(bind=test_engine)

    @patch("services.live_meeting_service.live_meeting_graph.ainvoke")
    def test_post_live_window_success(self, mock_graph_invoke):
        mock_graph_invoke.return_value = {
            "members": ["Alice", "Bob"],
            "recent_decisions": [
                {"decision": "Adopt LangGraph for live meeting context management", "topic": "Architecture", "rationale": "State machine semantics"}
            ],
            "tasks": [
                {"task": "Implement 4-minute window runner", "assignee": "Bob", "work_status": "PLANNED", "area": "Backend"}
            ],
            "window_summary": "Alice and Bob aligned on LangGraph architecture.",
            "accumulated_summary": "Architecture sync underway. Team decided on LangGraph for stateful meetings.",
            "rag_context": [{"source": "knowledge", "title": "LangGraph Doc", "content": "LangGraph handles cyclic states."}],
            "new_decisions": [{"decision": "Adopt LangGraph"}],
            "new_tasks": [{"task": "Implement 4-minute window runner"}]
        }

        payload = {
            "segments": [
                {"speaker": "Alice", "text": "Let's use LangGraph for our meeting state.", "timestamp": "01:15"},
                {"speaker": "Bob", "text": "Agreed, I'll take the task to implement the window runner.", "timestamp": "02:00"}
            ],
            "window_start_sec": 0.0,
            "window_end_sec": 240.0,
            "force_rag": True
        }

        response = self.client.post("/meetings/meet-live-101/live-window", json=payload)
        self.assertEqual(response.status_code, 200)

        data = response.json()
        self.assertEqual(data["meeting_id"], "meet-live-101")
        self.assertEqual(data["window_index"], 1)
        self.assertEqual(data["window_summary"], "Alice and Bob aligned on LangGraph architecture.")
        self.assertEqual(len(data["members"]), 2)
        self.assertIn("Bob", data["members"])
        self.assertEqual(len(data["recent_decisions"]), 1)
        self.assertEqual(data["recent_decisions"][0]["decision"], "Adopt LangGraph for live meeting context management")
        self.assertEqual(len(data["tasks"]), 1)
        self.assertEqual(data["tasks"][0]["assignee"], "Bob")
        self.assertTrue(data["rag_used"])
        self.assertEqual(data["rag_context_count"], 1)

        # Verify DB was updated
        db_meeting = self.db.query(Meeting).filter(Meeting.id == "meet-live-101").first()
        self.assertEqual(db_meeting.status, "active")
        self.assertEqual(len(db_meeting.decisions_json), 1)
        self.assertEqual(len(db_meeting.action_items_json), 1)

    def test_get_live_state(self):
        # Update meeting state in DB
        self.meeting.summary = "Ongoing sync with 1 decision recorded."
        self.meeting.participants_json = ["Alice", "Bob"]
        self.meeting.decisions_json = [{"decision": "Use PostgreSQL", "topic": "Database"}]
        self.meeting.action_items_json = [{"task": "Write schema", "assignee": "Alice"}]
        self.meeting.metadata_json = {"live_windows": [{"window_index": 1}], "last_window_time": 240.0}
        self.db.commit()

        response = self.client.get("/meetings/meet-live-101/live-state")
        self.assertEqual(response.status_code, 200)

        data = response.json()
        self.assertEqual(data["meeting_id"], "meet-live-101")
        self.assertEqual(data["summary"], "Ongoing sync with 1 decision recorded.")
        self.assertEqual(len(data["members"]), 2)
        self.assertEqual(len(data["recent_decisions"]), 1)
        self.assertEqual(len(data["tasks"]), 1)
        self.assertEqual(data["total_windows_processed"], 1)
        self.assertEqual(data["last_window_time"], 240.0)

    def test_start_and_stop_live_session(self):
        start_res = self.client.post("/meetings/meet-live-101/live/start", json={"interval_seconds": 240})
        self.assertEqual(start_res.status_code, 200)
        self.assertEqual(start_res.json()["status"], "running")

        stop_res = self.client.post("/meetings/meet-live-101/live/stop")
        self.assertEqual(stop_res.status_code, 200)
        self.assertEqual(stop_res.json()["status"], "stopped")


if __name__ == "__main__":
    unittest.main()
