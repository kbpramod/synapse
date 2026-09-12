from fastapi import FastAPI, Depends, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from sqlalchemy import text

from db.session import get_db
from db.database import engine, Base

# Import all SQLAlchemy models to register with Base metadata
from models.user import User
from models.organization import Organization
from models.organization_member import OrganizationMember
from models.project import Project
from models.repository import Repository, GithubRepository
from models.doc_page import DocPage
from models.github_install_state import GithubInstallState
from models.github_installation import GithubInstallation
from models.knowledge_node import KnowledgeNode
from models.person import Person, IdentityLink
from models.work_item import WorkItem
from models.event_node import EventNode
from models.event_relationship import EventRelationship
from models.github_event import GithubEvent
from models.github_user import GithubUser
from models.pull_request import PullRequest
from models.commit import Commit
from models.fact import Fact
from models.meeting import Meeting
from models.decision import Decision
from models.task import Task
from models.knowledge import Knowledge

# Import API Routers
from api.user import router as user_router
from api.onboarding import router as onboarding_router
from api.projects import router as projects_router
from api.repositories import router as repositories_router
from api.memory import router as memory_router
from api.webhooks import router as webhooks_router
from api.github import router as github_router, github_installation_setup_callback
from api.events import router as events_router
from api.meetings import router as meetings_router
from api.query import router as query_router
from api.identity import router as identity_router
from api.work_items import router as work_items_router

app = FastAPI(
    title="Tzylo — Engineering Memory Assistant API",
    version="1.0.0",
    description="FastAPI Backend for Tzylo Engineering Memory Assistant"
)

# 1. Configure CORS Middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# 2. Error Response Handler
@app.exception_handler(HTTPException)
async def custom_http_exception_handler(request: Request, exc: HTTPException):
    code_map = {
        400: "BAD_REQUEST",
        401: "UNAUTHORIZED",
        403: "FORBIDDEN",
        404: "NOT_FOUND",
        500: "INTERNAL_SERVER_ERROR"
    }
    error_code = code_map.get(exc.status_code, "ERROR")
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": {
                "code": error_code,
                "message": exc.detail if isinstance(exc.detail, str) else str(exc.detail),
                "details": []
            }
        }
    )


# 3. Include Routers
app.include_router(meetings_router)
app.include_router(query_router)
app.include_router(identity_router)
app.include_router(work_items_router)
app.include_router(events_router)
app.include_router(webhooks_router, prefix="/api/webhook")
app.include_router(github_router)
app.include_router(user_router)
app.include_router(onboarding_router)
app.include_router(projects_router)
app.include_router(repositories_router)
app.include_router(memory_router)

# Alias route for GitHub App Setup Callback
app.get("/github/setup", include_in_schema=False)(github_installation_setup_callback)


@app.on_event("startup")
async def startup():
    try:
        with engine.connect() as conn:
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector;"))
            conn.execute(text("ALTER TABLE knowledge_nodes ADD COLUMN IF NOT EXISTS source VARCHAR;"))
            conn.execute(text("ALTER TABLE knowledge_nodes ADD COLUMN IF NOT EXISTS event_id VARCHAR;"))
            conn.execute(text("ALTER TABLE event_nodes ADD COLUMN IF NOT EXISTS person_id VARCHAR;"))
            conn.execute(text("ALTER TABLE event_nodes ADD COLUMN IF NOT EXISTS work_item_id VARCHAR;"))
            conn.execute(text("ALTER TABLE meetings ADD COLUMN IF NOT EXISTS platform VARCHAR DEFAULT 'google_meet';"))
            conn.execute(text("ALTER TABLE meetings ADD COLUMN IF NOT EXISTS meeting_url VARCHAR;"))
            conn.execute(text("ALTER TABLE meetings ADD COLUMN IF NOT EXISTS native_meeting_id VARCHAR;"))
            conn.execute(text("ALTER TABLE meetings ADD COLUMN IF NOT EXISTS vexa_meeting_id VARCHAR;"))
            conn.execute(text("ALTER TABLE meetings ADD COLUMN IF NOT EXISTS source_type VARCHAR DEFAULT 'pasted';"))
            conn.execute(text("ALTER TABLE meetings ADD COLUMN IF NOT EXISTS storage_key VARCHAR;"))
            conn.execute(text("ALTER TABLE meetings ADD COLUMN IF NOT EXISTS transcript_text TEXT;"))
            conn.execute(text("ALTER TABLE meetings ADD COLUMN IF NOT EXISTS analyzed_at TIMESTAMP;"))
            conn.execute(text("ALTER TABLE meetings ADD COLUMN IF NOT EXISTS analysis_status VARCHAR DEFAULT 'pending';"))
            conn.commit()
        Base.metadata.create_all(bind=engine)
        print("[DB INIT] Database tables and extensions verified successfully.")
    except Exception as err:
        print(f"[DB INIT ERROR] Could not initialize database tables: {err}")



@app.get("/")
def root():
    return {
        "service": "Tzylo Engineering Memory Assistant API",
        "status": "running"
    }


@app.get("/health/db")
def health(db: Session = Depends(get_db)):
    try:
        db.execute(text("SELECT 1"))
        return {"status": "healthy"}
    except Exception as e:
        return {"status": "unhealthy", "error": str(e)}