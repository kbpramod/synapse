import re
from urllib.parse import urlparse
from src.transcription.exceptions import InvalidMeetingUrlError


# Standard Google Meet code pattern: 3 letters, 4 letters, 3 letters (e.g. abc-defg-hij)
MEET_CODE_HYPHENATED_REGEX = re.compile(r"^[a-z]{3}-[a-z]{4}-[a-z]{3}$", re.IGNORECASE)
MEET_CODE_RAW_REGEX = re.compile(r"^[a-z]{10}$", re.IGNORECASE)
MEET_PATH_REGEX = re.compile(r"([a-z]{3}-[a-z]{4}-[a-z]{3}|[a-z]{10})", re.IGNORECASE)


def parse_google_meet_url(url_or_code: str) -> str:
    """
    Extracts and normalizes the native Google Meet meeting ID from a URL or meeting code.
    
    Examples:
        - 'https://meet.google.com/abc-defg-hij' -> 'abc-defg-hij'
        - 'https://meet.google.com/abc-defg-hij?authuser=0' -> 'abc-defg-hij'
        - 'meet.google.com/abc-defg-hij/' -> 'abc-defg-hij'
        - 'http://meet.google.com/abc-defg-hij' -> 'abc-defg-hij'
        - 'abc-defg-hij' -> 'abc-defg-hij'
        - 'abcdefghij' -> 'abc-defg-hij'
        
    Raises:
        InvalidMeetingUrlError: If the URL or code is invalid or not a Google Meet meeting.
    """
    if not url_or_code or not isinstance(url_or_code, str):
        raise InvalidMeetingUrlError("Meeting URL cannot be empty.")

    cleaned = url_or_code.strip()
    if not cleaned:
        raise InvalidMeetingUrlError("Meeting URL cannot be empty.")

    # 1. Direct match on standard code e.g. "abc-defg-hij"
    if MEET_CODE_HYPHENATED_REGEX.match(cleaned):
        return cleaned.lower()

    # 2. Match unhyphenated 10-char code e.g. "abcdefghij" -> format as "abc-defg-hij"
    if MEET_CODE_RAW_REGEX.match(cleaned):
        code = cleaned.lower()
        return f"{code[:3]}-{code[3:7]}-{code[7:]}"

    # 3. Parse URL
    if not cleaned.startswith("http://") and not cleaned.startswith("https://"):
        # Prepend https:// for urlparse to correctly identify netloc and path
        cleaned = f"https://{cleaned}"

    parsed = urlparse(cleaned)
    domain = parsed.netloc.lower()

    if not (domain == "meet.google.com" or domain.endswith(".meet.google.com")):
        raise InvalidMeetingUrlError(f"Invalid domain '{domain}'. Expected a 'meet.google.com' URL.")

    # Extract code from URL path
    path = parsed.path.strip("/")
    # Check parts if lookup or direct code
    match = MEET_PATH_REGEX.search(path)
    if not match:
        raise InvalidMeetingUrlError(
            f"Could not extract a valid Google Meet meeting ID from URL '{url_or_code}'. Expected format: https://meet.google.com/abc-defg-hij"
        )

    matched_code = match.group(1).lower()
    if "-" in matched_code:
        return matched_code
    elif len(matched_code) == 10:
        return f"{matched_code[:3]}-{matched_code[3:7]}-{matched_code[7:]}"
    else:
        return matched_code
