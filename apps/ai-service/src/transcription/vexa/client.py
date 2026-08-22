import os
import logging
from typing import Optional, Dict, Any, Union, List
import httpx

from src.transcription.exceptions import (
    ConfigurationError,
    ProviderAuthError,
    ProviderUnavailableError,
    ProviderTimeoutError,
    TranscriptNotFoundError,
    TranscriptionError
)

logger = logging.getLogger(__name__)


class VexaClient:
    """
    Low-level HTTP client for the Vexa Cloud API.
    
    Handles authentication, request construction, response parsing, and error normalization.
    Ensures credentials and sensitive headers are never logged or leaked.
    """

    def __init__(
        self,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        timeout: float = 20.0
    ):
        self.base_url = (base_url or os.getenv("VEXA_API_BASE_URL", "https://api.cloud.vexa.ai")).rstrip("/")
        self._api_key = api_key or os.getenv("VEXA_API_KEY")
        self.timeout = timeout

    @property
    def api_key(self) -> str:
        key = self._api_key or os.getenv("VEXA_API_KEY")
        if not key:
            raise ConfigurationError(
                "Vexa API key is missing. Please configure VEXA_API_KEY in environment variables."
            )
        return key

    def _get_headers(self) -> Dict[str, str]:
        """Constructs headers with Vexa authentication."""
        return {
            "X-API-Key": self.api_key,
            "Content-Type": "application/json",
            "Accept": "application/json"
        }

    async def create_bot(
        self,
        native_meeting_id: str,
        platform: str = "google_meet",
        bot_name: str = "Engineering Assistant",
        transcribe_enabled: bool = True,
        extra_payload: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Sends a bot into a meeting via POST /bots.
        
        Args:
            native_meeting_id: Platform meeting code (e.g. 'abc-defg-hij')
            platform: Platform name ('google_meet')
            bot_name: Display name for the bot in meeting
            transcribe_enabled: Whether audio transcription is enabled
            extra_payload: Optional additional parameters
            
        Returns:
            Dict containing Vexa bot dispatch response (e.g. {'id': '...', 'status': '...'})
        """
        url = f"{self.base_url}/bots"
        payload = {
            "platform": platform,
            "native_meeting_id": native_meeting_id,
            "bot_name": bot_name,
            "transcribe_enabled": transcribe_enabled,
            **(extra_payload or {})
        }

        logger.info(f"[VEXA CLIENT] Requesting bot for platform='{platform}', native_meeting_id='{native_meeting_id}'")

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(
                    url,
                    json=payload,
                    headers=self._get_headers()
                )

            if response.status_code in (200, 201, 202):
                data = response.json()
                logger.info(f"[VEXA CLIENT] Bot created successfully for native_meeting_id='{native_meeting_id}'")
                return data

            if response.status_code in (401, 403):
                logger.error(f"[VEXA CLIENT] Authentication failed (HTTP {response.status_code})")
                raise ProviderAuthError("Failed to authenticate with Vexa Cloud API. Please check VEXA_API_KEY.")

            if response.status_code == 422:
                err_detail = response.text
                logger.error(f"[VEXA CLIENT] Invalid request parameters: {err_detail}")
                raise TranscriptionError(f"Vexa rejected bot creation: invalid parameters", status_code=400)

            logger.error(f"[VEXA CLIENT] Provider returned error status HTTP {response.status_code}")
            raise ProviderUnavailableError(f"Vexa Cloud API returned error (HTTP {response.status_code})")

        except (httpx.ConnectTimeout, httpx.ReadTimeout, httpx.WriteTimeout, httpx.PoolTimeout) as exc:
            logger.error(f"[VEXA CLIENT] Request timeout: {exc}")
            raise ProviderTimeoutError("Connection to Vexa Cloud API timed out.")
        except httpx.ConnectError as exc:
            logger.error(f"[VEXA CLIENT] Connection error: {exc}")
            raise ProviderUnavailableError("Could not connect to Vexa Cloud API.")
        except (ConfigurationError, ProviderAuthError, ProviderUnavailableError, ProviderTimeoutError, TranscriptionError):
            raise
        except Exception as exc:
            logger.error(f"[VEXA CLIENT] Unexpected error: {exc}", exc_info=False)
            raise ProviderUnavailableError(f"Unexpected error communicating with Vexa API.")

    async def get_transcript(
        self,
        native_meeting_id: str,
        platform: str = "google_meet"
    ) -> Union[List[Dict[str, Any]], Dict[str, Any]]:
        """
        Retrieves transcript for a meeting via GET /transcripts/{platform}/{native_meeting_id}.
        
        Args:
            native_meeting_id: Platform meeting code (e.g. 'abc-defg-hij')
            platform: Platform name ('google_meet')
            
        Returns:
            Raw response containing transcript data/segments.
        """
        url = f"{self.base_url}/transcripts/{platform}/{native_meeting_id}"
        logger.info(f"[VEXA CLIENT] Requesting transcript for platform='{platform}', native_meeting_id='{native_meeting_id}'")

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.get(
                    url,
                    headers=self._get_headers()
                )

            if response.status_code == 200:
                data = response.json()
                logger.info(f"[VEXA CLIENT] Transcript retrieved for native_meeting_id='{native_meeting_id}'")
                return data

            if response.status_code in (401, 403):
                logger.error(f"[VEXA CLIENT] Authentication failed on transcript retrieval (HTTP {response.status_code})")
                raise ProviderAuthError("Failed to authenticate with Vexa Cloud API.")

            if response.status_code == 404:
                logger.info(f"[VEXA CLIENT] No transcript found or bot not ready yet for native_meeting_id='{native_meeting_id}'")
                # Return empty list or raise not found
                return []

            logger.error(f"[VEXA CLIENT] Transcript request returned HTTP {response.status_code}")
            raise ProviderUnavailableError(f"Vexa Cloud API returned error (HTTP {response.status_code})")

        except (httpx.ConnectTimeout, httpx.ReadTimeout, httpx.WriteTimeout, httpx.PoolTimeout) as exc:
            logger.error(f"[VEXA CLIENT] Request timeout on get_transcript: {exc}")
            raise ProviderTimeoutError("Connection to Vexa Cloud API timed out.")
        except httpx.ConnectError as exc:
            logger.error(f"[VEXA CLIENT] Connection error on get_transcript: {exc}")
            raise ProviderUnavailableError("Could not connect to Vexa Cloud API.")
        except (ConfigurationError, ProviderAuthError, ProviderUnavailableError, ProviderTimeoutError, TranscriptionError):
            raise
        except Exception as exc:
            logger.error(f"[VEXA CLIENT] Unexpected error on get_transcript: {exc}", exc_info=False)
            raise ProviderUnavailableError("Unexpected error communicating with Vexa API.")

    async def stop_bot(self, bot_id: str) -> Dict[str, Any]:
        """Stops a running bot via DELETE /bots/{bot_id}."""
        url = f"{self.base_url}/bots/{bot_id}"
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.delete(url, headers=self._get_headers())
            if response.status_code in (200, 204):
                return {"status": "stopped", "bot_id": bot_id}
            return {"status": "unknown", "statusCode": response.status_code}
        except Exception as exc:
            logger.error(f"[VEXA CLIENT] Error stopping bot {bot_id}: {exc}")
            return {"status": "error", "error": str(exc)}
