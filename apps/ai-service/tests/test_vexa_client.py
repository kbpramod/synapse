import unittest
from unittest.mock import AsyncMock, patch, MagicMock
import httpx

from src.transcription.vexa.client import VexaClient
from src.transcription.exceptions import (
    ConfigurationError,
    ProviderAuthError,
    ProviderUnavailableError,
    ProviderTimeoutError,
    TranscriptionError
)


class TestVexaClient(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        self.api_key = "test-secret-vexa-key-12345"
        self.base_url = "https://api.cloud.vexa.ai"
        self.client = VexaClient(base_url=self.base_url, api_key=self.api_key)

    def test_missing_api_key_raises_configuration_error(self):
        with patch.dict("os.environ", {}, clear=True):
            client = VexaClient(base_url=self.base_url, api_key=None)
            with self.assertRaises(ConfigurationError) as ctx:
                _ = client.api_key
            self.assertIn("Vexa API key is missing", str(ctx.exception))

    async def test_create_bot_success(self):
        mock_response = httpx.Response(
            status_code=200,
            json={"id": "vexa-bot-999", "status": "joining"},
            request=httpx.Request("POST", f"{self.base_url}/bots")
        )

        with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = mock_response

            res = await self.client.create_bot(
                native_meeting_id="abc-defg-hij",
                platform="google_meet",
                bot_name="Engineering Assistant"
            )

            self.assertEqual(res["id"], "vexa-bot-999")
            self.assertEqual(res["status"], "joining")

            # Verify request arguments
            mock_post.assert_called_once()
            args, kwargs = mock_post.call_args
            self.assertEqual(args[0], f"{self.base_url}/bots")
            self.assertEqual(kwargs["headers"]["X-API-Key"], self.api_key)
            self.assertEqual(kwargs["json"]["native_meeting_id"], "abc-defg-hij")
            self.assertEqual(kwargs["json"]["platform"], "google_meet")
            self.assertEqual(kwargs["json"]["bot_name"], "Engineering Assistant")
            self.assertTrue(kwargs["json"]["transcribe_enabled"])

    async def test_get_transcript_success(self):
        mock_transcript = [
            {"speaker": "John", "text": "Hello world", "start_time": 1.0, "end_time": 3.0}
        ]
        mock_response = httpx.Response(
            status_code=200,
            json=mock_transcript,
            request=httpx.Request("GET", f"{self.base_url}/transcripts/google_meet/abc-defg-hij")
        )

        with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = mock_response

            res = await self.client.get_transcript(
                native_meeting_id="abc-defg-hij",
                platform="google_meet"
            )

            self.assertEqual(res, mock_transcript)
            mock_get.assert_called_once()
            args, kwargs = mock_get.call_args
            self.assertEqual(args[0], f"{self.base_url}/transcripts/google_meet/abc-defg-hij")
            self.assertEqual(kwargs["headers"]["X-API-Key"], self.api_key)

    async def test_get_transcript_404_returns_empty_list(self):
        mock_response = httpx.Response(
            status_code=404,
            json={"detail": "Not found"},
            request=httpx.Request("GET", f"{self.base_url}/transcripts/google_meet/abc-defg-hij")
        )

        with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = mock_response

            res = await self.client.get_transcript(native_meeting_id="abc-defg-hij")
            self.assertEqual(res, [])

    async def test_create_bot_401_raises_provider_auth_error_without_key_leak(self):
        mock_response = httpx.Response(
            status_code=401,
            json={"error": "Unauthorized"},
            request=httpx.Request("POST", f"{self.base_url}/bots")
        )

        with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = mock_response

            with self.assertRaises(ProviderAuthError) as ctx:
                await self.client.create_bot(native_meeting_id="abc-defg-hij")

            # Verify API key is NOT leaked in exception message
            self.assertNotIn(self.api_key, str(ctx.exception))

    async def test_create_bot_500_raises_provider_unavailable(self):
        mock_response = httpx.Response(
            status_code=500,
            text="Internal Server Error",
            request=httpx.Request("POST", f"{self.base_url}/bots")
        )

        with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = mock_response

            with self.assertRaises(ProviderUnavailableError):
                await self.client.create_bot(native_meeting_id="abc-defg-hij")

    async def test_timeout_raises_provider_timeout_error(self):
        with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
            mock_post.side_effect = httpx.ReadTimeout("Read timed out")

            with self.assertRaises(ProviderTimeoutError):
                await self.client.create_bot(native_meeting_id="abc-defg-hij")


if __name__ == "__main__":
    unittest.main()
