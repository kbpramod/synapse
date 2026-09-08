import os
from pathlib import Path
from typing import Optional
from dotenv import load_dotenv

# Ensure .env is explicitly loaded from the project root
PROJECT_ROOT = Path(__file__).resolve().parent.parent
env_path = PROJECT_ROOT / ".env"
if env_path.exists():
    load_dotenv(dotenv_path=env_path)
load_dotenv()

# Database
DATABASE_URL = os.getenv("DATABASE_URL", "")

# Storage
# Durable artifacts (discovery/planner JSON, test scripts, verification reports) live in
# Supabase Storage now. FORGE_CACHE_ROOT is just a disposable local scratch space used to
# materialize files where a real filesystem path is required (e.g. Playwright subprocess
# execution) — safe to clear at any time, defaults to the OS temp directory.
import tempfile

STORAGE_ROOT = Path(os.getenv("FORGE_CACHE_ROOT", str(Path(tempfile.gettempdir()) / "forge-cache"))).resolve()
STORAGE_ROOT.mkdir(parents=True, exist_ok=True)

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_SECRET_KEY = os.getenv("SUPABASE_SECRET_KEY", "")
SUPABASE_STORAGE_BUCKET = os.getenv("SUPABASE_STORAGE_BUCKET", "tzylo")
SUPABASE_STORAGE_PREFIX = os.getenv("SUPABASE_STORAGE_PREFIX", "forge").strip("/")

# Headless mode control — defaults to HEADED so browser runs are visible for analysis.
# Override with FORGE_HEADLESS=true or HEADLESS=true to go back to headless.
_raw_headless = os.getenv("FORGE_HEADLESS", os.getenv("HEADLESS", "false")).strip().lower()
DEFAULT_HEADLESS = _raw_headless in ("true", "1", "yes", "headless")

# LLM / AI Credits
AICREDITS_API_KEY = os.getenv("AICREDITS_API_KEY", "")
AICREDITS_BASE_URL = os.getenv("AICREDITS_BASE_URL", "https://api.aicredits.in/v1")
AICREDITS_MODEL = os.getenv("AICREDITS_MODEL", "gpt-4o-mini")

# Timeouts
DEFAULT_DISCOVERY_TIMEOUT_MS = int(os.getenv("FORGE_DISCOVERY_TIMEOUT_MS", "30000"))
DEFAULT_TEST_TIMEOUT_S = int(os.getenv("FORGE_TEST_TIMEOUT_S", "45"))

# Script Language (python or typescript, default: python)
DEFAULT_TEST_LANGUAGE = os.getenv("FORGE_TEST_LANGUAGE", "python").strip().lower()


def is_headless(override: Optional[bool] = None) -> bool:
    """Returns whether browser actions should run headless. Defaults to headed (False)."""
    if override is not None:
        return override
    return DEFAULT_HEADLESS


def get_storage_root() -> Path:
    """Returns the base storage directory."""
    return STORAGE_ROOT
