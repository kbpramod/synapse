import os
import logging
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional
import httpx

logger = logging.getLogger(__name__)


class BlobStorageService(ABC):
    """Abstract base class for blob/object storage providers."""

    @abstractmethod
    async def upload(self, key: str, data: bytes, content_type: str = "text/plain") -> str:
        """Upload raw data to storage under the given key and return the storage key/URI."""
        pass

    @abstractmethod
    async def get(self, key: str) -> bytes:
        """Download raw bytes from storage for the given key."""
        pass

    @abstractmethod
    async def delete(self, key: str) -> bool:
        """Delete an object from storage."""
        pass

    @abstractmethod
    async def exists(self, key: str) -> bool:
        """Check if an object exists in storage."""
        pass


class SupabaseBlobStorageService(BlobStorageService):
    """Supabase Object Storage implementation using Supabase Storage REST API."""

    def __init__(
        self,
        supabase_url: Optional[str] = None,
        secret_key: Optional[str] = None,
        bucket_name: Optional[str] = None
    ):
        self.supabase_url = (supabase_url or os.getenv("SUPABASE_URL", "")).rstrip("/")
        self.secret_key = secret_key or os.getenv("SUPABASE_SECRET_KEY", "")
        self.bucket = bucket_name or os.getenv("SUPABASE_STORAGE_BUCKET", "synapse")
        self.timeout = float(os.getenv("STORAGE_TIMEOUT", "30.0"))

        if not self.supabase_url or not self.secret_key:
            raise ValueError("SUPABASE_URL and SUPABASE_SECRET_KEY must be configured for Supabase storage.")

    def _headers(self, content_type: Optional[str] = None) -> dict:
        headers = {
            "apikey": self.secret_key,
            "Authorization": f"Bearer {self.secret_key}",
        }
        if content_type:
            headers["Content-Type"] = content_type
        return headers

    def _object_url(self, key: str) -> str:
        clean_key = key.lstrip("/")
        return f"{self.supabase_url}/storage/v1/object/{self.bucket}/{clean_key}"

    def _auth_download_url(self, key: str) -> str:
        clean_key = key.lstrip("/")
        return f"{self.supabase_url}/storage/v1/object/authenticated/{self.bucket}/{clean_key}"

    async def upload(self, key: str, data: bytes, content_type: str = "text/plain") -> str:
        url = self._object_url(key)
        headers = self._headers(content_type)
        headers["x-upsert"] = "true"

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(url, headers=headers, content=data)
            if response.status_code in (200, 201):
                logger.info(f"[SUPABASE STORAGE] Successfully uploaded '{key}' to bucket '{self.bucket}'")
                return f"{self.bucket}/{key.lstrip('/')}"
            
            # If bucket might not exist yet, try creating bucket and retry once
            if response.status_code == 404 or "Bucket not found" in response.text:
                await self._ensure_bucket()
                retry_response = await client.post(url, headers=headers, content=data)
                if retry_response.status_code in (200, 201):
                    logger.info(f"[SUPABASE STORAGE] Created bucket '{self.bucket}' and uploaded '{key}'")
                    return f"{self.bucket}/{key.lstrip('/')}"
                raise RuntimeError(
                    f"Supabase storage upload failed ({retry_response.status_code}): {retry_response.text}"
                )

            raise RuntimeError(f"Supabase storage upload failed ({response.status_code}): {response.text}")

    async def _ensure_bucket(self) -> None:
        """Attempt to create the bucket if it doesn't already exist."""
        create_url = f"{self.supabase_url}/storage/v1/bucket"
        payload = {
            "id": self.bucket,
            "name": self.bucket,
            "public": False
        }
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            res = await client.post(create_url, headers=self._headers("application/json"), json=payload)
            logger.info(f"[SUPABASE STORAGE] Ensure bucket '{self.bucket}' status: {res.status_code}")

    async def get(self, key: str) -> bytes:
        # Strip bucket prefix if key includes it
        clean_key = key
        if clean_key.startswith(f"{self.bucket}/"):
            clean_key = clean_key[len(self.bucket) + 1:]

        url = self._auth_download_url(clean_key)
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.get(url, headers=self._headers())
            if response.status_code == 200:
                return response.content
            # Try unauthenticated object URL as fallback if public
            pub_url = self._object_url(clean_key)
            pub_res = await client.get(pub_url, headers=self._headers())
            if pub_res.status_code == 200:
                return pub_res.content

            raise FileNotFoundError(f"Object '{key}' not found in Supabase storage ({response.status_code})")

    async def delete(self, key: str) -> bool:
        clean_key = key
        if clean_key.startswith(f"{self.bucket}/"):
            clean_key = clean_key[len(self.bucket) + 1:]

        url = self._object_url(clean_key)
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.delete(url, headers=self._headers())
            return response.status_code in (200, 204)

    async def exists(self, key: str) -> bool:
        clean_key = key
        if clean_key.startswith(f"{self.bucket}/"):
            clean_key = clean_key[len(self.bucket) + 1:]

        url = self._auth_download_url(clean_key)
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.head(url, headers=self._headers())
            return response.status_code == 200


