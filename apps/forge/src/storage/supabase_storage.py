import json
import logging
from typing import Any, Optional

from config import SUPABASE_URL, SUPABASE_SECRET_KEY, SUPABASE_STORAGE_BUCKET, SUPABASE_STORAGE_PREFIX

logger = logging.getLogger("forge.storage.supabase")

_client = None


def is_configured() -> bool:
    """Whether Supabase Storage credentials are present in the environment."""
    return bool(SUPABASE_URL and SUPABASE_SECRET_KEY)


def _get_client():
    global _client
    if _client is None:
        from supabase import create_client
        _client = create_client(SUPABASE_URL, SUPABASE_SECRET_KEY)
    return _client


def _bucket():
    return _get_client().storage.from_(SUPABASE_STORAGE_BUCKET)


def _namespaced(key: str) -> str:
    key = key.lstrip("/")
    return f"{SUPABASE_STORAGE_PREFIX}/{key}" if SUPABASE_STORAGE_PREFIX else key


def upload_bytes(key: str, data: bytes, content_type: str = "application/octet-stream") -> bool:
    """Uploads bytes to `<SUPABASE_STORAGE_PREFIX>/<key>` in the configured bucket.
    Best-effort: returns False (and logs) instead of raising, so a Supabase hiccup
    never breaks the underlying discovery/planning/build pipeline."""
    if not is_configured():
        return False
    try:
        _bucket().upload(
            _namespaced(key),
            data,
            {"content-type": content_type, "upsert": "true"},
        )
        return True
    except Exception as e:
        logger.warning(f"[SUPABASE STORAGE] Upload failed for '{key}': {e}")
        return False


def upload_text(key: str, text: str, content_type: str = "text/plain; charset=utf-8") -> bool:
    return upload_bytes(key, text.encode("utf-8"), content_type=content_type)


def upload_json(key: str, data: Any) -> bool:
    return upload_text(key, json.dumps(data, indent=2, ensure_ascii=False), content_type="application/json")


def download_bytes(key: str) -> Optional[bytes]:
    """Downloads `<SUPABASE_STORAGE_PREFIX>/<key>` from the configured bucket, or None on any failure."""
    if not is_configured():
        return None
    try:
        return _bucket().download(_namespaced(key))
    except Exception as e:
        logger.warning(f"[SUPABASE STORAGE] Download failed for '{key}': {e}")
        return None


def download_text(key: str) -> Optional[str]:
    data = download_bytes(key)
    return data.decode("utf-8") if data is not None else None
