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
STORAGE_ROOT = Path(os.getenv("FORGE_STORAGE_ROOT", str(PROJECT_ROOT / "storage"))).resolve()
STORAGE_ROOT.mkdir(parents=True, exist_ok=True)

# Headless mode control (default: True, override via FORGE_HEADLESS=false or HEADLESS=false)
_raw_headless = os.getenv("FORGE_HEADLESS", os.getenv("HEADLESS", "true")).strip().lower()
DEFAULT_HEADLESS = _raw_headless not in ("false", "0", "no", "headed")

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
    """Returns whether browser actions should run headless."""
    if override is not None:
        return override
    raw = os.getenv("FORGE_HEADLESS", os.getenv("HEADLESS", "true")).strip().lower()
    return raw not in ("false", "0", "no", "headed")


def get_storage_root() -> Path:
    """Returns the base storage directory."""
    return STORAGE_ROOT
