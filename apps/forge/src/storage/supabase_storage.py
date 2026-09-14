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
    if not SUPABASE_STORAGE_PREFIX:
        return key
    prefix = SUPABASE_STORAGE_PREFIX.strip("/")
    if key == prefix or key.startswith(f"{prefix}/"):
        return key
    return f"{prefix}/{key}"


def get_storage_path(key: str) -> str:
    """Returns the namespaced path within the Supabase Storage bucket."""
    return _namespaced(key)


def get_public_url(key: str) -> str:
    """Returns the public URL for a file in Supabase Storage."""
    if not is_configured():
        return ""
    try:
        res = _bucket().get_public_url(_namespaced(key))
        return res if isinstance(res, str) else res.get("publicUrl", "")
    except Exception as e:
        logger.warning(f"[SUPABASE STORAGE] get_public_url failed for '{key}': {e}")
        return ""


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


def download_json(key: str) -> Optional[Any]:
    """Downloads and parses JSON from `<SUPABASE_STORAGE_PREFIX>/<key>`, or None on failure."""
    text = download_text(key)
    if not text:
        return None
    try:
        return json.loads(text)
    except Exception as e:
        logger.warning(f"[SUPABASE STORAGE] JSON parse failed for '{key}': {e}")
        return None


def file_exists(key: str) -> bool:
    """Checks whether `<SUPABASE_STORAGE_PREFIX>/<key>` exists in Supabase Storage."""
    if not is_configured():
        return False
    try:
        # Try downloading 1 byte or checking download
        data = download_bytes(key)
        return data is not None
    except Exception:
        return False


def list_files(prefix: str = "") -> list:
    """Lists files under `<SUPABASE_STORAGE_PREFIX>/<prefix>` in the configured bucket."""
    if not is_configured():
        return []
    try:
        namespaced_prefix = _namespaced(prefix).rstrip("/")
        items = _bucket().list(path=namespaced_prefix)
        return items or []
    except Exception as e:
        logger.warning(f"[SUPABASE STORAGE] list_files failed for prefix '{prefix}': {e}")
        return []


def delete_file(key: str) -> bool:
    """Deletes `<SUPABASE_STORAGE_PREFIX>/<key>` from the configured bucket."""
    if not is_configured():
        return False
    try:
        _bucket().remove([_namespaced(key)])
        return True
    except Exception as e:
        logger.warning(f"[SUPABASE STORAGE] delete_file failed for '{key}': {e}")
        return False

