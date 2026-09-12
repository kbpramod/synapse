import json
import logging
import os
from typing import List, Optional
from pydantic import BaseModel, Field, ValidationError

from src.ai.client import ai_client

logger = logging.getLogger(__name__)


class SourceReference(BaseModel):
    text: str = Field(description="Direct quotation or reference segment from the transcript")
    speaker: Optional[str] = Field(default=None, description="Speaker name if identified, or null")
    timestamp: Optional[str] = Field(default=None, description="Timestamp if present in transcript, or null")


class ExtractedDecision(BaseModel):
    decision: str = Field(description="Clear statement of the agreed decision")
    rationale: Optional[str] = Field(default=None, description="Why this decision was made, or null")
    participants: List[str] = Field(default_factory=list, description="List of participants involved in the decision")
    source_reference: SourceReference


class ExtractedTask(BaseModel):
    title: str = Field(description="Actionable task title")
    description: Optional[str] = Field(default=None, description="Task details or context, or null")
    owner: Optional[str] = Field(default=None, description="Person assigned to the task, or null if unassigned")
    deadline: Optional[str] = Field(default=None, description="Explicit deadline if mentioned, or null")
    source_reference: SourceReference


class ExtractedKnowledge(BaseModel):
    topic: str = Field(description="Topic category or concept")
    content: str = Field(description="Factual context, constraint, problem, or system behavior")
    source_reference: SourceReference


class TranscriptAnalysisOutput(BaseModel):
    decisions: List[ExtractedDecision] = Field(default_factory=list)
    tasks: List[ExtractedTask] = Field(default_factory=list)
    knowledge: List[ExtractedKnowledge] = Field(default_factory=list)


class TranscriptAnalysisError(Exception):
    """Raised when LLM analysis fails or returns invalid schema."""
    pass


TRANSCRIPT_ANALYSIS_SYSTEM_PROMPT = """You are an expert organizational memory and meeting analysis intelligence system.
Your job is to analyze meeting transcripts and extract strictly verified:
1. DECISIONS
2. TASKS
3. KNOWLEDGE

You must adhere to these strict extraction rules:

### 1. Decisions
- Extract ONLY decisions that were actually agreed upon by the participants.
- Do NOT convert proposals, suggestions, brainstorming, open questions, or unresolved discussions into decisions.
- Include rationale and participants if mentioned in the transcript.

### 2. Tasks
- Extract explicit or strongly agreed action items.
- Capture what needs to be done (title & description).
- Capture owner ONLY if clearly specified or committed to by a participant. If not specified, set owner to null. NEVER guess or invent owners.
- Capture deadline ONLY if explicitly stated (e.g., "by Friday", "end of sprint"). If not specified, set deadline to null. NEVER invent dates.

### 3. Knowledge
- Extract useful factual, architectural, or contextual information discussed during the meeting.
- Examples: current system behavior, technical constraints, product requirements, customer issues, established facts.
- Avoid duplicating the same information as both knowledge and a decision/task unless there is a clear distinction.

### 4. Source References (MANDATORY FOR EVERY ITEM)
- Every decision, task, and knowledge item MUST include a "source_reference" object:
  - "text": The exact or closely representative quote from the transcript where this came from.
  - "speaker": The name of the speaker who said it, or null if not evident.
  - "timestamp": The timestamp if present in the transcript (e.g. "12:34" or "[00:15:20]"), or null. NEVER invent timestamps.

### 5. Uncertainty & Strict Null Policy
- If any field is not explicitly present or directly supported by the transcript, you MUST set it to null.
- Never hallucinate names, dates, deadlines, or commitments.

Return ONLY a valid JSON object matching this exact schema:
{
  "decisions": [
    {
      "decision": "string",
      "rationale": "string or null",
      "participants": ["string"],
      "source_reference": {
        "text": "string",
        "speaker": "string or null",
        "timestamp": "string or null"
      }
    }
  ],
  "tasks": [
    {
      "title": "string",
      "description": "string or null",
      "owner": "string or null",
      "deadline": "string or null",
      "source_reference": {
        "text": "string",
        "speaker": "string or null",
        "timestamp": "string or null"
      }
    }
  ],
  "knowledge": [
    {
      "topic": "string",
      "content": "string",
      "source_reference": {
        "text": "string",
        "speaker": "string or null",
        "timestamp": "string or null"
      }
    }
  ]
}
"""


class TranscriptAnalyzer:
    """Service responsible for sending transcript text to LLM and parsing structured output."""

    def __init__(self, client=None, model: Optional[str] = None):
        self.client = client or ai_client
        self.model = model or os.getenv("OPENAI_MODEL", "gpt-4o-mini")

    async def analyze(
        self,
        transcript_text: str,
        title: Optional[str] = None
    ) -> TranscriptAnalysisOutput:
        """
        Submits transcript to LLM, enforcing structured JSON output and schema validation.
        Raises TranscriptAnalysisError if the call or parsing fails.
        """
        meeting_title = title or "Meeting Transcript"
        user_prompt = f"Meeting Title: {meeting_title}\n\nTranscript Content:\n{transcript_text}"

        try:
            logger.info(f"[LLM ANALYZER] Submitting transcript ({len(transcript_text)} chars) to {self.model}")
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": TRANSCRIPT_ANALYSIS_SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt}
                ],
                response_format={"type": "json_object"},
                temperature=0
            )

            raw_content = response.choices[0].message.content
            if not raw_content:
                raise TranscriptAnalysisError("LLM returned an empty response.")

            parsed_json = json.loads(raw_content)
            validated_output = TranscriptAnalysisOutput.model_validate(parsed_json)
            
            logger.info(
                f"[LLM ANALYZER] Extraction successful: {len(validated_output.decisions)} decisions, "
                f"{len(validated_output.tasks)} tasks, {len(validated_output.knowledge)} knowledge items."
            )
            return validated_output

        except json.JSONDecodeError as err:
            logger.error(f"[LLM ANALYZER ERROR] Invalid JSON returned by LLM: {err}")
            raise TranscriptAnalysisError(f"LLM returned invalid JSON: {str(err)}") from err
        except ValidationError as err:
            logger.error(f"[LLM ANALYZER ERROR] Schema validation failed: {err}")
            raise TranscriptAnalysisError(f"LLM output failed schema validation: {str(err)}") from err
        except Exception as err:
            logger.error(f"[LLM ANALYZER ERROR] LLM completion failed: {err}", exc_info=True)
            raise TranscriptAnalysisError(f"LLM analysis failed: {str(err)}") from err


transcript_analyzer = TranscriptAnalyzer()
