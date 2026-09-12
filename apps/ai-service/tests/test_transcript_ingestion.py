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

from models.user import User
from models.organization import Organization
from models.organization_member import OrganizationMember
from models.meeting import Meeting
from models.decision import Decision
from models.task import Task
from models.knowledge import Knowledge
import api.deps
import src.api.deps
from src.services.transcript_ingestion_service import transcript_ingestion_service
from src.services.transcript_analyzer import (
    transcript_analyzer,
    TranscriptAnalysisOutput,
    TranscriptAnalysisError
)

# Clear remote startup DB hook during isolated unit testing
app.router.on_startup.clear()


def mock_authenticated_user():
    user = User(
        id="test-user-123",
        clerk_user_id="user_test123",
        email="test@example.com",
        name="Test User"
    )
    user.organization_id = "org_test_123"
    return user


for dep_module in (api.deps, src.api.deps):
    if hasattr(dep_module, "require_auth"):
        app.dependency_overrides[dep_module.require_auth] = mock_authenticated_user
    if hasattr(dep_module, "get_current_user"):
        app.dependency_overrides[dep_module.get_current_user] = mock_authenticated_user
    if hasattr(dep_module, "optional_auth"):
        app.dependency_overrides[dep_module.optional_auth] = mock_authenticated_user


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

        response = self.client.post("/api/v1/meetings/transcript", json=payload)
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
        self.assertEqual(meeting.organization_id, "org_test_123")

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
        res1 = self.client.post("/api/v1/meetings/transcript", json={"title": "Test", "transcript": ""})
        self.assertIn(res1.status_code, (400, 422))

        # Whitespace-only string
        res2 = self.client.post("/api/v1/meetings/transcript", json={"title": "Test", "transcript": "   \n\t  "})
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
            "/api/v1/meetings/transcript/upload",
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
            "/api/v1/meetings/transcript/upload",
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
            "/api/v1/meetings/transcript/upload",
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
            "/api/v1/meetings/transcript/upload",
            files={"file": ("transcript.pdf", b"%PDF-1.4 dummy", "application/pdf")}
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("unsupported", response.text.lower())

    def test_corrupt_docx_rejected(self):
        response = self.client.post(
            "/api/v1/meetings/transcript/upload",
            files={"file": ("bad.docx", b"NOT_A_ZIP_ARCHIVE", "application/vnd.openxmlformats-officedocument.wordprocessingml.document")}
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("docx", response.text.lower())

    def test_empty_file_rejected(self):
        response = self.client.post(
            "/api/v1/meetings/transcript/upload",
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

        response = self.client.post("/api/v1/meetings/transcript", json=payload)
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

        create_res = self.client.post("/api/v1/meetings/transcript", json={
            "title": "Analysis Retrieval Test",
            "transcript": "Alice: We agreed to migrate. Bob: I will do it."
        })
        meeting_id = create_res.json()["meeting_id"]

        get_res = self.client.get(f"/api/v1/meetings/{meeting_id}/analysis")
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
        initial_res = self.client.post("/api/v1/meetings/transcript", json={
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

        retry_res = self.client.post(f"/api/v1/meetings/{meeting_id}/reanalyze")
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

    # -------------------------------------------------------------
    # 9. Clerk Organization Enforcement & Auto-provisioning
    # -------------------------------------------------------------
    @patch("api.deps.decode_clerk_token")
    @patch("src.api.deps.decode_clerk_token")
    def test_clerk_auth_organization_required_enforcement(self, mock_decode_src, mock_decode_api):
        mock_payload = {
            "sub": "user_no_org_123",
            "email": "no_org@test.com",
            "name": "No Org User"
            # No org_id!
        }
        mock_decode_src.return_value = mock_payload
        mock_decode_api.return_value = mock_payload

        # Temporarily clear overrides to invoke real get_current_user
        overrides_backup = dict(app.dependency_overrides)
        for dep in (api.deps.require_auth, src.api.deps.require_auth, api.deps.get_current_user, src.api.deps.get_current_user):
            app.dependency_overrides.pop(dep, None)

        try:
            client = TestClient(app)
            response = client.post(
                "/api/v1/meetings/transcript",
                headers={"Authorization": "Bearer fake_token_without_org"},
                json={"title": "Org Test", "transcript": "Some text."}
            )
            self.assertEqual(response.status_code, 403)
            self.assertIn("organization is required", response.text.lower())
        finally:
            app.dependency_overrides.update(overrides_backup)

    @patch("api.deps.decode_clerk_token")
    @patch("src.api.deps.decode_clerk_token")
    @patch.object(transcript_analyzer, "analyze")
    def test_clerk_auth_organization_auto_provisioning(self, mock_analyze, mock_decode_src, mock_decode_api):
        mock_analyze.return_value = TranscriptAnalysisOutput.model_validate(MOCK_ANALYSIS_DATA)
        mock_payload = {
            "sub": "user_with_org_456",
            "email": "org_user@test.com",
            "name": "Org User",
            "org_id": "org_clerk_real_999",
            "org_name": "Acme Engineering",
            "org_role": "org:admin"
        }
        mock_decode_src.return_value = mock_payload
        mock_decode_api.return_value = mock_payload

        overrides_backup = dict(app.dependency_overrides)
        for dep in (api.deps.require_auth, src.api.deps.require_auth, api.deps.get_current_user, src.api.deps.get_current_user):
            app.dependency_overrides.pop(dep, None)

        try:
            client = TestClient(app)
            response = client.post(
                "/api/v1/meetings/transcript",
                headers={"Authorization": "Bearer fake_token_with_org"},
                json={"title": "Org Auto Provision Test", "transcript": "Alice: Hello."}
            )
            self.assertEqual(response.status_code, 201)

            # Verify Organization and OrganizationMember were auto-provisioned in DB
            db = TestingSessionLocal()
            org = db.query(Organization).filter(Organization.id == "org_clerk_real_999").first()
            self.assertIsNotNone(org)
            self.assertEqual(org.name, "Acme Engineering")

            user = db.query(User).filter(User.clerk_user_id == "user_with_org_456").first()
            self.assertIsNotNone(user)

            member = db.query(OrganizationMember).filter(
                OrganizationMember.organization_id == org.id,
                OrganizationMember.user_id == user.id
            ).first()
            self.assertIsNotNone(member)
            self.assertEqual(member.role, "admin")

            # Meeting is also assigned to this organization
            meeting_id = response.json()["meeting_id"]
            meeting = db.query(Meeting).filter(Meeting.id == meeting_id).first()
            self.assertEqual(meeting.organization_id, "org_clerk_real_999")
            db.close()
        finally:
            app.dependency_overrides.update(overrides_backup)


if __name__ == "__main__":
    unittest.main()
