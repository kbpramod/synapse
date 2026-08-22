from abc import ABC, abstractmethod
from typing import Optional, List, Dict, Any
from dataclasses import dataclass
from src.transcription.models import CanonicalTranscript


@dataclass
class ProviderBotInfo:
    """Standardized representation of meeting bot launch info."""
    bot_id: str
    status: str
    platform: str
    native_meeting_id: str
    raw_response: Optional[Dict[str, Any]] = None


class TranscriptProvider(ABC):
    """
    Abstract interface for meeting transcription providers.
    
    Decouples the core application from specific third-party transcription vendors
    (e.g., Vexa Cloud, self-hosted bots, custom ASR pipeline).
    """

    @abstractmethod
    async def start_meeting(
        self,
        meeting_url: str,
        native_meeting_id: str,
        title: Optional[str] = None,
        bot_name: str = "Engineering Assistant",
        **kwargs: Any
    ) -> ProviderBotInfo:
        """
        Dispatches a transcription bot to the specified meeting.
        
        Args:
            meeting_url: Full URL of the meeting
            native_meeting_id: Native platform meeting identifier (e.g. 'abc-defg-hij')
            title: Meeting title or description
            bot_name: Display name for the bot in the meeting
            **kwargs: Provider-specific options
            
        Returns:
            ProviderBotInfo with status and provider bot identifier.
        """
        pass

    @abstractmethod
    async def get_transcript(
        self,
        native_meeting_id: str,
        platform: str = "google_meet",
        meeting_id: Optional[str] = None,
        title: Optional[str] = None,
        **kwargs: Any
    ) -> CanonicalTranscript:
        """
        Retrieves and normalizes the meeting transcript into canonical format.
        
        Args:
            native_meeting_id: Platform meeting code (e.g. 'abc-defg-hij')
            platform: Meeting platform (default 'google_meet')
            meeting_id: Local meeting record UUID
            title: Meeting title
            **kwargs: Provider-specific options
            
        Returns:
            CanonicalTranscript with speaker-attributed segments.
        """
        pass

    @abstractmethod
    async def stop_meeting(
        self,
        bot_id: str,
        **kwargs: Any
    ) -> bool:
        """
        Instructs the transcription provider to stop recording / remove bot.
        """
        pass
