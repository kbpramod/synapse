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


llm_service = LLMService()