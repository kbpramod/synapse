import json
import os
import re
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import urlparse
from dotenv import load_dotenv

load_dotenv()


def _get_storage_root() -> Path:
    """Returns the base storage directory configured in FORGE_STORAGE_ROOT or default."""
    root = os.getenv("FORGE_STORAGE_ROOT")
    if root:
        path = Path(root)
    else:
        path = Path("storage").resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


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
    with open(target_file, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
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
    with open(target_file, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    
    # Also save as site_discovery.json for convenience
    if filename != "site_discovery.json":
        sec_file = disc_dir / "site_discovery.json"
        with open(sec_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

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
    with open(root_discovery, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    return page_path
