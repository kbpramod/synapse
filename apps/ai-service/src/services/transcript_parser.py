import io
import os
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Set

SUPPORTED_EXTENSIONS: Set[str] = {".txt", ".md", ".docx"}
DEFAULT_MAX_FILE_SIZE_BYTES: int = 10 * 1024 * 1024  # 10 MB


class TranscriptParseError(ValueError):
    """Raised when transcript file parsing or validation fails."""
    pass


def get_max_file_size() -> int:
    try:
        return int(os.getenv("MAX_TRANSCRIPT_FILE_SIZE_BYTES", str(DEFAULT_MAX_FILE_SIZE_BYTES)))
    except ValueError:
        return DEFAULT_MAX_FILE_SIZE_BYTES


def extract_text_from_docx(file_bytes: bytes) -> str:
    """
    Extracts text paragraphs from a .docx file without requiring external dependencies,
    using standard library zipfile and XML ElementTree.
    """
    try:
        with zipfile.ZipFile(io.BytesIO(file_bytes)) as docx:
            if "word/document.xml" not in docx.namelist():
                raise TranscriptParseError("Invalid DOCX format: missing word/document.xml.")
            
            xml_content = docx.read("word/document.xml")
            tree = ET.fromstring(xml_content)
            
            namespaces = {
                "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
            }
            
            paragraphs = []
            for p in tree.iterfind(".//w:p", namespaces):
                texts = [
                    node.text for node in p.iterfind(".//w:t", namespaces)
                    if node.text
                ]
                if texts:
                    paragraphs.append("".join(texts))
            
            return "\n".join(paragraphs)
    except zipfile.BadZipFile as e:
        raise TranscriptParseError(f"Corrupt or invalid DOCX file: {e}")
    except ET.ParseError as e:
        raise TranscriptParseError(f"Failed to parse DOCX document XML: {e}")
    except Exception as e:
        raise TranscriptParseError(f"Error extracting text from DOCX: {e}")


def extract_text_from_plain(file_bytes: bytes) -> str:
    """Decodes plain text or markdown files with resilient encoding fallbacks."""
    for encoding in ("utf-8", "utf-8-sig", "latin-1", "cp1252"):
        try:
            return file_bytes.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
    return file_bytes.decode("utf-8", errors="replace")


def parse_and_validate_transcript_file(filename: str, file_bytes: bytes) -> str:
    """
    Validates file extension, size, and extracts normalized text transcript.
    Raises TranscriptParseError if validation fails.
    """
    if not filename:
        raise TranscriptParseError("Filename cannot be empty.")
    
    ext = Path(filename).suffix.lower()
    if ext not in SUPPORTED_EXTENSIONS:
        supported_str = ", ".join(sorted(SUPPORTED_EXTENSIONS))
        raise TranscriptParseError(
            f"Unsupported file format '{ext}'. Supported formats are: {supported_str}"
        )
    
    max_size = get_max_file_size()
    if len(file_bytes) > max_size:
        max_mb = max_size / (1024 * 1024)
        raise TranscriptParseError(f"File size exceeds maximum allowed limit of {max_mb:.1f}MB.")
    
    if len(file_bytes) == 0:
        raise TranscriptParseError("Uploaded file is completely empty.")

    if ext == ".docx":
        text = extract_text_from_docx(file_bytes)
    else:
        text = extract_text_from_plain(file_bytes)

    cleaned_text = text.strip()
    if not cleaned_text:
        raise TranscriptParseError("Extracted transcript content is empty or contains only whitespace.")

    return cleaned_text


def validate_pasted_transcript(transcript: str) -> str:
    """Validates raw pasted transcript text."""
    if not transcript or not transcript.strip():
        raise TranscriptParseError("Transcript text cannot be empty.")
    return transcript.strip()
