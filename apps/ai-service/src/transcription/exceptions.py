class TranscriptionError(Exception):
    """Base exception for all transcription errors."""
    def __init__(self, message: str, status_code: int = 500):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class InvalidMeetingUrlError(TranscriptionError):
    """Raised when the provided meeting URL cannot be parsed or is invalid."""
    def __init__(self, message: str = "Invalid Google Meet URL format. Expected URL like https://meet.google.com/abc-defg-hij"):
        super().__init__(message, status_code=400)


class ConfigurationError(TranscriptionError):
    """Raised when transcription provider configuration (e.g. API key) is missing or invalid."""
    def __init__(self, message: str = "Transcription provider configuration is missing or invalid."):
        super().__init__(message, status_code=500)


class ProviderAuthError(TranscriptionError):
    """Raised when authentication with the transcription provider fails."""
    def __init__(self, message: str = "Authentication failed with transcription provider."):
        super().__init__(message, status_code=502)


class ProviderUnavailableError(TranscriptionError):
    """Raised when the transcription provider API is unreachable or returns a server error."""
    def __init__(self, message: str = "Transcription provider service is currently unavailable."):
        super().__init__(message, status_code=502)


class ProviderTimeoutError(TranscriptionError):
    """Raised when a request to the transcription provider times out."""
    def __init__(self, message: str = "Request to transcription provider timed out."):
        super().__init__(message, status_code=504)


class TranscriptNotFoundError(TranscriptionError):
    """Raised when a requested transcript is not found on the provider."""
    def __init__(self, message: str = "Transcript not found."):
        super().__init__(message, status_code=404)


class TranscriptNotReadyError(TranscriptionError):
    """Raised when a bot is still joining or transcript is not yet available."""
    def __init__(self, message: str = "Transcript is not available yet."):
        super().__init__(message, status_code=200)
