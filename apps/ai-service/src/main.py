from fastapi import FastAPI, Depends, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from sqlalchemy import text

from src.db.session import get_db
from src.db.database import engine, Base

# Import all SQLAlchemy models to register with Base metadata
from src.models.knowledge_node import KnowledgeNode
from src.models.user import User
from src.models.project import Project
from src.models.repository import Repository
from src.models.doc_page import DocPage
from src.models.github_install_state import GithubInstallState
from src.models.github_installation import GithubInstallation

# Import API Routers
from src.api.memory import router as memory_router
from src.api.user import router as user_router
from src.api.onboarding import router as onboarding_router
from src.api.projects import router as projects_router
from src.api.repositories import router as repositories_router
from src.api.webhooks import router as webhooks_router
from src.api.github import router as github_router, github_installation_setup_callback

app = FastAPI(
    title="Tzylo Console Backend API",
    version="1.0.0",
    description="FastAPI Backend for Tzylo Console"
)

# 1. Configure CORS Middleware (Credential support & multi-origin)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://localhost:4000", "http://localhost:7200"],
    allow_origin_regex=r"https?://.*",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# 2. Standardized Error Response Handler
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
app.include_router(user_router)
app.include_router(onboarding_router)
app.include_router(projects_router)
app.include_router(repositories_router)
app.include_router(memory_router)
app.include_router(webhooks_router)
app.include_router(github_router)

# Alias route at root level for GitHub App Setup Redirect (/github/setup)
app.get("/github/setup", include_in_schema=False)(github_installation_setup_callback)


@app.on_event("startup")
async def startup():
    try:
        with engine.connect() as conn:
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector;"))
            conn.commit()
        Base.metadata.create_all(bind=engine)
    except Exception as err:
        print(f"[DB INIT ERROR] Could not initialize database tables: {err}")


@app.get("/")
def root():
    return {
        "service": "Tzylo AI Service",
        "status": "running"
    }


@app.get("/health/db")
def health(db: Session = Depends(get_db)):
    try:
        # Perform a simple query to check database connectivity
        db.execute(text("SELECT 1"))
        return {"status": "healthy"}
    except Exception as e:
        return {"status": "unhealthy", "error": str(e)}