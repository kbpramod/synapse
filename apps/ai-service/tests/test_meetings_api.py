import unittest
from unittest.mock import patch, AsyncMock
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.main import app
from src.db.database import Base
from src.db.session import get_db
from src.models.meeting import Meeting
from src.transcription.base import ProviderBotInfo
from src.transcription.models import CanonicalTranscript, CanonicalTranscriptSegment
from src.transcription.exceptions import (
    ProviderAuthError,
    ProviderUnavailableError,
    ConfigurationError
)


# Set up isolated in-memory SQLite engine for API tests
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


class TestMeetingsAPI(unittest.TestCase):

    def setUp(self):
        Base.metadata.create_all(bind=test_engine)
        self.client = TestClient(app)

    def tearDown(self):
        Base.metadata.drop_all(bind=test_engine)

    @patch("src.services.meeting_service.get_transcript_provider")
    def test_post_meetings_success(self, mock_get_provider):
        mock_provider = AsyncMock()
        mock_provider.start_meeting.return_value = ProviderBotInfo(
            bot_id="vexa-bot-456",
            status="joining",
            platform="google_meet",
            native_meeting_id="abc-defg-hij"
        )
        mock_get_provider.return_value = mock_provider

        payload = {
            "meetingUrl": "https://meet.google.com/abc-defg-hij",
            "title": "Engineering Sync"
        }

        response = self.client.post("/meetings", json=payload)
        self.assertEqual(response.status_code, 201)
        data = response.json()

        self.assertIn("id", data)
        self.assertEqual(data["status"], "joining")
        self.assertEqual(data["title"], "Engineering Sync")
        self.assertEqual(data["platform"], "google_meet")

        # Verify DB record was created
        db = TestingSessionLocal()
        meeting = db.query(Meeting).filter(Meeting.id == data["id"]).first()
        db.close()

        self.assertIsNotNone(meeting)
        self.assertEqual(meeting.native_meeting_id, "abc-defg-hij")
        self.assertEqual(meeting.vexa_meeting_id, "vexa-bot-456")
        self.assertEqual(meeting.status, "joining")

    def test_post_meetings_invalid_url_returns_400(self):
        payload = {
            "meetingUrl": "https://invalid-meeting-site.com/123",
            "title": "Invalid Meeting"
        }

        response = self.client.post("/meetings", json=payload)
        self.assertEqual(response.status_code, 400)
        data = response.json()
        self.assertIn("error", data)
        self.assertEqual(data["error"]["code"], "BAD_REQUEST")

    @patch("src.services.meeting_service.get_transcript_provider")
    def test_post_meetings_provider_auth_failure_returns_502(self, mock_get_provider):
        mock_provider = AsyncMock()
        mock_provider.start_meeting.side_effect = ProviderAuthError("Secret key invalid")
        mock_get_provider.return_value = mock_provider

        payload = {
            "meetingUrl": "https://meet.google.com/abc-defg-hij",
            "title": "Engineering Sync"
        }

        response = self.client.post("/meetings", json=payload)
        self.assertEqual(response.status_code, 502)
        data = response.json()
        self.assertIn("error", data)
        # Verify no secret leakage
        self.assertNotIn("Secret key invalid", response.text)
        self.assertEqual(data["error"]["message"], "Failed to authenticate with transcription provider.")

    @patch("src.services.meeting_service.get_transcript_provider")
    def test_get_meeting_transcript_success(self, mock_get_provider):
        # 1. Create meeting record in DB
        db = TestingSessionLocal()
        meeting = Meeting(
            id="meeting-uuid-777",
            title="Engineering Sync",
            platform="google_meet",
            meeting_url="https://meet.google.com/abc-defg-hij",
            native_meeting_id="abc-defg-hij",
            vexa_meeting_id="vexa-bot-456",
            status="joining"
        )
        db.add(meeting)
        db.commit()
        db.close()

        # 2. Mock provider transcript retrieval
        mock_provider = AsyncMock()
        mock_provider.get_transcript.return_value = CanonicalTranscript(
            meetingId="meeting-uuid-777",
            title="Engineering Sync",
            platform="google_meet",
            participants=["John", "Sarah"],
            segments=[
                CanonicalTranscriptSegment(
                    speaker="John",
                    text="We need retry logic for the payment webhook.",
                    startTime=142.5,
                    endTime=147.2
                ),
                CanonicalTranscriptSegment(
                    speaker="Sarah",
                    text="I will take that action item.",
                    startTime=148.0,
                    endTime=151.0
                )
            ],
            status="active"
        )
        mock_get_provider.return_value = mock_provider

        # 3. Call GET /meetings/:id/transcript
        response = self.client.get("/meetings/meeting-uuid-777/transcript")
        self.assertEqual(response.status_code, 200)
        data = response.json()

        self.assertEqual(data["meetingId"], "meeting-uuid-777")
        self.assertEqual(data["title"], "Engineering Sync")
        self.assertEqual(data["platform"], "google_meet")
        self.assertEqual(data["participants"], ["John", "Sarah"])
        self.assertEqual(len(data["segments"]), 2)
        self.assertEqual(data["segments"][0]["speaker"], "John")
        self.assertEqual(data["segments"][0]["text"], "We need retry logic for the payment webhook.")
        self.assertEqual(data["segments"][0]["startTime"], 142.5)
        self.assertEqual(data["segments"][0]["endTime"], 147.2)

    def test_get_meeting_transcript_not_found(self):
        response = self.client.get("/meetings/non-existent-uuid/transcript")
        self.assertEqual(response.status_code, 404)
        data = response.json()
        self.assertEqual(data["error"]["code"], "NOT_FOUND")

    def test_get_meeting_details(self):
        db = TestingSessionLocal()
        meeting = Meeting(
            id="meeting-detail-test",
            title="Design Review",
            platform="google_meet",
            meeting_url="https://meet.google.com/xyz-uvwx-rst",
            native_meeting_id="xyz-uvwx-rst",
            status="active"
        )
        db.add(meeting)
        db.commit()
        db.close()

        response = self.client.get("/meetings/meeting-detail-test")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["id"], "meeting-detail-test")
        self.assertEqual(data["title"], "Design Review")
        self.assertEqual(data["status"], "active")
        self.assertEqual(data["native_meeting_id"], "xyz-uvwx-rst")


if __name__ == "__main__":
    unittest.main()
