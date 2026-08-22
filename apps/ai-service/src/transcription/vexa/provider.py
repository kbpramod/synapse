import logging
from typing import Optional, Dict, Any, List, Union
from src.transcription.base import TranscriptProvider, ProviderBotInfo
from src.transcription.models import CanonicalTranscript, CanonicalTranscriptSegment
from src.transcription.vexa.client import VexaClient

logger = logging.getLogger(__name__)


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
            # If timestamp in format "MM:SS" or "HH:MM:SS"
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


class VexaTranscriptProvider(TranscriptProvider):
    """
    TranscriptProvider implementation integrating with Vexa Cloud API.
    """

    def __init__(self, client: Optional[VexaClient] = None):
        self.client = client or VexaClient()

    async def start_meeting(
        self,
        meeting_url: str,
        native_meeting_id: str,
        title: Optional[str] = None,
        bot_name: str = "Engineering Assistant",
        **kwargs: Any
    ) -> ProviderBotInfo:
        """
        Requests Vexa to send a bot to the meeting.
        """
        response = await self.client.create_bot(
            native_meeting_id=native_meeting_id,
            platform="google_meet",
            bot_name=bot_name,
            transcribe_enabled=True,
            extra_payload=kwargs
        )

        bot_id = str(
            response.get("id")
            or response.get("bot_id")
            or response.get("botId")
            or native_meeting_id
        )
        status = response.get("status") or "joining"

        logger.info(f"[VEXA PROVIDER] Dispatched bot_id='{bot_id}', status='{status}' for native_meeting_id='{native_meeting_id}'")

        return ProviderBotInfo(
            bot_id=bot_id,
            status=status,
            platform="google_meet",
            native_meeting_id=native_meeting_id,
            raw_response=response
        )

    async def get_transcript(
        self,
        native_meeting_id: str,
        platform: str = "google_meet",
        meeting_id: Optional[str] = None,
        title: Optional[str] = None,
        **kwargs: Any
    ) -> CanonicalTranscript:
        """
        Fetches transcript from Vexa and normalizes into CanonicalTranscript.
        """
        raw_data = await self.client.get_transcript(
            native_meeting_id=native_meeting_id,
            platform=platform
        )

        return self.normalize_transcript(
            raw_data=raw_data,
            meeting_id=meeting_id or native_meeting_id,
            title=title or "Engineering Sync",
            platform=platform
        )

    def normalize_transcript(
        self,
        raw_data: Union[List[Dict[str, Any]], Dict[str, Any]],
        meeting_id: str,
        title: str = "Engineering Sync",
        platform: str = "google_meet"
    ) -> CanonicalTranscript:
        """
        Normalizes any Vexa transcript response variant into CanonicalTranscript.
        """
        raw_segments: List[Dict[str, Any]] = []

        if isinstance(raw_data, list):
            raw_segments = raw_data
        elif isinstance(raw_data, dict):
            if "segments" in raw_data and isinstance(raw_data["segments"], list):
                raw_segments = raw_data["segments"]
            elif "transcript" in raw_data and isinstance(raw_data["transcript"], list):
                raw_segments = raw_data["transcript"]
            elif "data" in raw_data and isinstance(raw_data["data"], list):
                raw_segments = raw_data["data"]
            elif "text" in raw_data and isinstance(raw_data["text"], str):
                raw_segments = [{
                    "speaker": raw_data.get("speaker", "Speaker"),
                    "text": raw_data["text"],
                    "start_time": _parse_time(raw_data.get("start_time") or raw_data.get("startTime")),
                    "end_time": _parse_time(raw_data.get("end_time") or raw_data.get("endTime"))
                }]

        canonical_segments: List[CanonicalTranscriptSegment] = []
        participants_set = set()

        for seg in raw_segments:
            if not isinstance(seg, dict):
                continue
            
            speaker = str(
                seg.get("speaker")
                or seg.get("speaker_name")
                or seg.get("name")
                or "Unknown Speaker"
            ).strip()

            text = str(
                seg.get("text")
                or seg.get("content")
                or seg.get("message")
                or ""
            ).strip()

            if not text:
                continue

            start_time = _parse_time(
                seg.get("startTime")
                or seg.get("start_time")
                or seg.get("start")
                or seg.get("timestamp")
            )
            end_time = _parse_time(
                seg.get("endTime")
                or seg.get("end_time")
                or seg.get("end")
            )

            if speaker and speaker.lower() != "unknown speaker":
                participants_set.add(speaker)

            canonical_segments.append(
                CanonicalTranscriptSegment(
                    speaker=speaker,
                    text=text,
                    startTime=start_time,
                    endTime=end_time
                )
            )

        # Preserve participant order or sort
        participants = sorted(list(participants_set)) if participants_set else []

        return CanonicalTranscript(
            meetingId=meeting_id,
            title=title,
            platform=platform,
            participants=participants,
            segments=canonical_segments,
            status="active" if canonical_segments else "joining"
        )

    async def stop_meeting(self, bot_id: str, **kwargs: Any) -> bool:
        """Instructs Vexa to stop the meeting bot."""
        res = await self.client.stop_bot(bot_id)
        return res.get("status") == "stopped"
