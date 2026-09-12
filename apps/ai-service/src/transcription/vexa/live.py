import os
import json
import asyncio
import logging
from typing import Optional, Dict, Any, Set, Tuple, Callable, Awaitable
from urllib.parse import urlencode, urlparse, urlunparse
import websockets
from websockets.exceptions import ConnectionClosed

from src.schemas.live_meeting import LiveTranscriptSegment
from src.transcription.exceptions import ConfigurationError

logger = logging.getLogger(__name__)


def _format_seconds(seconds: Optional[float]) -> Optional[str]:
    """Helper to format float seconds into 'MM:SS' format."""
    if seconds is None:
        return None
    mins = int(seconds // 60)
    secs = int(seconds % 60)
    return f"{mins:02d}:{secs:02d}"


def _parse_time(val: Any) -> Optional[float]:
    """Helper to safely parse timestamps into float seconds."""
    if val is None:
        return None
    if isinstance(val, (int, float)):
        return float(val)
    if isinstance(val, str):
        try:
            return float(val)
        except ValueError:
            parts = val.strip().split(":")
            if len(parts) == 2:
                try:
                    return float(parts[0]) * 60 + float(parts[1])
                except ValueError:
                    pass
            elif len(parts) == 3:
                try:
                    return float(parts[0]) * 3600 + float(parts[1]) * 60 + float(parts[2])
                except ValueError:
                    pass
    return None


class VexaLiveClient:
    """
    Asynchronous WebSocket client connecting to the Vexa Cloud live transcript gateway (/ws).
    
    Subscribes to active meeting streams using platform & native meeting ID,
    normalizes real-time draft/final frames, and dispatches them to registered handlers.
    """

    def __init__(
        self,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        on_segment: Optional[Callable[[str, LiveTranscriptSegment], Awaitable[None]]] = None,
        reconnect_delay: float = 2.0,
        max_reconnect_delay: float = 30.0
    ):
        raw_url = (base_url or os.getenv("VEXA_API_BASE_URL", "https://api.cloud.vexa.ai")).rstrip("/")
        self.base_url = raw_url
        self._api_key = api_key or os.getenv("VEXA_API_KEY")
        self.on_segment = on_segment
        self.reconnect_delay = reconnect_delay
        self.max_reconnect_delay = max_reconnect_delay

        # Active subscriptions: set of (platform, native_meeting_id)
        self._subscriptions: Set[Tuple[str, str]] = set()

        # State management
        self._ws: Optional[websockets.ClientConnection] = None
        self._worker_task: Optional[asyncio.Task] = None
        self._running: bool = False
        self._lock = asyncio.Lock()

    @property
    def api_key(self) -> str:
        key = self._api_key or os.getenv("VEXA_API_KEY")
        if not key:
            raise ConfigurationError(
                "Vexa API key is missing. Please configure VEXA_API_KEY in environment variables."
            )
        return key

    def get_ws_url(self) -> str:
        """Constructs the WebSocket URL from base_url, appending /ws and query auth."""
        parsed = urlparse(self.base_url)
        scheme = "wss" if parsed.scheme == "https" else "ws"
        netloc = parsed.netloc or parsed.path
        path = parsed.path if parsed.netloc else ""
        if not path.endswith("/ws"):
            path = f"{path.rstrip('/')}/ws"

        # Embed api_key in query params for proxies that omit custom upgrade headers
        query = urlencode({"api_key": self.api_key})
        return urlunparse((scheme, netloc, path, "", query, ""))

    async def subscribe(self, native_meeting_id: str, platform: str = "google_meet") -> None:
        """
        Subscribes to live transcripts for a meeting.
        If already connected, sends the subscription message immediately.
        """
        async with self._lock:
            sub = (platform, native_meeting_id)
            if sub in self._subscriptions:
                logger.info(f"[VEXA LIVE] Already subscribed to platform='{platform}', native_id='{native_meeting_id}'")
                return

            self._subscriptions.add(sub)
            logger.info(f"[VEXA LIVE] Subscribed to platform='{platform}', native_id='{native_meeting_id}'")

            # Send subscribe command if connection is alive
            if self._ws and not self._ws.closed:
                await self._send_subscribe(platform, native_meeting_id)

            self._ensure_worker()

    async def unsubscribe(self, native_meeting_id: str, platform: str = "google_meet") -> None:
        """Unsubscribes from live transcripts for a meeting."""
        async with self._lock:
            sub = (platform, native_meeting_id)
            self._subscriptions.discard(sub)
            logger.info(f"[VEXA LIVE] Unsubscribed from platform='{platform}', native_id='{native_meeting_id}'")

            if not self._subscriptions and self._ws and not self._ws.closed:
                logger.info("[VEXA LIVE] No remaining subscriptions; idling connection.")

    def _ensure_worker(self) -> None:
        """Ensures the background WebSocket connection worker is active."""
        if not self._running:
            self._running = True
            self._worker_task = asyncio.create_task(self._run_loop())

    async def start(self) -> None:
        """Explicitly starts the background WebSocket connection."""
        async with self._lock:
            self._ensure_worker()

    async def stop(self) -> None:
        """Gracefully terminates the WebSocket client and closes active connection."""
        async with self._lock:
            self._running = False
            if self._ws and not self._ws.closed:
                await self._ws.close()
                self._ws = None

            if self._worker_task and not self._worker_task.done():
                self._worker_task.cancel()
                try:
                    await self._worker_task
                except asyncio.CancelledError:
                    pass
                self._worker_task = None
            logger.info("[VEXA LIVE] VexaLiveClient stopped successfully.")

    async def _send_subscribe(self, platform: str, native_meeting_id: str) -> None:
        """Sends the JSON subscription frame to Vexa gateway."""
        if not self._ws or self._ws.closed:
            return
        payload = {
            "action": "subscribe",
            "meetings": [
                {
                    "platform": platform,
                    "native_id": native_meeting_id
                }
            ]
        }
        try:
            await self._ws.send(json.dumps(payload))
            logger.info(f"[VEXA LIVE] Dispatched subscribe frame for '{native_meeting_id}'")
        except Exception as exc:
            logger.warning(f"[VEXA LIVE] Failed to send subscribe frame: {exc}")

    async def _run_loop(self) -> None:
        """Core resilient connection loop with exponential backoff."""
        delay = self.reconnect_delay
        while self._running:
            try:
                ws_url = self.get_ws_url()
                headers = {"X-API-Key": self.api_key}

                logger.info(f"[VEXA LIVE] Connecting to Vexa live gateway at {self.base_url}/ws...")
                async with websockets.connect(
                    ws_url,
                    additional_headers=headers,
                    open_timeout=10,
                    ping_interval=20,
                    ping_timeout=20
                ) as ws:
                    self._ws = ws
                    delay = self.reconnect_delay  # reset backoff on successful handshake
                    logger.info("[VEXA LIVE] Connected to Vexa live WebSocket gateway.")

                    # Re-subscribe to all active meetings
                    async with self._lock:
                        for platform, native_id in list(self._subscriptions):
                            await self._send_subscribe(platform, native_id)

                    # Ingest frames
                    async for raw_message in ws:
                        if not self._running:
                            break
                        await self._handle_raw_message(raw_message)

            except ConnectionClosed as exc:
                logger.warning(f"[VEXA LIVE] WebSocket connection closed ({exc.code}): {exc.reason}")
            except asyncio.CancelledError:
                logger.info("[VEXA LIVE] Live worker loop cancelled.")
                break
            except Exception as exc:
                logger.error(f"[VEXA LIVE] Error in WebSocket worker: {exc}", exc_info=False)

            # Reconnection backoff
            if self._running:
                logger.info(f"[VEXA LIVE] Reconnecting in {delay:.1f}s...")
                await asyncio.sleep(delay)
                delay = min(delay * 1.5, self.max_reconnect_delay)

    async def _handle_raw_message(self, raw_message: Any) -> None:
        """Parses raw text/json frame from Vexa gateway into canonical live segments."""
        try:
            if isinstance(raw_message, bytes):
                raw_message = raw_message.decode("utf-8")
            data = json.loads(raw_message)
        except Exception as exc:
            logger.warning(f"[VEXA LIVE] Non-JSON or corrupted frame received: {exc}")
            return

        # Acknowledgment frame
        msg_type = data.get("type") or data.get("action")
        if msg_type == "subscribed":
            logger.info(f"[VEXA LIVE] Subscription confirmed: {data.get('meetings') or data}")
            return

        # Segments frame
        native_id = (
            data.get("native_id")
            or data.get("native_meeting_id")
            or data.get("meeting_id")
            or ""
        )

        raw_segments = data.get("segments") or []
        if not raw_segments and ("text" in data or "message" in data):
            raw_segments = [data]

        for seg in raw_segments:
            if not isinstance(seg, dict):
                continue

            text = str(seg.get("text") or seg.get("content") or seg.get("message") or "").strip()
            if not text:
                continue

            speaker = str(
                seg.get("speaker")
                or seg.get("speaker_name")
                or seg.get("name")
                or "Speaker"
            ).strip()

            completed = bool(seg.get("completed", False))
            start_time = _parse_time(seg.get("startTime") or seg.get("start_time") or seg.get("start"))
            end_time = _parse_time(seg.get("endTime") or seg.get("end_time") or seg.get("end"))
            timestamp = seg.get("timestamp") or _format_seconds(start_time)

            seg_id = str(
                seg.get("id")
                or seg.get("segment_id")
                or f"{native_id}_{speaker}_{int((start_time or 0) * 100)}"
            )

            live_segment = LiveTranscriptSegment(
                id=seg_id,
                speaker=speaker,
                text=text,
                completed=completed,
                start_time=start_time,
                end_time=end_time,
                timestamp=timestamp
            )

            if self.on_segment:
                try:
                    await self.on_segment(native_id, live_segment)
                except Exception as callback_err:
                    logger.error(f"[VEXA LIVE] Error in on_segment callback: {callback_err}", exc_info=True)
