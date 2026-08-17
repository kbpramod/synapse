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
        """Analyze a PR title, body, list of files, and patch diff to generate a semantic ChangeRecord."""
        prompt = f"""You are a PR Change Analyzer for an Engineering Memory System.
Developers sometimes write poor PR titles or empty descriptions (e.g. "fix", "changes", "commit 123").
Your task is to analyze the PR metadata, changed files, and diff snippet to construct a high-quality semantic ChangeRecord.

PR Title: {pr_title or 'Untitled'}
PR Body: {pr_body or 'No description provided'}
Changed Files: {json.dumps(changed_files)}

Diff Snippet (Truncated):
{diff_snippet[:3000]}

Generate a structured ChangeRecord object with:
- summary: A clear 1-2 sentence high-level summary of what this PR actually implemented or changed.
- changes: A list of specific functional/architectural changes made in this PR.
- areas: A list of engineering/domain areas affected (e.g. "Discussions API", "Caching", "Authentication", "Database Migration").

Respond ONLY with a JSON object in this schema:
{{
  "summary": "string",
  "changes": ["string"],
  "areas": ["string"]
}}
"""
        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {
                        "role": "system",
                        "content": "You analyze PR diffs and metadata into clean JSON change records."
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
            print(f"[LLM CHANGE ANALYZER ERROR] {e}")
            return {
                "summary": pr_title or "PR code changes",
                "changes": [pr_title or "Updated files"],
                "areas": ["General"]
            }


llm_service = LLMService()
