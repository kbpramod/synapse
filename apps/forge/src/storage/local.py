import json
import logging
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import urlparse
from dotenv import load_dotenv

from storage.supabase_storage import upload_text, is_configured as _cloud_configured

load_dotenv()

logger = logging.getLogger("forge.storage.local")


def _get_storage_root() -> Path:
    """
    Returns the local scratch directory used to materialize files that need a real
    filesystem path (e.g. a Playwright subprocess executing a test script).

    This is a disposable cache, not durable storage — every write that matters is
    also mirrored to Supabase Storage via mirror_to_cloud(). Defaults to the OS temp
    directory; override with FORGE_CACHE_ROOT if a fixed local path is useful for
    debugging.
    """
    root = os.getenv("FORGE_CACHE_ROOT")
    path = Path(root) if root else Path(tempfile.gettempdir()) / "forge-cache"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _relative_key(path: Path) -> str:
    """Maps a local cache path back to the storage key it mirrors in Supabase,
    e.g. <cache_root>/example.com/tests/flow_login.py -> example.com/tests/flow_login.py"""
    try:
        rel = path.resolve().relative_to(_get_storage_root().resolve())
    except ValueError:
        rel = Path(path.name)
    return rel.as_posix()


def mirror_to_cloud(path: Path, content: str, content_type: str = "text/plain; charset=utf-8") -> None:
    """
    Best-effort mirror of a locally-cached artifact to Supabase Storage, the durable
    copy. Never raises — a Supabase hiccup should not break discovery/planning/build.
    """
    if not _cloud_configured():
        return
    key = _relative_key(path)
    if not upload_text(key, content, content_type=content_type):
        logger.warning(f"[LOCAL STORAGE] Could not mirror '{key}' to Supabase Storage.")


def sanitize_domain(url_or_domain: str) -> str:
    """
    Extracts a sanitized domain name suitable for folder naming.
    e.g. 'https://tzylo.com/about?x=1' -> 'tzylo.com'
    """
    if "://" in url_or_domain:
        parsed = urlparse(url_or_domain)
        netloc = parsed.netloc or parsed.path
    else:
        netloc = url_or_domain

    netloc = netloc.split("@")[-1].split(":")[0]
    sanitized = re.sub(r"[^\w\.-]", "_", netloc).strip("._")
    return sanitized or "unknown_domain"


def sanitize_page_slug(url: str, default: str = "home") -> str:
    """
    Generates a folder-friendly page slug from the URL path.
    e.g. 'https://tzylo.com/' -> 'home'
    e.g. 'https://tzylo.com/about' -> 'about'
    e.g. 'https://tzylo.com/blog/getting-started' -> 'blog_getting-started'
    """
    parsed = urlparse(url)
    path = parsed.path.strip("/")
    if not path:
        return default
    # Replace slashes and invalid chars with underscores or subpaths
    safe_slug = re.sub(r"[^\w\.-]", "_", path)
    return safe_slug or default


def get_website_storage_dir(url_or_domain: str) -> Path:
    """Returns the root directory for a website: <storage_root>/<domain>/"""
    domain = sanitize_domain(url_or_domain)
    site_dir = _get_storage_root() / domain
    site_dir.mkdir(parents=True, exist_ok=True)
    return site_dir


def get_discovery_storage_dir(url_or_domain: str) -> Path:
    """Returns the discovery directory for a website: <storage_root>/<domain>/discovery/"""
    disc_dir = get_website_storage_dir(url_or_domain) / "discovery"
    disc_dir.mkdir(parents=True, exist_ok=True)
    return disc_dir


def get_pages_storage_dir(url_or_domain: str) -> Path:
    """Returns the base pages directory: <storage_root>/<domain>/discovery/pages/"""
    pages_dir = get_discovery_storage_dir(url_or_domain) / "pages"
    pages_dir.mkdir(parents=True, exist_ok=True)
    return pages_dir


def _write_json(path: Path, data: Any) -> None:
    """Writes JSON to the local cache and mirrors it to Supabase Storage."""
    text = json.dumps(data, indent=2, ensure_ascii=False)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    mirror_to_cloud(path, text, content_type="application/json")


def get_page_folder(url: str, custom_slug: Optional[str] = None) -> Path:
    """
    Returns the dedicated folder for a specific page:
    e.g. <storage_root>/<domain>/discovery/pages/about/
    """
    pages_dir = get_pages_storage_dir(url)
    slug = custom_slug or sanitize_page_slug(url)
    page_folder = pages_dir / slug
    page_folder.mkdir(parents=True, exist_ok=True)
    return page_folder


def save_page_discovery(
    url: str,
    data: Dict[str, Any],
    custom_slug: Optional[str] = None,
    filename: str = "index.json"
) -> Path:
    """
    Saves a discovered page into its own dedicated folder:
    e.g. <storage_root>/<domain>/discovery/pages/<slug>/index.json
    This allows storing screenshots, state variations, and extra metadata alongside index.json.
    """
    page_folder = get_page_folder(url, custom_slug=custom_slug)
    target_file = page_folder / filename
    _write_json(target_file, data)
    return target_file


