import json
from typing import Optional, Dict, Any, List
from sqlalchemy.orm import Session

from src.services.hybrid_search_service import hybrid_search_service
from src.ai.llm_service import llm_service


class QueryService:

    async def answer_query(
        self,
        db: Session,
        query: str,
        time_window_days: Optional[int] = 30,
        actor: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Answers natural language queries about engineering work state using Hybrid Search evidence
        and LLM reasoning.
        """
        # 1. Retrieve hybrid search context
        search_result = await hybrid_search_service.search(
            db=db,
            query_text=query,
            time_window_days=time_window_days,
            actor=actor,
            top_k=15
        )

        events = search_result.get("events", [])
        work_items = search_result.get("work_items", [])

        # 2. Format timeline evidence for LLM prompt
        formatted_events = []
        for e in events:
            formatted_events.append({
                "event_id": e.id,
                "timestamp": e.timestamp.isoformat() if e.timestamp else None,
                "source": e.source,
                "event_type": e.event_type,
                "actor": e.actor,
                "entity_id": e.entity_id,
                "content": e.content,
                "metadata": e.metadata_json or {}
            })

        formatted_work_items = []
        for w in work_items:
            formatted_work_items.append({
                "work_id": w.id,
                "title": w.title,
                "status": w.status,
                "area": w.area
            })

        # 3. Construct LLM prompt
        prompt = f"""You are Tzylo, an Engineering Memory Assistant that connects meeting intent with GitHub execution.
You must answer the user's question accurately using ONLY the structured evidence provided below. Do not invent facts or assume completed work without GitHub PR/merge evidence.

USER QUESTION:
"{query}"

STRUCTURED EVIDENCE (EVENTS TIMELINE):
{json.dumps(formatted_events, indent=2)}

TRACKED WORK ITEMS:
{json.dumps(formatted_work_items, indent=2)}

INSTRUCTIONS:
1. Reconstruct the full lifecycle of the engineering work mentioned (What was discussed/committed in meetings vs. what GitHub activity occurred afterward).
2. Clearly highlight:
   - Meeting Intent & Commitments (Who committed to what)
   - GitHub Execution (PR created, reviews, approvals, merges)
   - Current State (e.g., Completed, Awaiting Review, In Progress, Outstanding)
3. If evidence shows gaps or pending work, state them clearly.

Respond with a clear, concise, structured natural language explanation.
"""

        # 4. Invoke LLM for reasoning
        try:
            answer_text = await llm_service.complete(prompt)
        except Exception as err:
            print(f"[QUERY SERVICE LLM ERROR] {err}")
            answer_text = "Unable to process query reasoner at this time."

        return {
            "query": query,
            "answer": answer_text,
            "timeline": formatted_events,
            "work_items": formatted_work_items,
            "evidence_count": len(events)
        }


query_service = QueryService()
