import json
import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.main import app
from src.db.database import Base
from src.db.session import get_db
from models.meeting import Meeting
from schemas.live_meeting import LiveTranscriptSegment, LiveChunkIngestRequest
from transcription.vexa.live import VexaLiveClient, _format_seconds, _parse_time
from services.meeting_live_broadcaster import MeetingLiveBroadcaster


# Isolated test database setup
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


class TestVexaLiveClient(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        self.api_key = "test-vexa-live-key"
        self.client = VexaLiveClient(
            base_url="https://api.cloud.vexa.ai",
            api_key=self.api_key
        )

    def test_ws_url_construction(self):
        url = self.client.get_ws_url()
        self.assertTrue(url.startswith("wss://api.cloud.vexa.ai/ws?"))
        self.assertIn("api_key=test-vexa-live-key", url)

    def test_http_url_converts_to_ws(self):
        local_client = VexaLiveClient(base_url="http://localhost:8056", api_key="secret")
        url = local_client.get_ws_url()
        self.assertTrue(url.startswith("ws://localhost:8056/ws?"))

    def test_time_parsing_and_formatting(self):
        self.assertEqual(_parse_time(125.5), 125.5)
        self.assertEqual(_parse_time("02:15"), 135.0)
        self.assertEqual(_parse_time("01:02:03"), 3723.0)
        self.assertEqual(_format_seconds(125.0), "02:05")

    async def test_frame_parsing_and_dispatch(self):
        dispatched_segments = []

        async def mock_callback(native_id, seg):
            dispatched_segments.append((native_id, seg))

        self.client.on_segment = mock_callback

        raw_frame = json.dumps({
            "meeting_id": "meet-xyz",
            "platform": "google_meet",
            "segments": [
                {
                    "id": "seg-1",
                    "speaker": "Sarah",
                    "text": "Starting the sync",
                    "completed": False,
                    "startTime": 12.0
                },
                {
                    "id": "seg-1",
                    "speaker": "Sarah",
                    "text": "Starting the sync now.",
                    "completed": True,
                    "startTime": 12.0,
                    "endTime": 15.0
                }
            ]
        })

        await self.client._handle_raw_message(raw_frame)

        self.assertEqual(len(dispatched_segments), 2)
        native_id, first_seg = dispatched_segments[0]
        self.assertEqual(native_id, "meet-xyz")
        self.assertEqual(first_seg.speaker, "Sarah")
        self.assertEqual(first_seg.text, "Starting the sync")
        self.assertFalse(first_seg.completed)

        _, second_seg = dispatched_segments[1]
        self.assertEqual(second_seg.text, "Starting the sync now.")
        self.assertTrue(second_seg.completed)


class TestMeetingLiveBroadcaster(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        self.broadcaster = MeetingLiveBroadcaster()

    async def test_buffer_reconciliation(self):
        meeting_id = "test-meeting-1"

        # 1. Draft chunk
        seg1 = LiveTranscriptSegment(
            id="seg-1",
            speaker="Alex",
            text="We are currently",
            completed=False,
            start_time=1.0
        )
        await self.broadcaster.broadcast_segment(meeting_id, seg1)

        snapshot1 = self.broadcaster.get_live_buffer(meeting_id)
        self.assertEqual(len(snapshot1.segments), 1)
        self.assertEqual(snapshot1.segments[0].text, "We are currently")
        self.assertFalse(snapshot1.segments[0].completed)

        # 2. Updated draft (same ID or in-flight)
        seg1_updated = LiveTranscriptSegment(
            id="seg-1",
            speaker="Alex",
            text="We are currently deploying the service",
            completed=False,
            start_time=1.0
        )
        await self.broadcaster.broadcast_segment(meeting_id, seg1_updated)

        snapshot2 = self.broadcaster.get_live_buffer(meeting_id)
        self.assertEqual(len(snapshot2.segments), 1)
        self.assertEqual(snapshot2.segments[0].text, "We are currently deploying the service")

        # 3. Finalized chunk
        seg1_final = LiveTranscriptSegment(
            id="seg-1",
            speaker="Alex",
            text="We are currently deploying the service to production.",
            completed=True,
            start_time=1.0,
            end_time=4.5
        )
        await self.broadcaster.broadcast_segment(meeting_id, seg1_final)

        snapshot3 = self.broadcaster.get_live_buffer(meeting_id)
        self.assertEqual(len(snapshot3.segments), 1)
        self.assertEqual(snapshot3.segments[0].text, "We are currently deploying the service to production.")
        self.assertTrue(snapshot3.segments[0].completed)

        # 4. Next speaker segment
        seg2 = LiveTranscriptSegment(
            id="seg-2",
            speaker="Jordan",
            text="Understood.",
            completed=True,
            start_time=5.0
        )
        await self.broadcaster.broadcast_segment(meeting_id, seg2)

        snapshot4 = self.broadcaster.get_live_buffer(meeting_id)
        self.assertEqual(len(snapshot4.segments), 2)
        self.assertIn("Alex", snapshot4.speakers)
        self.assertIn("Jordan", snapshot4.speakers)

    async def test_native_meeting_id_mapping(self):
        self.broadcaster.register_meeting_mapping("uuid-1234", "abc-defg-hij")
        self.assertEqual(self.broadcaster.resolve_meeting_id("abc-defg-hij"), "uuid-1234")

    async def test_sse_generator(self):
        meeting_id = "test-sse-meeting"
        seg = LiveTranscriptSegment(
            id="seg-10",
            speaker="Eve",
            text="Testing SSE generator",
            completed=True
        )
        await self.broadcaster.broadcast_segment(meeting_id, seg)

        gen = self.broadcaster.subscribe_sse(meeting_id)
        # First event: connected
        first_event = await gen.__anext__()
        self.assertIn("event: connected", first_event)
        # Second event: catch_up
        second_event = await gen.__anext__()
        self.assertIn("event: catch_up", second_event)
        self.assertIn("Testing SSE generator", second_event)
        await gen.aclose()


class TestLiveStreamingAPI(unittest.TestCase):

    def setUp(self):
        from services.meeting_live_broadcaster import meeting_live_broadcaster
        meeting_live_broadcaster._buffers.clear()
        meeting_live_broadcaster._speakers.clear()
        meeting_live_broadcaster._ws_subscribers.clear()
        meeting_live_broadcaster._sse_subscribers.clear()
        meeting_live_broadcaster._native_to_meeting.clear()

        Base.metadata.create_all(bind=test_engine)
        self.client = TestClient(app)

        # Seed meeting
        db = TestingSessionLocal()
        self.meeting = Meeting(
            id="meet-uuid-999",
            title="Sprint Planning",
            platform="google_meet",
            meeting_url="https://meet.google.com/xyz-uvwx-rst",
            native_meeting_id="xyz-uvwx-rst",
            status="active"
        )
        db.add(self.meeting)
        db.commit()
        db.close()

    def tearDown(self):
        from services.meeting_live_broadcaster import meeting_live_broadcaster
        meeting_live_broadcaster._buffers.clear()
        meeting_live_broadcaster._speakers.clear()
        meeting_live_broadcaster._ws_subscribers.clear()
        meeting_live_broadcaster._sse_subscribers.clear()
        meeting_live_broadcaster._native_to_meeting.clear()
        Base.metadata.drop_all(bind=test_engine)

    def test_post_chunk_and_get_snapshot(self):
        chunk_payload = {
            "speaker": "Dave",
            "text": "Let's review the blockers.",
            "completed": True,
            "startTime": 30.0
        }

        # 1. Ingest chunk via POST
        res = self.client.post("/api/v1/meetings/meet-uuid-999/live/transcript-chunk", json=chunk_payload)
        self.assertEqual(res.status_code, 201)
        data = res.json()
        self.assertEqual(data["speaker"], "Dave")
        self.assertEqual(data["text"], "Let's review the blockers.")
        self.assertTrue(data["completed"])

        # 2. Query snapshot via GET
        snapshot_res = self.client.get("/api/v1/meetings/meet-uuid-999/live/transcript")
        self.assertEqual(snapshot_res.status_code, 200)
        snapshot_data = snapshot_res.json()
        self.assertEqual(snapshot_data["meeting_id"], "meet-uuid-999")
        self.assertEqual(len(snapshot_data["segments"]), 1)
        self.assertIn("Dave", snapshot_data["speakers"])

    def test_websocket_connection_and_catchup(self):
        # Ingest initial segment
        self.client.post("/api/v1/meetings/meet-uuid-999/live/transcript-chunk", json={
            "speaker": "Dave",
            "text": "Hello world",
            "completed": True
        })

        # Connect WebSocket
        with self.client.websocket_connect("/api/v1/meetings/meet-uuid-999/live/ws") as ws:
            # 1. Connected frame
            frame1 = ws.receive_json()
            self.assertEqual(frame1["type"], "connected")
            self.assertEqual(frame1["meeting_id"], "meet-uuid-999")

            # 2. Catch-up frame
            frame2 = ws.receive_json()
            self.assertEqual(frame2["type"], "catch_up")
            self.assertEqual(len(frame2["segments"]), 1)
            self.assertEqual(frame2["segments"][0]["text"], "Hello world")

            # 3. Send ping -> receive pong
            ws.send_json({"type": "ping"})
            pong_frame = ws.receive_json()
            self.assertEqual(pong_frame["type"], "pong")
