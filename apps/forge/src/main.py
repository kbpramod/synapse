import sys
import logging
from contextlib import asynccontextmanager
from pathlib import Path

# Add src to sys.path to enable direct imports across backend modules
SRC_DIR = Path(__file__).resolve().parent
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

def configure_logging() -> None:
    """
    The agent pipeline logs everything it does (discover/plan/build/run/heal/verify) at
    INFO level via logging.getLogger("forge.*"). Two things fight this:

    1. Nothing ever configured the root logger, so it sat at the default WARNING level
       and every one of those messages was dropped.
    2. Alembic's env.py calls logging.config.fileConfig(alembic.ini) on every startup
       (init_db() -> command.upgrade() -> env.py). alembic.ini's [logger_root] section
       hardcodes level=WARNING with its own stderr handler, and fileConfig() always
       reapplies that to the root logger — silently undoing (1) again a few
       milliseconds after this module finishes importing.

    So this must be called both at import time AND again right after init_db() runs
    in the lifespan startup, or Alembic's config wins.
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stdout,
        force=True,
    )

    # Root is INFO so every forge.* pipeline logger is visible, but that also unmutes
    # chatty third-party libraries that propagate to root — keep those at WARNING so
    # the actual discover/plan/build/run/heal/verify narration doesn't get buried.
    for noisy_logger in (
        "alembic",
        "sqlalchemy.engine",
        "httpx",
        "httpcore",
        "urllib3",
        "hpack",
        "h2",
        "postgrest",
        "storage3",
        "supabase",
        "asyncio",
    ):
        logging.getLogger(noisy_logger).setLevel(logging.WARNING)


configure_logging()

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from api.website import route as website_router
from api.cron import router as cron_router
from api.onboarding import router as onboarding_router
from db.migrations import init_db

logger = logging.getLogger("forge.main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Run database migrations automatically on server boot
    try:
        applied = init_db()
        # init_db() runs Alembic's env.py, which calls fileConfig(alembic.ini) and
        # resets the root logger back to WARNING (see configure_logging() docstring
        # above) — reassert our config now that migrations are done.
        configure_logging()
        if applied:
            logger.info(f"Database migrations applied on startup: {applied}")
        else:
            logger.info("Database schema is up to date.")
    except Exception as e:
        configure_logging()
        logger.error(f"Failed to run database migrations on startup: {e}")
    yield


app = FastAPI(title="Bessemer Backend API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
app.include_router(website_router, prefix="/api")
app.include_router(website_router)
app.include_router(onboarding_router, prefix="/api")
app.include_router(onboarding_router)
app.include_router(cron_router, prefix="/api")




@app.get("/")
def read_root():
    return {"message": "Hello World"}


@app.get("/health")
def health_check():
    return {"status": "ok"}