class LocalBlobStorageService(BlobStorageService):
    """Local filesystem storage implementation for offline development and testing."""

    def __init__(self, base_dir: Optional[str] = None):
        dir_path = base_dir or os.getenv("STORAGE_LOCAL_DIR", "./storage/transcripts")
        self.base_dir = Path(dir_path).resolve()
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def _resolve_safe_path(self, key: str) -> Path:
        clean_key = key.lstrip("/\\")
        target_path = (self.base_dir / clean_key).resolve()
        # Security check against directory traversal
        if not str(target_path).startswith(str(self.base_dir)):
            raise ValueError(f"Illegal storage key path traversal: '{key}'")
        return target_path

    async def upload(self, key: str, data: bytes, content_type: str = "text/plain") -> str:
        target_path = self._resolve_safe_path(key)
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_bytes(data)
        logger.info(f"[LOCAL STORAGE] Wrote {len(data)} bytes to {target_path}")
        return f"local://{clean_key}" if (clean_key := key.lstrip("/\\")) else key

    async def get(self, key: str) -> bytes:
        clean_key = key.removeprefix("local://")
        target_path = self._resolve_safe_path(clean_key)
        if not target_path.exists():
            raise FileNotFoundError(f"Local storage key not found: {key}")
        return target_path.read_bytes()

    async def delete(self, key: str) -> bool:
        clean_key = key.removeprefix("local://")
        target_path = self._resolve_safe_path(clean_key)
        if target_path.exists():
            target_path.unlink()
            return True
        return False

    async def exists(self, key: str) -> bool:
        clean_key = key.removeprefix("local://")
        target_path = self._resolve_safe_path(clean_key)
        return target_path.exists()


_blob_storage_instance: Optional[BlobStorageService] = None


def get_blob_storage() -> BlobStorageService:
    """
    Factory to retrieve the active BlobStorageService singleton.
    Prefers Supabase storage when credentials exist, otherwise falls back to local storage.
    """
    global _blob_storage_instance
    if _blob_storage_instance is not None:
        return _blob_storage_instance

    backend = os.getenv("BLOB_STORAGE_BACKEND", "").lower().strip()
    supabase_url = os.getenv("SUPABASE_URL")
    supabase_key = os.getenv("SUPABASE_SECRET_KEY")

    if backend == "local":
        logger.info("[STORAGE FACTORY] Explicit 'local' backend configured.")
        _blob_storage_instance = LocalBlobStorageService()
    elif supabase_url and supabase_key:
        try:
            logger.info("[STORAGE FACTORY] Initializing Supabase Blob Storage...")
            _blob_storage_instance = SupabaseBlobStorageService()
        except Exception as e:
            logger.warning(f"[STORAGE FACTORY] Supabase init failed ({e}), falling back to local storage.")
            _blob_storage_instance = LocalBlobStorageService()
    else:
        logger.info("[STORAGE FACTORY] No cloud credentials detected; using LocalBlobStorageService.")
        _blob_storage_instance = LocalBlobStorageService()

    return _blob_storage_instance
