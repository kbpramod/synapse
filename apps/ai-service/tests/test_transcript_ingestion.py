import io
import json
import unittest
import zipfile
from unittest.mock import patch, AsyncMock
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

# Isolated SQLite in-memory engine for unit testing
SQLALCHEMY_DATABASE_URL = "sqlite:///:memory:"
test_engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)


def override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


from src.db.database import Base
from src.main import app
from db.session import get_db as db_get_db
from src.db.session import get_db as src_get_db
app.dependency_overrides[db_get_db] = override_get_db
app.dependency_overrides[src_get_db] = override_get_db

from models.meeting import Meeting
from models.decision import Decision
from models.task import Task
from models.knowledge import Knowledge
from src.services.transcript_ingestion_service import transcript_ingestion_service
from src.services.transcript_analyzer import (
    transcript_analyzer,
    TranscriptAnalysisOutput,
    TranscriptAnalysisError
)

# Clear remote startup DB hook during isolated unit testing
app.router.on_startup.clear()


def create_sample_docx(paragraphs: list[str]) -> bytes:
    """Creates a real in-memory DOCX binary for testing."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        xml_parts = [
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">',
            '<w:body>'
        ]
        for p in paragraphs:
            xml_parts.append(f'<w:p><w:r><w:t>{p}</w:t></w:r></w:p>')
        xml_parts.append('</w:body></w:document>')
        z.writestr('word/document.xml', ''.join(xml_parts))
    return buf.getvalue()


MOCK_ANALYSIS_DATA = {
    "decisions": [
        {
            "decision": "Migrate payment service to PostgreSQL",
            "rationale": "Better ACID guarantees and native JSON support",
            "participants": ["Alice", "Bob"],
            "source_reference": {
                "text": "Alice: We agreed to migrate payment service to Postgres.",
                "speaker": "Alice",
                "timestamp": "04:15"
            }
        }
    ],
    "tasks": [
        {
            "title": "Setup PostgreSQL schema migration",
            "description": "Create Alembic migrations for new schema",
            "owner": "Bob",
            "deadline": "Friday",
            "source_reference": {
                "text": "Bob: I will handle the schema migrations by Friday.",
                "speaker": "Bob",
                "timestamp": "05:20"
            }
        },
        {
            "title": "Investigate latency spikes",
            "description": "Check connection pool bottlenecks",
            "owner": None,
            "deadline": None,
            "source_reference": {
                "text": "Someone should investigate the latency spikes.",
                "speaker": "Alice",
                "timestamp": None
            }
        }
    ],
    "knowledge": [
        {
            "topic": "Payment Gateway Timeout",
            "content": "Stripe webhooks timeout after 5 seconds if database locking occurs.",
            "source_reference": {
                "text": "Bob: Stripe drops connections after 5 seconds of latency.",
                "speaker": "Bob",
                "timestamp": "08:12"
            }
        }
    ]
}


class TestTranscriptIngestion(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)

    def setUp(self):
        Base.metadata.create_all(bind=test_engine)
        self.mock_storage = AsyncMock()
        self.mock_storage.upload.return_value = "transcripts/test/file.txt"
        self.mock_storage.get.return_value = b"sample transcript content"
        self.mock_storage.delete.return_value = True
        self.mock_storage.exists.return_value = True
        transcript_ingestion_service.storage = self.mock_storage

    def tearDown(self):
        Base.metadata.drop_all(bind=test_engine)

    # -------------------------------------------------------------
    # 1. Successful pasted transcript ingestion
    # -------------------------------------------------------------
    @patch.object(transcript_analyzer, "analyze")
    def test_pasted_transcript_success(self, mock_analyze):
        mock_analyze.return_value = TranscriptAnalysisOutput.model_validate(MOCK_ANALYSIS_DATA)

        payload = {
            "title": "Payment Service Architecture Sync",
            "transcript": (
                "Alice: We agreed to migrate payment service to Postgres.\n"
                "Bob: I will handle the schema migrations by Friday.\n"
                "Bob: Stripe drops connections after 5 seconds of latency."
            )
        }

        response = self.client.post("/meetings/transcript", json=payload)
        self.assertEqual(response.status_code, 201)
        data = response.json()

        self.assertIn("meeting_id", data)
        self.assertEqual(data["status"], "completed")
        self.assertEqual(data["source"], "pasted")
        self.assertEqual(data["decisions_count"], 1)
        self.assertEqual(data["tasks_count"], 2)
        self.assertEqual(data["knowledge_count"], 1)

        # Verify DB records
        db = TestingSessionLocal()
        meeting = db.query(Meeting).filter(Meeting.id == data["meeting_id"]).first()
        self.assertIsNotNone(meeting)
        self.assertEqual(meeting.title, "Payment Service Architecture Sync")
        self.assertEqual(meeting.analysis_status, "completed")
        self.assertEqual(meeting.source_type, "pasted")

        decisions = db.query(Decision).filter(Decision.meeting_id == meeting.id).all()
        self.assertEqual(len(decisions), 1)
        self.assertEqual(decisions[0].decision, "Migrate payment service to PostgreSQL")
        self.assertEqual(decisions[0].source_reference["speaker"], "Alice")
        self.assertEqual(decisions[0].source_reference["timestamp"], "04:15")

        tasks = db.query(Task).filter(Task.meeting_id == meeting.id).all()
        self.assertEqual(len(tasks), 2)
        self.assertEqual(tasks[0].owner, "Bob")
        self.assertEqual(tasks[0].deadline, "Friday")
        self.assertIsNone(tasks[1].owner)
        self.assertIsNone(tasks[1].deadline)

        knowledge = db.query(Knowledge).filter(Knowledge.meeting_id == meeting.id).all()
        self.assertEqual(len(knowledge), 1)
        self.assertEqual(knowledge[0].topic, "Payment Gateway Timeout")
        db.close()

    # -------------------------------------------------------------
    # 2. Empty transcript rejection
    # -------------------------------------------------------------
    def test_pasted_transcript_empty_rejected(self):
        # Empty string
        res1 = self.client.post("/meetings/transcript", json={"title": "Test", "transcript": ""})
        self.assertIn(res1.status_code, (400, 422))

        # Whitespace-only string
        res2 = self.client.post("/meetings/transcript", json={"title": "Test", "transcript": "   \n\t  "})
        self.assertEqual(res2.status_code, 400)
        self.assertIn("empty", res2.text.lower())

    # -------------------------------------------------------------
    # 3. Successful file uploads (.txt, .md, .docx)
    # -------------------------------------------------------------
    @patch.object(transcript_analyzer, "analyze")
    def test_file_upload_txt_success(self, mock_analyze):
        mock_analyze.return_value = TranscriptAnalysisOutput.model_validate(MOCK_ANALYSIS_DATA)

        file_content = b"Alice: We need to scale the cache.\nBob: I will benchmark Redis."
        response = self.client.post(
            "/meetings/transcript/upload",
            files={"file": ("notes.txt", file_content, "text/plain")},
            data={"title": "Cache Sync"}
        )
        self.assertEqual(response.status_code, 201)
        data = response.json()
        self.assertEqual(data["status"], "completed")
        self.assertEqual(data["source"], "file")
        self.assertEqual(data["decisions_count"], 1)

    @patch.object(transcript_analyzer, "analyze")
    def test_file_upload_md_success(self, mock_analyze):
        mock_analyze.return_value = TranscriptAnalysisOutput.model_validate(MOCK_ANALYSIS_DATA)

        file_content = b"# Architecture Meeting\nAlice: Approved Redis.\nBob: Done."
        response = self.client.post(
            "/meetings/transcript/upload",
            files={"file": ("notes.md", file_content, "text/markdown")},
            data={"title": "Markdown Meeting"}
        )
        self.assertEqual(response.status_code, 201)
        data = response.json()
        self.assertEqual(data["status"], "completed")
        self.assertEqual(data["source"], "file")

    @patch.object(transcript_analyzer, "analyze")
    def test_file_upload_docx_success(self, mock_analyze):
        mock_analyze.return_value = TranscriptAnalysisOutput.model_validate(MOCK_ANALYSIS_DATA)

        docx_bytes = create_sample_docx([
            "Meeting Title: Payment Discussion",
            "Alice: We agreed to migrate payment service to Postgres.",
            "Bob: I will handle the schema migrations by Friday."
        ])

        response = self.client.post(
            "/meetings/transcript/upload",
            files={"file": ("discussion.docx", docx_bytes, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
            data={"title": "Word Document Meeting"}
        )
        self.assertEqual(response.status_code, 201)
        data = response.json()
        self.assertEqual(data["status"], "completed")
        self.assertEqual(data["source"], "file")
        self.assertEqual(data["decisions_count"], 1)

    # -------------------------------------------------------------
    # 4. Invalid file rejection (format, size, corruption)
    # -------------------------------------------------------------
    def test_unsupported_file_extension_rejected(self):
        response = self.client.post(
            "/meetings/transcript/upload",
            files={"file": ("transcript.pdf", b"%PDF-1.4 dummy", "application/pdf")}
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("unsupported", response.text.lower())

    def test_corrupt_docx_rejected(self):
        response = self.client.post(
            "/meetings/transcript/upload",
            files={"file": ("bad.docx", b"NOT_A_ZIP_ARCHIVE", "application/vnd.openxmlformats-officedocument.wordprocessingml.document")}
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("docx", response.text.lower())

    def test_empty_file_rejected(self):
        response = self.client.post(
            "/meetings/transcript/upload",
            files={"file": ("empty.txt", b"", "text/plain")}
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("empty", response.text.lower())

    # -------------------------------------------------------------
    # 5. LLM structured output parsing & Pydantic validation
    # -------------------------------------------------------------
    def test_llm_structured_output_parsing(self):
        output = TranscriptAnalysisOutput.model_validate(MOCK_ANALYSIS_DATA)
        self.assertEqual(len(output.decisions), 1)
        self.assertEqual(output.decisions[0].decision, "Migrate payment service to PostgreSQL")
        self.assertEqual(output.decisions[0].source_reference.speaker, "Alice")
        self.assertEqual(output.tasks[0].owner, "Bob")
        self.assertEqual(output.tasks[0].deadline, "Friday")
        self.assertIsNone(output.tasks[1].owner)
        self.assertIsNone(output.tasks[1].deadline)

    # -------------------------------------------------------------
    # 6. LLM failure handling: Transcript NOT lost, status = "failed"
    # -------------------------------------------------------------
    @patch.object(transcript_analyzer, "analyze")
    def test_llm_failure_preserves_transcript(self, mock_analyze):
        mock_analyze.side_effect = TranscriptAnalysisError("OpenAI API rate limit exceeded")

        payload = {
            "title": "Failed Meeting Sync",
            "transcript": "Alice: Some important discussion that should not be lost."
        }

        response = self.client.post("/meetings/transcript", json=payload)
        self.assertEqual(response.status_code, 201)
        data = response.json()

        self.assertEqual(data["status"], "failed")
        self.assertIn("OpenAI API rate limit exceeded", data["error"])

        # Verify DB still contains Meeting record with status='failed' and raw transcript intact!
        db = TestingSessionLocal()
        meeting = db.query(Meeting).filter(Meeting.id == data["meeting_id"]).first()
        self.assertIsNotNone(meeting)
        self.assertEqual(meeting.analysis_status, "failed")
        self.assertIn("OpenAI API rate limit exceeded", meeting.error_message)
        self.assertEqual(meeting.transcript_text, "Alice: Some important discussion that should not be lost.")
        self.assertIsNotNone(meeting.storage_key)
        db.close()

    # -------------------------------------------------------------
    # 7. Database persistence & retrieval
    # -------------------------------------------------------------
    @patch.object(transcript_analyzer, "analyze")
    def test_get_meeting_analysis_endpoint(self, mock_analyze):
        mock_analyze.return_value = TranscriptAnalysisOutput.model_validate(MOCK_ANALYSIS_DATA)

        create_res = self.client.post("/meetings/transcript", json={
            "title": "Analysis Retrieval Test",
            "transcript": "Alice: We agreed to migrate. Bob: I will do it."
        })
        meeting_id = create_res.json()["meeting_id"]

        get_res = self.client.get(f"/meetings/{meeting_id}/analysis")
        self.assertEqual(get_res.status_code, 200)
        data = get_res.json()

        self.assertEqual(data["meeting_id"], meeting_id)
        self.assertEqual(data["title"], "Analysis Retrieval Test")
        self.assertEqual(data["decisions_count"], 1)
        self.assertEqual(data["tasks_count"], 2)
        self.assertEqual(data["knowledge_count"], 1)
        self.assertEqual(data["decisions"][0]["decision"], "Migrate payment service to PostgreSQL")
        self.assertEqual(data["tasks"][0]["title"], "Setup PostgreSQL schema migration")

    # -------------------------------------------------------------
    # 8. Retry / Re-analysis without duplicate extracted records (Idempotency)
    # -------------------------------------------------------------
    @patch.object(transcript_analyzer, "analyze")
    def test_reanalysis_idempotency_no_duplicates(self, mock_analyze):
        mock_analyze.return_value = TranscriptAnalysisOutput.model_validate(MOCK_ANALYSIS_DATA)

        # 1. Initial ingestion
        initial_res = self.client.post("/meetings/transcript", json={
            "title": "Retry Sync",
            "transcript": "Alice: Agreed on PostgreSQL."
        })
        meeting_id = initial_res.json()["meeting_id"]

        db = TestingSessionLocal()
        self.assertEqual(db.query(Decision).filter(Decision.meeting_id == meeting_id).count(), 1)
        self.assertEqual(db.query(Task).filter(Task.meeting_id == meeting_id).count(), 2)
        self.assertEqual(db.query(Knowledge).filter(Knowledge.meeting_id == meeting_id).count(), 1)
        db.close()

        # 2. Trigger re-analysis with updated mock data (e.g. 2 decisions instead of 1)
        updated_mock_data = {
            "decisions": [
                {
                    "decision": "Decision A",
                    "rationale": "Rationale A",
                    "participants": ["Alice"],
                    "source_reference": {"text": "Quote A", "speaker": "Alice", "timestamp": None}
                },
                {
                    "decision": "Decision B",
                    "rationale": "Rationale B",
                    "participants": ["Bob"],
                    "source_reference": {"text": "Quote B", "speaker": "Bob", "timestamp": None}
                }
            ],
            "tasks": [
                {
                    "title": "Task 1",
                    "description": None,
                    "owner": "Alice",
                    "deadline": None,
                    "source_reference": {"text": "Task quote", "speaker": "Alice", "timestamp": None}
                }
            ],
            "knowledge": []
        }
        mock_analyze.return_value = TranscriptAnalysisOutput.model_validate(updated_mock_data)

        retry_res = self.client.post(f"/meetings/{meeting_id}/reanalyze")
        self.assertEqual(retry_res.status_code, 200)
        retry_data = retry_res.json()

        self.assertEqual(retry_data["status"], "completed")
        self.assertEqual(retry_data["decisions_count"], 2)
        self.assertEqual(retry_data["tasks_count"], 1)
        self.assertEqual(retry_data["knowledge_count"], 0)

        # 3. Verify in DB: exactly 2 decisions, 1 task, 0 knowledge (NO DUPLICATES)
        db = TestingSessionLocal()
        self.assertEqual(db.query(Decision).filter(Decision.meeting_id == meeting_id).count(), 2)
        self.assertEqual(db.query(Task).filter(Task.meeting_id == meeting_id).count(), 1)
        self.assertEqual(db.query(Knowledge).filter(Knowledge.meeting_id == meeting_id).count(), 0)
        db.close()


if __name__ == "__main__":
    unittest.main()
