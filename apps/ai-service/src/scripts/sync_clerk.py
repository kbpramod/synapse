"""
CLI script to synchronize all organizations and members from Clerk into local PostgreSQL.
Usage:
    uv run python -m src.scripts.sync_clerk
"""
import sys
import logging
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


def main():
    try:
        from db.session import SessionLocal
        from services.clerk_service import clerk_service
        from models.organization import Organization
        from models.organization_member import OrganizationMember
        from models.user import User
    except ImportError:
        from src.db.session import SessionLocal
        from src.services.clerk_service import clerk_service
        from src.models.organization import Organization
        from src.models.organization_member import OrganizationMember
        from src.models.user import User

    db = SessionLocal()
    try:
        logger.info("Connecting to Clerk and syncing organizations and members...")
        result = clerk_service.sync_all_organizations_and_members(db)
        logger.info("------------------------------------------------------------")
        logger.info(f"Sync complete! Found {result['organizations_count']} organizations.")
        logger.info(f"Total memberships synced: {result['total_memberships_synced']}")
        logger.info("------------------------------------------------------------")
        for org in result.get("organizations", []):
            logger.info(f"  * Org: {org['organization_name']} (ID: {org['organization_id']}) -> {org['members_synced_count']} members")

        total_orgs = db.query(Organization).count()
        total_members = db.query(OrganizationMember).count()
        total_users = db.query(User).count()
        logger.info("------------------------------------------------------------")
        logger.info(f"Database Current Totals:")
        logger.info(f"  Organizations:        {total_orgs}")
        logger.info(f"  Organization Members: {total_members}")
        logger.info(f"  Users:                {total_users}")
        logger.info("------------------------------------------------------------")
    except Exception as exc:
        logger.error(f"Sync failed with error: {exc}", exc_info=True)
        sys.exit(1)
    finally:
        db.close()


if __name__ == "__main__":
    main()
