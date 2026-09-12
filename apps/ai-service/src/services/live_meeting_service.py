import asyncio
import logging
from datetime import datetime
from typing import Optional, Dict, Any, List
from sqlalchemy.orm import Session

from db.database import SessionLocal
from models.meeting import Meeting
from ai.graphs.live_meeting_graph import live_meeting_graph, LiveMeetingGraphState
from schemas.live_meeting import (
    LiveMeetingWindowRequest,
    LiveMeetingWindowResponse,
    LiveMeetingStateResponse,
    LiveDecisionItem,
    LiveTaskItem
)
from transcription.factory import get_transcript_provider
from transcription.utils import parse_google_meet_url
from services.meeting_live_broadcaster import meeting_live_broadcaster

logger = logging.getLogger(__name__)


class LiveMeetingService:
    """
    Service coordinating real-time meeting intelligence using LangGraph.
    Processes rolling 4-minute transcript slices, preserving the 4 context elements:
      1. 4-minute cadence
      2. Previous minutes summary
      3. Conditional RAG context
      4. Meeting state (members, recent decisions, tasks)
    """

    def __init__(self):
        # In-memory registry of active live meeting background polling tasks
        self._active_sessions: Dict[str, asyncio.Task] = {}

    async def process_window(
        self,
        db: Session,
        meeting_id: str,
        request: Optional[LiveMeetingWindowRequest] = None
    ) -> LiveMeetingWindowResponse:
        """
        Executes the LangGraph 4-minute live meeting pipeline for a given meeting slice.
        """
        meeting = db.query(Meeting).filter(
            (Meeting.id == meeting_id) | (Meeting.native_meeting_id == meeting_id) | (Meeting.external_meeting_id == meeting_id)
        ).first()

        if not meeting:
            raise ValueError(f"Meeting with id '{meeting_id}' not found.")

        metadata = dict(meeting.metadata_json or {})
        live_windows = metadata.get("live_windows", [])
        window_index = len(live_windows) + 1

        req = request or LiveMeetingWindowRequest()
        
        # Calculate time range
        last_window_time = metadata.get("last_window_time") or 0.0
        window_start = req.window_start_sec if req.window_start_sec is not None else float(last_window_time)
        window_end = req.window_end_sec if req.window_end_sec is not None else (window_start + 240.0)

        # Obtain transcript content
        transcript_text = req.transcript or ""
        segments_dicts = []

        if req.segments:
            segments_dicts = [
                {"speaker": s.speaker, "text": s.text, "timestamp": s.timestamp}
                for s in req.segments
            ]
            if not transcript_text:
                transcript_text = "\n".join(
                    f"[{s.timestamp or '00:00'}] {s.speaker}: {s.text}" for s in req.segments
                )

        # If no transcript provided in request, attempt to pull latest segments from transcription provider
        if not transcript_text:
            transcript_text, segments_dicts = await self._fetch_recent_provider_transcript(
                meeting=meeting,
                start_sec=window_start,
                end_sec=window_end
            )

        if not transcript_text.strip():
            transcript_text = f"(No speech detected between {int(window_start//60)}m and {int(window_end//60)}m)"

        # Prepare 4-part context window state
        initial_state: LiveMeetingGraphState = {
            "meeting_id": meeting.id,
            "organization_id": meeting.organization_id,
            "repository_id": meeting.repository_id,
            "window_index": window_index,
            "window_start_sec": window_start,
            "window_end_sec": window_end,
            "current_window_transcript": transcript_text,
            "current_window_segments": segments_dicts,
            "previous_minutes_summary": meeting.summary or "Meeting just started.",
            "force_rag": req.force_rag or False,
            "members": list(meeting.participants_json or []),
            "recent_decisions": list(meeting.decisions_json or []),
            "tasks": list(meeting.action_items_json or []),
        }

        logger.info(f"[LIVE MEETING] Running LangGraph for meeting={meeting.id}, window={window_index} ({window_start}s - {window_end}s)")

        # Invoke LangGraph
        final_state = await live_meeting_graph.ainvoke(initial_state)

        # Extract updated state
        updated_members = final_state.get("members", [])
        updated_decisions = final_state.get("recent_decisions", [])
        updated_tasks = final_state.get("tasks", [])
        accumulated_summary = final_state.get("accumulated_summary") or meeting.summary or ""
        window_summary = final_state.get("window_summary") or f"Discussion in window #{window_index}"
        rag_context = final_state.get("rag_context") or []

        # Record window in history metadata
        live_windows.append({
            "window_index": window_index,
            "start_sec": window_start,
            "end_sec": window_end,
            "window_summary": window_summary,
            "rag_used": bool(rag_context),
            "rag_context_count": len(rag_context),
            "new_decisions": final_state.get("new_decisions", []),
            "new_tasks": final_state.get("new_tasks", []),
            "processed_at": datetime.utcnow().isoformat()
        })
        metadata["live_windows"] = live_windows
        metadata["last_window_time"] = window_end

        # Persist to database
        meeting.participants_json = updated_members
        meeting.decisions_json = updated_decisions
        meeting.action_items_json = updated_tasks
        meeting.summary = accumulated_summary
        meeting.metadata_json = metadata
        if meeting.status == "joining":
            meeting.status = "active"

        db.commit()
        db.refresh(meeting)

        logger.info(
            f"[LIVE MEETING] Window {window_index} complete for {meeting.id}. "
            f"Members: {len(updated_members)}, Decisions: {len(updated_decisions)}, Tasks: {len(updated_tasks)}"
        )

        return LiveMeetingWindowResponse(
            meeting_id=meeting.id,
            window_index=window_index,
            window_start_sec=window_start,
            window_end_sec=window_end,
            window_summary=window_summary,
            accumulated_summary=accumulated_summary,
            members=updated_members,
            recent_decisions=[LiveDecisionItem(**d) for d in updated_decisions],
            tasks=[LiveTaskItem(**t) for t in updated_tasks],
            rag_used=bool(rag_context),
            rag_context_count=len(rag_context)
        )

    def get_live_state(self, db: Session, meeting_id: str) -> LiveMeetingStateResponse:
        """Retrieves real-time meeting state for frontend dashboards."""
        meeting = db.query(Meeting).filter(
            (Meeting.id == meeting_id) | (Meeting.native_meeting_id == meeting_id) | (Meeting.external_meeting_id == meeting_id)
        ).first()

        if not meeting:
            raise ValueError(f"Meeting with id '{meeting_id}' not found.")

        metadata = meeting.metadata_json or {}
        live_windows = metadata.get("live_windows", [])

        is_active = meeting.id in self._active_sessions and not self._active_sessions[meeting.id].done()

        return LiveMeetingStateResponse(
            meeting_id=meeting.id,
            title=meeting.title or "Engineering Meeting",
            status=meeting.status,
            members=meeting.participants_json or [],
            summary=meeting.summary,
            recent_decisions=[LiveDecisionItem(**d) for d in (meeting.decisions_json or [])],
            tasks=[LiveTaskItem(**t) for t in (meeting.action_items_json or [])],
            total_windows_processed=len(live_windows),
            last_window_time=metadata.get("last_window_time"),
            is_live_active=is_active
        )

    def start_live_session(self, meeting_id: str, interval_seconds: int = 240) -> None:
        """
        Starts the background periodic polling runner (default 240s = 4 minutes)
        that periodically pulls new transcript segments and triggers LangGraph.
        """
        if meeting_id in self._active_sessions and not self._active_sessions[meeting_id].done():
            logger.warning(f"[LIVE MEETING] Session {meeting_id} already has an active runner.")
            return

        task = asyncio.create_task(
            self._live_session_loop(meeting_id=meeting_id, interval_seconds=interval_seconds)
        )
        self._active_sessions[meeting_id] = task
        logger.info(f"[LIVE MEETING] Started live runner for {meeting_id} with interval={interval_seconds}s")

    def stop_live_session(self, meeting_id: str) -> bool:
        """Stops the live background runner for a meeting."""
        task = self._active_sessions.pop(meeting_id, None)
        if task and not task.done():
            task.cancel()
            logger.info(f"[LIVE MEETING] Stopped live runner for {meeting_id}")
            return True
        return False

    async def _live_session_loop(self, meeting_id: str, interval_seconds: int) -> None:
        """Periodic background loop executing window analysis every 4 minutes."""
        try:
            while True:
                await asyncio.sleep(interval_seconds)
                db = SessionLocal()
                try:
                    meeting = db.query(Meeting).filter(Meeting.id == meeting_id).first()
                    if not meeting or meeting.status in ("completed", "failed"):
                        logger.info(f"[LIVE MEETING] Meeting {meeting_id} ended (status={getattr(meeting, 'status', None)}). Exiting runner.")
                        break

                    logger.info(f"[LIVE MEETING RUNNER] Triggering periodic {interval_seconds}s window for {meeting_id}")
                    await self.process_window(db=db, meeting_id=meeting_id)
                except Exception as loop_err:
                    logger.error(f"[LIVE MEETING RUNNER ERROR] Error during live processing: {loop_err}", exc_info=True)
                finally:
                    db.close()
        except asyncio.CancelledError:
            logger.info(f"[LIVE MEETING RUNNER] Live session loop cancelled for {meeting_id}")

    async def _fetch_recent_provider_transcript(
        self,
        meeting: Meeting,
        start_sec: float,
        end_sec: float
    ) -> tuple[str, List[Dict[str, Any]]]:
        """Fetches and filters segments falling into [start_sec, end_sec] from the transcription provider."""
        try:
            # 1. Fast-path: Check real-time broadcaster buffer
            live_snapshot = meeting_live_broadcaster.get_live_buffer(meeting.id)
            if live_snapshot.segments:
                window_segments = [
                    s for s in live_snapshot.segments
                    if s.start_time is None or (start_sec <= s.start_time <= end_sec)
                ]
                if not window_segments and live_snapshot.segments:
                    window_segments = live_snapshot.segments[-10:]

                if window_segments:
                    text_lines = [f"[{s.speaker}]: {s.text}" for s in window_segments]
                    segments_dicts = [
                        {"speaker": s.speaker, "text": s.text, "start_time": s.start_time}
                        for s in window_segments
                    ]
                    logger.info(f"[LIVE MEETING] Loaded {len(window_segments)} segments from real-time live broadcaster.")
                    return "\n".join(text_lines), segments_dicts

            # 2. Fallback to provider REST retrieval
            native_id = meeting.native_meeting_id
            if not native_id and meeting.meeting_url:
                try:
                    native_id = parse_google_meet_url(meeting.meeting_url)
                except Exception:
                    native_id = meeting.id

            if not native_id:
                return "", []

            provider = get_transcript_provider()
            canonical = await provider.get_transcript(
                native_meeting_id=native_id,
                platform=meeting.platform or "google_meet",
                meeting_id=meeting.id,
                title=meeting.title
            )

            if not canonical or not canonical.segments:
                return "", []

            # Filter segments within the time window
            window_segments = []
            for seg in canonical.segments:
                seg_start = seg.start_time
                if seg_start is None:
                    window_segments.append(seg)
                elif start_sec <= seg_start <= end_sec:
                    window_segments.append(seg)

            if not window_segments and canonical.segments:
                # Fallback: take latest 10 segments if timestamp was null
                window_segments = canonical.segments[-10:]

            text_lines = [f"[{s.speaker}]: {s.text}" for s in window_segments]
            segments_dicts = [{"speaker": s.speaker, "text": s.text, "start_time": s.start_time} for s in window_segments]
            
            return "\n".join(text_lines), segments_dicts
        except Exception as e:
            logger.warning(f"[LIVE MEETING] Failed to fetch provider transcript: {e}")
            return "", []


live_meeting_service = LiveMeetingService()
