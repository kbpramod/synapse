import os
from typing import Optional
from src.transcription.base import TranscriptProvider
from src.transcription.vexa.provider import VexaTranscriptProvider
from src.transcription.vexa.client import VexaClient

_default_provider: Optional[TranscriptProvider] = None


def get_transcript_provider(provider_type: Optional[str] = None) -> TranscriptProvider:
    """
    Factory to obtain the configured TranscriptProvider.
    
    Defaults to VexaTranscriptProvider, enabling clean modular switching in the future.
    """
    global _default_provider
    provider_name = (provider_type or os.getenv("TRANSCRIPTION_PROVIDER", "vexa")).lower()

    if provider_name == "vexa":
        return VexaTranscriptProvider()
    
    # Fallback to Vexa
    return VexaTranscriptProvider()
