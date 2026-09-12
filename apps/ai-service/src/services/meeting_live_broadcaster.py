import json
import asyncio
import logging
from typing import Dict, Set, List, Optional, Any, AsyncGenerator
from fastapi import WebSocket

from src.schemas.live_meeting import (
    LiveTranscriptSegment,
    LiveTranscriptBufferResponse,
    LiveChunkIngestRequest
)
from src.transcription.vexa.live import VexaLiveClient

logger = logging.getLogger(__name__)


class MeetingLiveBroadcaster:
    """
    Central hub managing real-time transcript streaming from Vexa to frontend clients.
    
    Responsibilities:
    - Multiplexes upstream Vexa WebSocket listeners for all active meetings.
    - Maintains an in-memory chronological segment buffer per meeting for instant catch-up.
    - Reconciles live drafts (completed=False) with final transcripts (completed=True).
    - Broadcasts live events to frontend WebSocket and Server-Sent Events (SSE) subscribers.
    """

    def __init__(self):
        # meeting_id -> list of LiveTranscriptSegment
        self._buffers: Dict[str, List[LiveTranscriptSegment]] = {}

        # meeting_id -> set of detected speaker names
        self._speakers: Dict[str, Set[str]] = {}

        # native_meeting_id -> internal meeting_id
        self._native_to_meeting: Dict[str, str] = {}

        # meeting_id -> set of active frontend WebSockets
        self._ws_subscribers: Dict[str, Set[WebSocket]] = {}

        # meeting_id -> set of active SSE queues
        self._sse_subscribers: Dict[str, Set[asyncio.Queue]] = {}

        # Locks
        self._lock = asyncio.Lock()

        # Upstream Vexa live client
        self._vexa_client = VexaLiveClient(on_segment=self._on_upstream_segment)

    def register_meeting_mapping(self, meeting_id: str, native_meeting_id: str) -> None:
        """Associates a platform native meeting ID (e.g. 'abc-defg-hij') with internal UUID."""
        if native_meeting_id:
            self._native_to_meeting[native_meeting_id] = meeting_id
            logger.info(f"[BROADCASTER] Mapped native_id='{native_meeting_id}' -> meeting_id='{meeting_id}'")

    def resolve_meeting_id(self, identifier: str) -> str:
        """Resolves internal meeting_id if native_meeting_id was passed."""
        return self._native_to_meeting.get(identifier, identifier)

    async def ensure_upstream_subscribed(self, native_meeting_id: str, meeting_id: str, platform: str = "google_meet") -> None:
        """Subscribes to Vexa upstream WebSocket for this meeting."""
        self.register_meeting_mapping(meeting_id=meeting_id, native_meeting_id=native_meeting_id)
        try:
            await self._vexa_client.subscribe(native_meeting_id=native_meeting_id, platform=platform)
        except Exception as exc:
            logger.warning(f"[BROADCASTER] Failed to subscribe upstream Vexa client: {exc}")

    async def _on_upstream_segment(self, native_meeting_id: str, segment: LiveTranscriptSegment) -> None:
        """Callback invoked whenever Vexa live gateway sends a transcript frame."""
        meeting_id = self.resolve_meeting_id(native_meeting_id)
        logger.debug(f"[BROADCASTER] Inbound segment from Vexa for meeting '{meeting_id}': [{segment.speaker}] {segment.text}")
        await self.broadcast_segment(meeting_id=meeting_id, segment=segment)

    async def broadcast_segment(self, meeting_id: str, segment: LiveTranscriptSegment) -> None:
        """
        Ingests a transcript segment into the buffer and broadcasts to all connected clients.
        Reconciles live drafts in-place.
        """
        async with self._lock:
            if meeting_id not in self._buffers:
                self._buffers[meeting_id] = []
                self._speakers[meeting_id] = set()

            buf = self._buffers[meeting_id]
            speakers = self._speakers[meeting_id]

            if segment.speaker:
                speakers.add(segment.speaker)

            # Reconcile segment in buffer:
            # 1. Match by exact segment ID
            existing_idx = next((i for i, s in enumerate(buf) if s.id == segment.id), None)

            # 2. Match by last utterance if same speaker and was not finalized
            if existing_idx is None and buf and not buf[-1].completed and buf[-1].speaker == segment.speaker:
                existing_idx = len(buf) - 1

            if existing_idx is not None:
                # Update existing segment in place
                buf[existing_idx] = segment
            else:
                # Append new segment
                buf.append(segment)

        # Broadcast outside of buffer lock
        await self._dispatch_to_subscribers(meeting_id, segment)

    async def _dispatch_to_subscribers(self, meeting_id: str, segment: LiveTranscriptSegment) -> None:
        """Pushes segment to all registered WebSockets and SSE queues for meeting_id."""
        seg_dict = segment.model_dump(by_alias=True)
        payload = {
            "type": "transcript",
            "meeting_id": meeting_id,
            "segment": seg_dict
        }

        # Broadcast to WebSocket clients
        ws_list = list(self._ws_subscribers.get(meeting_id, set()))
        dead_ws = []
        for ws in ws_list:
            try:
                await ws.send_json(payload)
            except Exception as e:
                logger.debug(f"[BROADCASTER] Failed to send to WS client: {e}")
                dead_ws.append(ws)

        if dead_ws:
            for d in dead_ws:
                self._ws_subscribers.get(meeting_id, set()).discard(d)

        # Broadcast to SSE queues
        sse_list = list(self._sse_subscribers.get(meeting_id, set()))
        dead_queues = []
        for q in sse_list:
            try:
                q.put_nowait({
                    "event": "transcript",
                    "data": json.dumps(payload)
                })
            except asyncio.QueueFull:
                logger.warning(f"[BROADCASTER] SSE queue full for client in meeting {meeting_id}")
            except Exception as e:
                logger.debug(f"[BROADCASTER] Failed to enqueue SSE item: {e}")
                dead_queues.append(q)

        if dead_queues:
            for dq in dead_queues:
                self._sse_subscribers.get(meeting_id, set()).discard(dq)

    async def connect_websocket(self, meeting_id: str, websocket: WebSocket) -> None:
        """Handles new WebSocket client handshake and delivers backlog catch-up."""
        await websocket.accept()

        async with self._lock:
            if meeting_id not in self._ws_subscribers:
                self._ws_subscribers[meeting_id] = set()
            self._ws_subscribers[meeting_id].add(websocket)

            buffer_snapshot = list(self._buffers.get(meeting_id, []))
            speakers_snapshot = list(self._speakers.get(meeting_id, set()))

        # Send connection confirmation
        await websocket.send_json({
            "type": "connected",
            "meeting_id": meeting_id,
            "speakers": speakers_snapshot,
            "total_segments": len(buffer_snapshot)
        })

        # Send catch-up buffer
        if buffer_snapshot:
            await websocket.send_json({
                "type": "catch_up",
                "meeting_id": meeting_id,
                "segments": [s.model_dump(by_alias=True) for s in buffer_snapshot]
            })

        logger.info(f"[BROADCASTER] WebSocket client connected to meeting '{meeting_id}' (catch-up={len(buffer_snapshot)} segments)")

    def disconnect_websocket(self, meeting_id: str, websocket: WebSocket) -> None:
        """Removes a disconnected WebSocket client."""
        if meeting_id in self._ws_subscribers:
            self._ws_subscribers[meeting_id].discard(websocket)
            logger.info(f"[BROADCASTER] WebSocket client disconnected from meeting '{meeting_id}'")

    async def subscribe_sse(self, meeting_id: str) -> AsyncGenerator[str, None]:
        """
        Async generator for Server-Sent Events (SSE).
        Sends initial connection & catch_up frames, then streams live events and periodic keepalives.
        """
        queue: asyncio.Queue = asyncio.Queue(maxsize=100)

        async with self._lock:
            if meeting_id not in self._sse_subscribers:
                self._sse_subscribers[meeting_id] = set()
            self._sse_subscribers[meeting_id].add(queue)

            buffer_snapshot = list(self._buffers.get(meeting_id, []))
            speakers_snapshot = list(self._speakers.get(meeting_id, set()))

        try:
            # 1. Yield connected event
            connected_data = json.dumps({
                "type": "connected",
                "meeting_id": meeting_id,
                "speakers": speakers_snapshot,
                "total_segments": len(buffer_snapshot)
            })
            yield f"event: connected\ndata: {connected_data}\n\n"

            # 2. Yield catch-up event
            if buffer_snapshot:
                catch_up_data = json.dumps({
                    "type": "catch_up",
                    "meeting_id": meeting_id,
                    "segments": [s.model_dump(by_alias=True) for s in buffer_snapshot]
                })
                yield f"event: catch_up\ndata: {catch_up_data}\n\n"

            # 3. Stream loop
            while True:
                try:
                    # Wait for next item or heartbeat timeout
                    item = await asyncio.wait_for(queue.get(), timeout=15.0)
                    yield f"event: {item['event']}\ndata: {item['data']}\n\n"
                except asyncio.TimeoutError:
                    # Send comment keepalive to prevent browser/proxy connection drop
                    yield ": ping\n\n"

        finally:
            async with self._lock:
                if meeting_id in self._sse_subscribers:
                    self._sse_subscribers[meeting_id].discard(queue)
            logger.info(f"[BROADCASTER] SSE client disconnected from meeting '{meeting_id}'")

    def get_live_buffer(self, meeting_id: str, status: str = "active") -> LiveTranscriptBufferResponse:
        """Returns snapshot of current accumulated live segments and speakers."""
        resolved_id = self.resolve_meeting_id(meeting_id)
        buf = list(self._buffers.get(resolved_id, []))
        speakers = list(self._speakers.get(resolved_id, set()))

        has_subscribers = bool(
            self._ws_subscribers.get(resolved_id) or self._sse_subscribers.get(resolved_id)
        )

        return LiveTranscriptBufferResponse(
            meeting_id=resolved_id,
            status=status,
            segments=buf,
            speakers=speakers,
            total_segments=len(buf),
            is_live=has_subscribers or (status in ("active", "joining"))
        )

    async def ingest_chunk(self, meeting_id: str, request: LiveChunkIngestRequest) -> LiveTranscriptSegment:
        """Manually ingests a chunk into the live stream (useful for mock testing, webhooks, or external bot bridges)."""
        resolved_id = self.resolve_meeting_id(meeting_id)

        seg_id = request.id or f"chunk_{resolved_id}_{len(self._buffers.get(resolved_id, [])) + 1}"
        start_time = request.start_time
        timestamp = f"{int((start_time or 0) // 60):02d}:{int((start_time or 0) % 60):02d}" if start_time is not None else None

        segment = LiveTranscriptSegment(
            id=seg_id,
            speaker=request.speaker,
            text=request.text,
            completed=request.completed,
            start_time=request.start_time,
            end_time=request.end_time,
            timestamp=timestamp
        )

        await self.broadcast_segment(meeting_id=resolved_id, segment=segment)
        return segment

    async def broadcast_status(self, meeting_id: str, status: str) -> None:
        """Broadcasts meeting status change (e.g. active, completed) to all connected clients."""
        resolved_id = self.resolve_meeting_id(meeting_id)
        payload = {
            "type": "status",
            "meeting_id": resolved_id,
            "status": status
        }
        ws_list = list(self._ws_subscribers.get(resolved_id, set()))
        for ws in ws_list:
            try:
                await ws.send_json(payload)
            except Exception:
                pass

        sse_list = list(self._sse_subscribers.get(resolved_id, set()))
        for q in sse_list:
            try:
                q.put_nowait({"event": "status", "data": json.dumps(payload)})
            except Exception:
                pass


# Global singleton instance
meeting_live_broadcaster = MeetingLiveBroadcaster()
