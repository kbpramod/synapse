import json
import os
from src.ai.client import ai_client

class LLMService:

    def __init__(self):
        self.client = ai_client
        self.model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

    async def complete(
        self,
        prompt: str
    ) -> str:

        response = await self.client.chat.completions.create(
            model=self.model,
            messages=[
                {
                    "role": "user",
                    "content": prompt
                }
            ],
            temperature=0
        )

        return response.choices[0].message.content

    async def evaluate_fact_action(
        self,
        incoming_fact: dict,
        existing_facts: list[dict]
    ) -> dict:
        prompt = f"""You are a Knowledge Base Maintenance Agent.
Analyze the INCOMING FACT against EXISTING FACTS in the knowledge base.

INCOMING FACT:
Section: {incoming_fact.get('section')}
Topic: {incoming_fact.get('topic')}
Text: {incoming_fact.get('text')}

EXISTING CANDIDATE FACTS:
{json.dumps(existing_facts, indent=2)}

Determine the required action:
- "ADD": The incoming fact represents new knowledge not covered in existing facts.
- "UPDATE": The incoming fact updates or replaces a specific existing fact. Provide target_id of that fact.
- "DELETE": The incoming fact invalidates or contradicts a specific existing fact. Provide target_id of that fact.
- "NOOP": The incoming fact is already accurately present or redundant.

Respond ONLY with a valid JSON object matching this schema:
{{
  "action": "ADD" | "UPDATE" | "DELETE" | "NOOP",
  "target_id": "string or null",
  "reasoning": "brief explanation"
}}
"""
        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {
                        "role": "system",
                        "content": "You are a precise knowledge reconciliation assistant that outputs only JSON."
                    },
                    {
                        "role": "user",
                        "content": prompt
                    }
                ],
                response_format={"type": "json_object"},
                temperature=0
            )
            result = json.loads(response.choices[0].message.content)
            return result
        except Exception as e:
            # Fallback if evaluation fails or no existing facts
            return {
                "action": "ADD" if not existing_facts else "NOOP",
                "target_id": None,
                "reasoning": f"Fallback due to evaluation error: {str(e)}"
            }

    async def extract_meeting_events(
        self,
        title: str,
        segments: list[dict]
    ) -> list[dict]:
        """Extract commitments, decisions, and action items from meeting transcript segments."""
        formatted_transcript = "\n".join(
            [f"[{s.get('timestamp', '00:00')}] {s.get('speaker', 'Unknown')}: {s.get('text', '')}" for s in segments]
        )

        prompt = f"""You are an Engineering Memory Extraction System.
Analyze the following meeting transcript and extract distinct engineering commitments, technical decisions, and action items.

Meeting Title: {title}

Transcript:
{formatted_transcript}

For each extracted item, specify:
- actor: Name of the person who committed or proposed it (e.g. "Pramod", "Alex")
- event_type: "meeting_commitment" or "meeting_decision"
- entity_type: "commitment" or "decision"
- entity_id: A short canonical topic identifier (e.g. "caching-discussion", "auth-refactor")
- content: Clear statement of the commitment or decision (e.g. "Pramod will implement caching for Discussions.")
- topic: Short topic name for knowledge indexing (e.g. "Caching")
- section: Section category (e.g. "Architecture", "Infrastructure", "Features")

Respond ONLY with a JSON object in this exact schema:
{{
  "events": [
    {{
      "actor": "string",
      "event_type": "meeting_commitment" | "meeting_decision",
      "entity_type": "string",
      "entity_id": "string",
      "content": "string",
      "topic": "string",
      "section": "string"
    }}
  ]
}}
"""
        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {
                        "role": "system",
                        "content": "You extract engineering commitments and decisions from transcripts into JSON."
                    },
                    {
                        "role": "user",
                        "content": prompt
                    }
                ],
                response_format={"type": "json_object"},
                temperature=0
            )
            data = json.loads(response.choices[0].message.content)
            return data.get("events", [])
        except Exception as e:
            print(f"[LLM EXTRACTION ERROR] {e}")
            return []

    async def evaluate_event_correlation(
        self,
        event_a: dict,
        event_b: dict
    ) -> dict:
        """Evaluate if two events (e.g., a meeting commitment and a GitHub PR) correlate."""
        prompt = f"""You are a Cross-Source Engineering Memory Correlation Agent.
Determine if Event B (e.g., GitHub PR / action) fulfills, relates to, or correlates with Event A (e.g., Meeting Commitment / Decision).

EVENT A:
Type: {event_a.get('event_type')}
Actor: {event_a.get('actor')}
Content: {event_a.get('content')}

EVENT B:
Type: {event_b.get('event_type')}
Actor: {event_b.get('actor')}
Content: {event_b.get('content')}

Evaluate:
1. Are the actors matching or related?
2. Does Event B address, fulfill, or update the work described in Event A?

Respond ONLY with a JSON object in this schema:
{{
  "is_correlated": true | false,
  "relationship_type": "fulfills" | "correlates_to" | "supersedes" | "none",
  "confidence": 0.0 to 1.0,
  "reasoning": "brief explanation"
}}
"""
        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {
                        "role": "system",
                        "content": "You correlate engineering commitments with GitHub activity and output JSON."
                    },
                    {
                        "role": "user",
                        "content": prompt
                    }
                ],
                response_format={"type": "json_object"},
                temperature=0
            )
            return json.loads(response.choices[0].message.content)
        except Exception as e:
            return {
                "is_correlated": False,
                "relationship_type": "none",
                "confidence": 0.0,
                "reasoning": f"Error during evaluation: {str(e)}"
            }

    async def analyze_pr_change(
        self,
        pr_title: str,
        pr_body: str,
        changed_files: list[str],
        diff_snippet: str
    ) -> dict:
        """Analyze a PR title, body, list of files, and patch diff to generate a structured ChangeRecord."""
        prompt = f"""You are a Senior Engineering Change Analyzer.
Developers sometimes write poor PR titles or empty descriptions (e.g. "fix", "changes", "wip").
Your task is to analyze the PR metadata, changed files, and filtered diff snippet to construct a precise, high-value structured ChangeRecord.

PR Title: {pr_title or 'Untitled'}
PR Body: {pr_body or 'No description provided'}
Changed Files: {json.dumps(changed_files)}

Filtered Diff Snippet:
{diff_snippet}

Respond ONLY with a valid JSON object matching this EXACT schema:
{{
  "summary": "A concise 1-2 sentence high-level summary of what was implemented or changed.",
  "changes": [
    {{
      "area": "Engineering area or component (e.g., Caching, API, Database, Testing, Auth)",
      "description": "Specific functional or architectural change made."
    }}
  ],
  "impact": [
    "Bullet points of architectural, performance, security, or behavioral impacts."
  ],
  "files_changed": {json.dumps(changed_files)}
}}
"""
        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {
                        "role": "system",
                        "content": "You are a software architect that analyzes code changes and outputs strictly structured JSON."
                    },
                    {
                        "role": "user",
                        "content": prompt
                    }
                ],
                response_format={"type": "json_object"},
                temperature=0
            )
            parsed = json.loads(response.choices[0].message.content)
            if not parsed.get("files_changed"):
                parsed["files_changed"] = changed_files
            return parsed
        except Exception as e:
            print(f"[LLM CHANGE ANALYZER ERROR] {e}")
            return {
                "summary": pr_title or "Code changes updated",
                "changes": [
                    {
                        "area": "General",
                        "description": pr_title or "Updated application files"
                    }
                ],
                "impact": [
                    "Updated repository source files."
                ],
                "files_changed": changed_files
            }

    async def analyze_meeting_comprehensive(
        self,
        title: str,
        transcript_text: str,
        participants: list[str] | None = None
    ) -> dict:
        """
        Analyzes a full meeting transcript to extract:
        1. Executive & technical summary
        2. Key architectural & product decisions
        3. Action items & commitments with assignees
        4. Discrete searchable knowledge facts for pgvector RAG
        """
        participants_str = ", ".join(participants) if participants else "Meeting attendees"

        prompt = f"""You are an Engineering Memory Extraction System.
Analyze the following meeting transcript to extract high-fidelity knowledge, decisions, and action items.

Meeting Title: {title}
Participants: {participants_str}

Transcript:
{transcript_text}

Extract the following in strictly structured JSON:
1. "summary": A clear 2-3 paragraph executive and engineering summary of the meeting discussions.
2. "decisions": Array of architectural, product, or design decisions made during the meeting.
   Each decision object must contain:
   - "topic": Short topic title (e.g. "Caching Strategy", "Authentication")
   - "decision": Clear statement of what was decided
   - "rationale": Why this decision was chosen (or null)
   - "section": Category ("Architecture", "Infrastructure", "Product", "Operations")
3. "action_items": Array of actionable commitments assigned to individuals.
   Each action item object must contain:
   - "assignee": Name of the person responsible (or "Unassigned")
   - "task": Specific description of the action to be taken
   - "area": Domain area (e.g. "Backend", "Frontend", "DevOps")
   - "work_status": "IN_PROGRESS" or "PROPOSED"
4. "discrete_facts": Array of atomic, self-contained factual statements suitable for vector search.
   Each fact object must contain:
   - "content": Concise standalone statement (e.g. "[Decision] Decided to use Redis for discussion query caching.")
   - "area": Domain area
   - "person": Assignee or speaker name (or null)
   - "fact_status": "ACTIVE"
   - "work_status": "COMPLETED" (for decisions) or "IN_PROGRESS" / "PROPOSED" (for commitments)

Respond ONLY with a JSON object in this exact schema:
{{
  "summary": "string",
  "decisions": [
    {{
      "topic": "string",
      "decision": "string",
      "rationale": "string or null",
      "section": "string"
    }}
  ],
  "action_items": [
    {{
      "assignee": "string",
      "task": "string",
      "area": "string",
      "work_status": "string"
    }}
  ],
  "discrete_facts": [
    {{
      "content": "string",
      "area": "string",
      "person": "string or null",
      "fact_status": "string",
      "work_status": "string"
    }}
  ]
}}
"""
        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {
                        "role": "system",
                        "content": "You are a software engineering memory extractor that produces strictly formatted JSON."
                    },
                    {
                        "role": "user",
                        "content": prompt
                    }
                ],
                response_format={"type": "json_object"},
                temperature=0
            )
            parsed = json.loads(response.choices[0].message.content)
            return parsed
        except Exception as e:
            print(f"[LLM MEETING ANALYZER ERROR] {e}")
            return {
                "summary": f"Discussion notes from meeting: {title}",
                "decisions": [],
                "action_items": [],
                "discrete_facts": [
                    {
                        "content": f"Meeting '{title}' held with participants: {participants_str}",
                        "area": "General",
                        "person": None,
                        "fact_status": "ACTIVE",
                        "work_status": "COMPLETED"
                    }
                ]
            }


llm_service = LLMService()
