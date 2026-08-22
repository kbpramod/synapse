import unittest
from unittest.mock import AsyncMock, MagicMock

from src.transcription.vexa.provider import VexaTranscriptProvider, _parse_time
from src.transcription.vexa.client import VexaClient
from src.transcription.models import CanonicalTranscript


class TestVexaTranscriptProvider(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        self.mock_client = MagicMock(spec=VexaClient)
        self.provider = VexaTranscriptProvider(client=self.mock_client)

    def test_parse_time_formats(self):
        self.assertEqual(_parse_time(142.5), 142.5)
        self.assertEqual(_parse_time(100), 100.0)
        self.assertEqual(_parse_time("142.5"), 142.5)
        self.assertEqual(_parse_time("02:30"), 150.0)  # 2m 30s = 150s
        self.assertEqual(_parse_time("01:00:00"), 3600.0)  # 1h = 3600s
        self.assertIsNone(_parse_time(None))
        self.assertIsNone(_parse_time("invalid"))

    def test_normalize_list_of_segments(self):
        raw = [
            {
                "speaker": "John",
                "text": "We need retry logic for the payment webhook.",
                "start_time": 142.5,
                "end_time": 147.2
            },
            {
                "speaker": "Alice",
                "text": "Agreed, let's also add exponential backoff.",
                "startTime": 148.0,
                "endTime": 152.4
            }
        ]

        normalized = self.provider.normalize_transcript(
            raw_data=raw,
            meeting_id="test-meeting-uuid-1",
            title="Engineering Sync"
        )

        self.assertEqual(normalized.meeting_id, "test-meeting-uuid-1")
        self.assertEqual(normalized.title, "Engineering Sync")
        self.assertEqual(normalized.platform, "google_meet")
        self.assertEqual(sorted(normalized.participants), ["Alice", "John"])
        self.assertEqual(len(normalized.segments), 2)
        self.assertEqual(normalized.segments[0].speaker, "John")
        self.assertEqual(normalized.segments[0].text, "We need retry logic for the payment webhook.")
        self.assertEqual(normalized.segments[0].start_time, 142.5)
        self.assertEqual(normalized.segments[0].end_time, 147.2)
        self.assertEqual(normalized.segments[1].speaker, "Alice")
        self.assertEqual(normalized.status, "active")

    def test_normalize_nested_segments_dict(self):
        raw = {
            "segments": [
                {
                    "name": "Bob",
                    "content": "Deployment is complete.",
                    "start": "01:15",
                    "end": "01:20"
                }
            ]
        }

        normalized = self.provider.normalize_transcript(
            raw_data=raw,
            meeting_id="meet-uuid-2",
            title="Release Sync"
        )

        self.assertEqual(normalized.participants, ["Bob"])
        self.assertEqual(len(normalized.segments), 1)
        self.assertEqual(normalized.segments[0].speaker, "Bob")
        self.assertEqual(normalized.segments[0].text, "Deployment is complete.")
        self.assertEqual(normalized.segments[0].start_time, 75.0)  # 1m 15s

    def test_normalize_empty_transcript(self):
        normalized = self.provider.normalize_transcript(
            raw_data=[],
            meeting_id="meet-uuid-3",
            title="Standup"
        )

        self.assertEqual(normalized.segments, [])
        self.assertEqual(normalized.participants, [])
        self.assertEqual(normalized.status, "joining")

    async def test_start_meeting_flow(self):
        self.mock_client.create_bot = AsyncMock(return_value={
            "id": "bot-abc-123",
            "status": "joining"
        })

        bot_info = await self.provider.start_meeting(
            meeting_url="https://meet.google.com/abc-defg-hij",
            native_meeting_id="abc-defg-hij",
            title="Sprint Planning"
        )

        self.assertEqual(bot_info.bot_id, "bot-abc-123")
        self.assertEqual(bot_info.status, "joining")
        self.assertEqual(bot_info.native_meeting_id, "abc-defg-hij")

    async def test_get_transcript_flow(self):
        self.mock_client.get_transcript = AsyncMock(return_value=[
            {"speaker": "Dev", "text": "Writing unit tests", "start_time": 5.0, "end_time": 10.0}
        ])

        canonical = await self.provider.get_transcript(
            native_meeting_id="abc-defg-hij",
            meeting_id="meeting-123",
            title="Sprint Planning"
        )

        self.assertIsInstance(canonical, CanonicalTranscript)
        self.assertEqual(canonical.meeting_id, "meeting-123")
        self.assertEqual(len(canonical.segments), 1)
        self.assertEqual(canonical.segments[0].speaker, "Dev")


if __name__ == "__main__":
    unittest.main()