def save_site_discovery(
    url_or_domain: str,
    data: Dict[str, Any],
    filename: str = "sitemap.json"
) -> Path:
    """
    Saves the global site discovery / sitemap / crawl graph into:
    <storage_root>/<domain>/discovery/sitemap.json
    and <storage_root>/<domain>/discovery/site_discovery.json
    """
    disc_dir = get_discovery_storage_dir(url_or_domain)
    target_file = disc_dir / filename
    _write_json(target_file, data)

    # Also save as site_discovery.json for convenience
    if filename != "site_discovery.json":
        sec_file = disc_dir / "site_discovery.json"
        _write_json(sec_file, data)

    return target_file


def save_discovery_result(
    url: str,
    data: Dict[str, Any],
    filename: Optional[str] = None,
    save_as_page: bool = True
) -> Path:
    """
    Backward-compatible save helper.
    Saves to <storage_root>/<domain>/discovery/pages/<slug>/index.json
    and updates <storage_root>/<domain>/discovery/discovery.json
    """
    page_path = save_page_discovery(url, data, filename=filename or "index.json")
    
    # Also save root discovery.json snapshot
    disc_dir = get_discovery_storage_dir(url)
    root_discovery = disc_dir / "discovery.json"
    _write_json(root_discovery, data)

    return page_path


def get_script_history_dir(url_or_domain: str, test_id: str) -> Path:
    """Revision history for one test: <storage_root>/<domain>/tests/history/<test_id>/"""
    safe_test_id = re.sub(r"[^\w\.-]", "_", str(test_id)).strip("._") or "unknown_test"
    history_dir = get_website_storage_dir(url_or_domain) / "tests" / "history" / safe_test_id
    history_dir.mkdir(parents=True, exist_ok=True)
    return history_dir


def save_script_revision(
    url_or_domain: str,
    test_id: str,
    run_id: str,
    attempt: int,
    code: str,
    metadata: Optional[Dict[str, Any]] = None,
) -> Path:
    """
    Archives one version of a test script as <run_id>_attempt<N>.py, alongside a
    <run_id>_attempt<N>.json describing why it changed.

    Self-healing overwrites the script in place, so without this the previous version — and
    the reason it was replaced — is lost. attempt 0 is the code as it stood before any
    healing, so a run's full lineage reads attempt0 -> attempt1 -> ...
    """
    history_dir = get_script_history_dir(url_or_domain, test_id)
    safe_run_id = re.sub(r"[^\w\.-]", "_", str(run_id)).strip("._") or "run"
    stem = f"{safe_run_id}_attempt{attempt}"

    script_file = history_dir / f"{stem}.py"
    with open(script_file, "w", encoding="utf-8") as f:
        f.write(code)
    mirror_to_cloud(script_file, code, content_type="text/x-python")

    record = {
        "test_id": test_id,
        "run_id": run_id,
        "attempt": attempt,
        "saved_at": datetime.now(timezone.utc).isoformat(),
        "code_bytes": len(code),
        **(metadata or {}),
    }
    _write_json(history_dir / f"{stem}.json", record)

    return script_file


def get_planner_storage_dir(url_or_domain: str) -> Path:
    """Returns the planner directory for a website: <storage_root>/<domain>/planner/"""
    planner_dir = get_website_storage_dir(url_or_domain) / "planner"
    planner_dir.mkdir(parents=True, exist_ok=True)
    return planner_dir


def save_hypotheses(
    url_or_domain: str,
    hypotheses: list,
    filename: str = "hypotheses.json",
) -> Path:
    """
    Saves generated test hypotheses for a website:
    - Global list: <storage_root>/<domain>/planner/hypotheses.json
    - Divided by category:
      - <storage_root>/<domain>/planner/smoke/<id>.json
      - <storage_root>/<domain>/planner/flows/<id>.json
    - Summary metadata: <storage_root>/<domain>/planner/summary.json
    """
    planner_dir = get_planner_storage_dir(url_or_domain)
    smoke_dir = planner_dir / "smoke"
    flows_dir = planner_dir / "flows"
    smoke_dir.mkdir(parents=True, exist_ok=True)
    flows_dir.mkdir(parents=True, exist_ok=True)

    # Save complete hypotheses array
    main_file = planner_dir / filename
    _write_json(main_file, hypotheses)

    smoke_tests = []
    flow_tests = []

    for item in hypotheses:
        test_type = str(item.get("type", "FLOW")).upper()
        test_id = item.get("id", "test_item")
        if test_type == "SMOKE":
            smoke_tests.append(item)
            item_file = smoke_dir / f"{test_id}.json"
        else:
            flow_tests.append(item)
            item_file = flows_dir / f"{test_id}.json"

        _write_json(item_file, item)

    # Summary metadata
    summary = {
        "url_or_domain": url_or_domain,
        "total_hypotheses": len(hypotheses),
        "smoke_count": len(smoke_tests),
        "flow_count": len(flow_tests),
        "smoke_ids": [t.get("id") for t in smoke_tests],
        "flow_ids": [t.get("id") for t in flow_tests],
    }
    _write_json(planner_dir / "summary.json", summary)

    return main_file
