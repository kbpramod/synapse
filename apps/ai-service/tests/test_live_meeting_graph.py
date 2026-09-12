import unittest
from unittest.mock import patch, AsyncMock, MagicMock
from langchain_core.messages import AIMessage

from ai.graphs.live_meeting_graph import (
    assess_rag_necessity_node,
    consolidate_state_node,
    live_meeting_graph,
    LiveMeetingGraphState
)


class TestLiveMeetingGraph(unittest.IsolatedAsyncioTestCase):

    def test_assess_rag_necessity_pleasantries(self):
        """Short greetings or trivial banter should NOT trigger RAG."""
        state: LiveMeetingGraphState = {
            "current_window_transcript": "Hey guys, can everyone hear me? Yeah, loud and clear.",
            "force_rag": False,
            "window_index": 1
        }
        res = assess_rag_necessity_node(state)
        self.assertFalse(res["rag_needed"])
        self.assertIsNone(res["rag_query"])

    def test_assess_rag_necessity_technical_keywords(self):
        """Technical discussion referring to architecture, databases, or schemas should trigger RAG."""
        state: LiveMeetingGraphState = {
            "current_window_transcript": (
                "Alice: Should we migrate the database schema for the postgres tables? "
                "Bob: Yes, let's check the previous architecture decision regarding database indexing."
            ),
            "force_rag": False,
            "window_index": 2
        }
        res = assess_rag_necessity_node(state)
        self.assertTrue(res["rag_needed"])
        self.assertIsNotNone(res["rag_query"])

    def test_assess_rag_necessity_force_rag(self):
        """When force_rag is True, RAG is always invoked."""
        state: LiveMeetingGraphState = {
            "current_window_transcript": "General sync without keywords.",
            "force_rag": True,
            "window_index": 3
        }
        res = assess_rag_necessity_node(state)
        self.assertTrue(res["rag_needed"])

    def test_consolidate_state_node(self):
        """Consolidates members, decisions, tasks, and updates rolling summary without duplicates."""
        state: LiveMeetingGraphState = {
            "members": ["Alice", "Bob"],
            "extracted_members": ["Charlie", "Alice", "Unknown"],
            "recent_decisions": [
                {"decision": "Use FastAPI for REST services", "topic": "Architecture"}
            ],
            "new_decisions": [
                {"decision": "Use PostgreSQL for persistence", "topic": "Database", "rationale": "ACID compliance"},
                {"decision": "Use FastAPI for REST services", "topic": "Architecture"}  # Duplicate
            ],
            "tasks": [
                {"task": "Set up Alembic migrations", "assignee": "Alice", "work_status": "COMPLETED", "area": "Database"}
            ],
            "new_tasks": [
                {"task": "Implement LangGraph live workflow", "assignee": "Bob", "work_status": "PLANNED", "area": "AI"},
                {"task": "Set up Alembic migrations", "assignee": "Alice"}  # Duplicate
            ],
            "previous_minutes_summary": "Initial architecture discussion.",
            "window_summary": "Agreed on PostgreSQL and live workflow.",
            "accumulated_summary": "Initial architecture discussion followed by agreement on PostgreSQL."
        }

        consolidated = consolidate_state_node(state)

        # Verify members merged, deduplicated, sorted, "Unknown" filtered
        self.assertEqual(consolidated["members"], ["Alice", "Bob", "Charlie"])

        # Verify decisions deduplicated
        decisions = consolidated["recent_decisions"]
        self.assertEqual(len(decisions), 2)
        self.assertEqual(decisions[0]["decision"], "Use FastAPI for REST services")
        self.assertEqual(decisions[1]["decision"], "Use PostgreSQL for persistence")

        # Verify tasks deduplicated
        tasks = consolidated["tasks"]
        self.assertEqual(len(tasks), 2)
        self.assertEqual(tasks[0]["task"], "Set up Alembic migrations")
        self.assertEqual(tasks[1]["task"], "Implement LangGraph live workflow")

    @patch("src.ai.graphs.live_meeting_graph.ChatOpenAI.ainvoke")
    async def test_full_graph_ainvoke(self, mock_ainvoke):
        """Tests end-to-end execution of live_meeting_graph with mocked LLM response."""
        mock_response_json = """{
            "detected_speakers": ["Alice", "Dave"],
            "window_summary": "Alice and Dave discussed adopting pgvector for live meeting search.",
            "new_decisions": [
                {
                    "decision": "Adopt pgvector 1536-dim embeddings for knowledge search",
                    "topic": "Search Architecture",
                    "rationale": "Native PostgreSQL integration without extra vector DB cluster"
                }
            ],
            "new_tasks": [
                {
                    "task": "Create pgvector schema migration",
                    "assignee": "Dave",
                    "work_status": "PLANNED",
                    "area": "Database"
                }
            ],
            "updated_accumulated_summary": "Meeting kicked off with database architecture sync. Alice and Dave agreed to use pgvector."
        }"""
        
        mock_ainvoke.return_value = AIMessage(content=mock_response_json)

        initial_state: LiveMeetingGraphState = {
            "meeting_id": "test-meet-123",
            "window_index": 1,
            "window_start_sec": 0.0,
            "window_end_sec": 240.0,
            "current_window_transcript": "[01:30] Alice: Should we adopt pgvector? [02:00] Dave: Yes, pgvector is great.",
            "previous_minutes_summary": "Meeting started.",
            "force_rag": False,
            "members": ["Alice"],
            "recent_decisions": [],
            "tasks": []
        }

        result = await live_meeting_graph.ainvoke(initial_state)

        # Check that LLM was called
        mock_ainvoke.assert_called_once()
        
        # Verify final state outputs
        self.assertIn("Dave", result["members"])
        self.assertIn("Alice", result["members"])
        self.assertEqual(len(result["recent_decisions"]), 1)
        self.assertEqual(result["recent_decisions"][0]["decision"], "Adopt pgvector 1536-dim embeddings for knowledge search")
        self.assertEqual(len(result["tasks"]), 1)
        self.assertEqual(result["tasks"][0]["assignee"], "Dave")
        self.assertIn("pgvector", result["accumulated_summary"])


if __name__ == "__main__":
    unittest.main()
