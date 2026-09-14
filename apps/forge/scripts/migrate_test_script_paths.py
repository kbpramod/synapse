import logging
import sys
from pathlib import Path

# Add project root and src to sys.path
root_dir = Path(__file__).resolve().parent.parent
src_dir = root_dir / "src"
if str(src_dir) not in sys.path:
    sys.path.insert(0, str(src_dir))

from sqlalchemy import text
from db.connection import get_connection
from storage.supabase_storage import get_storage_path, list_files, file_exists
from storage.local import sanitize_domain

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("forge.scripts.migrate_test_script_paths")


def migrate_script_paths() -> None:
    """
    Migrates any local filesystem paths (e.g. C:\\Users\\...\\forge-cache\\...)
    stored in `forge.tests.script_path` to durable namespaced Supabase Storage paths,
    and resolves missing script_paths for tests whose scripts exist in Supabase Storage.
    """
    logger.info("Starting migration of tests.script_path to Supabase Storage paths...")

    with get_connection() as conn:
        # 1. Migrate tests that currently have local filesystem paths
        rows = conn.execute(
            text("SELECT test_id, domain, script_path FROM forge.tests WHERE script_path IS NOT NULL;")
        ).fetchall()

        migrated_count = 0
        already_migrated = 0

        for test_id, domain, script_path in rows:
            clean_sp = script_path.replace("\\\\", "/").replace("\\", "/")

            # Check if it's already a clean Supabase path (not an absolute local path)
            is_local = (
                ":/" in clean_sp
                or "forge-cache" in clean_sp
                or clean_sp.startswith("/")
                or clean_sp.startswith("C:")
                or clean_sp.startswith("D:")
            )

            if not is_local:
                already_migrated += 1
                continue

            clean_dom = sanitize_domain(domain)
            idx = clean_sp.find(clean_dom)
            if idx != -1:
                rel_key = clean_sp[idx:]
            else:
                rel_key = f"{clean_dom}/tests/{test_id}/test.py"

            new_supabase_path = get_storage_path(rel_key)

            conn.execute(
                text("UPDATE forge.tests SET script_path = :new_path WHERE test_id = :test_id;"),
                {"new_path": new_supabase_path, "test_id": test_id},
            )
            logger.info(f"  [MIGRATED LOCAL -> SUPABASE] {test_id}: '{script_path}' -> '{new_supabase_path}'")
            migrated_count += 1

        # 2. For tests with NULL/empty script_path, check if their scripts exist in Supabase Storage
        null_rows = conn.execute(
            text("SELECT test_id, domain FROM forge.tests WHERE script_path IS NULL OR script_path = '';")
        ).fetchall()

        # Cache directory listings per domain to minimize network calls
        domain_files_cache = {}

        discovered_count = 0
        for test_id, domain in null_rows:
            clean_dom = sanitize_domain(domain)
            if clean_dom not in domain_files_cache:
                items = list_files(f"{clean_dom}/tests")
                domain_files_cache[clean_dom] = {it.get("name") for it in items if it.get("name")}

            existing_names = domain_files_cache[clean_dom]

            # Candidate filenames / keys to look for in Supabase
            candidates = []
            # Directory artifact pattern
            candidates.append(f"{clean_dom}/tests/{test_id}/test.py")
            # Single file pattern
            candidates.append(f"{clean_dom}/tests/{test_id}.py")
            if "_" in test_id:
                unprefixed = test_id.split("_", 1)[1]
                candidates.append(f"{clean_dom}/tests/{unprefixed}/test.py")
                candidates.append(f"{clean_dom}/tests/{unprefixed}.py")

            matched_key = None
            for cand in candidates:
                # Fast check against listing
                cand_name = cand.split(f"{clean_dom}/tests/")[1]
                top_part = cand_name.split("/")[0]
                if top_part in existing_names:
                    # If it's a directory or file in existing_names, verify file exists
                    if file_exists(cand):
                        matched_key = cand
                        break

            if matched_key:
                new_path = get_storage_path(matched_key)
                conn.execute(
                    text("UPDATE forge.tests SET script_path = :new_path WHERE test_id = :test_id;"),
                    {"new_path": new_path, "test_id": test_id},
                )
                logger.info(f"  [DISCOVERED IN SUPABASE] {test_id} -> '{new_path}'")
                discovered_count += 1

        logger.info(
            f"Migration complete: {migrated_count} local paths converted, "
            f"{discovered_count} discovered from Supabase Storage, "
            f"{already_migrated} already in Supabase Storage format."
        )


if __name__ == "__main__":
    migrate_script_paths()
