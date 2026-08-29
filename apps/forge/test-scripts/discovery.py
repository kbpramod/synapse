import json
import os
import sys
from pathlib import Path

# Add src to sys.path
root_dir = Path(__file__).resolve().parent.parent
src_dir = root_dir / "src"
if str(src_dir) not in sys.path:
    sys.path.insert(0, str(src_dir))

from browser.discovery import discover_site_sync
from schemas.discovery import SiteDiscoveryConfig
from storage.local import get_discovery_storage_dir


def main():
    target_url = sys.argv[1] if len(sys.argv) > 1 else "https://tzylo.com"
    print("=" * 75)
    print(f"FORGE MULTI-PAGE QUEUE DISCOVERY TEST: {target_url}")
    print("=" * 75)

    config = SiteDiscoveryConfig(
        max_pages=5,
        max_depth=2,
        default_role="guest",
        settle_ms=1000,
        timeout_ms=25000,
        save_to_storage=True
    )

    result = discover_site_sync(target_url, config=config)

    print(f"\n[Crawl Overview]")
    print(f"  Target Domain          : {result.domain}")
    print(f"  Start URL              : {result.start_url}")
    print(f"  Total Pages Discovered : {result.total_pages_discovered}")
    print(f"  Max Depth Reached      : {result.max_depth_reached}")

    print(f"\n[Discovered Pages Breakdown]")
    for i, page in enumerate(result.pages, 1):
        print(f"\n  ({i}) [{page.slug}] -> {page.title}")
        print(f"      URL        : {page.url}")
        print(f"      Depth      : {page.depth} (Discovered from: {page.discovered_from or 'ROOT'})")
        print(f"      Storage File: {page.page_file}")
        print(f"      Elements   : Buttons={page.element_counts['buttons']}, Inputs={page.element_counts['inputs']}, Links={page.element_counts['links']}, Headings={page.element_counts['headings']}")
        print(f"      Outbound Internal Links: {len(page.outbound_internal_links)}")

    disc_dir = get_discovery_storage_dir(target_url)
    print(f"\n[Storage Directory Verification]")
    print(f"  Root Discovery Folder: {disc_dir}")
    print(f"  Sitemap Manifest     : {disc_dir / 'sitemap.json'}")
    print(f"  Pages Folder         : {disc_dir / 'pages'}")
    
    pages_folder = disc_dir / "pages"
    if pages_folder.exists():
        subfolders = [f.name for f in pages_folder.iterdir() if f.is_dir()]
        print(f"  Created Page Folders : {subfolders}")


if __name__ == "__main__":
    main()
