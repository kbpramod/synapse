import os
import logging
from datetime import datetime
from typing import List, Optional, Dict, Any, Set
from clerk_backend_api import Clerk
from sqlalchemy.orm import Session

try:
    from models.organization import Organization
    from models.organization_member import OrganizationMember
    from models.user import User
except ImportError:
    from src.models.organization import Organization
    from src.models.organization_member import OrganizationMember
    from src.models.user import User

logger = logging.getLogger(__name__)


class ClerkService:
    """Service wrapper for Clerk Backend API organization operations and database sync."""

    def __init__(self, secret_key: Optional[str] = None):
        self._secret_key = secret_key
        self._client: Optional[Clerk] = None

    @property
    def client(self) -> Clerk:
        if self._client is None:
            key = self._secret_key or os.getenv("CLERK_SECRET_KEY")
            if not key:
                raise RuntimeError("CLERK_SECRET_KEY is not configured in environment variables.")
            self._client = Clerk(bearer_auth=key)
        return self._client

    def get_organization(self, organization_id: str) -> Optional[Dict[str, Any]]:
        """Fetch organization details directly from Clerk."""
        try:
            org = self.client.organizations.get(organization_id=organization_id)
            if not org:
                return None
            return {
                "id": org.id,
                "name": org.name,
                "slug": org.slug,
                "image_url": getattr(org, "image_url", None)
            }
        except Exception as exc:
            logger.warning(f"[CLERK] Failed to fetch organization '{organization_id}': {exc}")
            return None

    def list_organization_members(self, organization_id: str) -> List[Dict[str, Any]]:
        """List active members of an organization from Clerk."""
        try:
            res = self.client.organization_memberships.list(organization_id=organization_id)
            members = []
            raw_members = getattr(res, "data", []) or []
            for item in raw_members:
                pub = getattr(item, "public_user_data", None)
                fname = getattr(pub, "first_name", "") or ""
                lname = getattr(pub, "last_name", "") or ""
                full_name = f"{fname} {lname}".strip() or None

                user_id = getattr(pub, "user_id", "") if pub else ""
                email = getattr(pub, "identifier", None) if pub else None
                image_url = getattr(pub, "image_url", None) if pub else None

                members.append({
                    "user_id": user_id,
                    "name": full_name or email or user_id,
                    "email": email,
                    "role": getattr(item, "role", "org:member"),
                    "image_url": image_url
                })
            return members
        except Exception as exc:
            logger.error(f"[CLERK] Failed to list members for organization '{organization_id}': {exc}")
            raise

    def invite_member(
        self,
        organization_id: str,
        email_address: str,
        role: str = "org:member",
        inviter_user_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """Create an organization invitation via Clerk."""
        try:
            clerk_role = role if role.startswith("org:") else f"org:{role}"
            kwargs: Dict[str, Any] = {
                "organization_id": organization_id,
                "email_address": email_address,
                "role": clerk_role
            }
            if inviter_user_id:
                kwargs["inviter_user_id"] = inviter_user_id

            invitation = self.client.organization_invitations.create(**kwargs)
            return {
                "id": invitation.id,
                "email": invitation.email_address,
                "role": invitation.role,
                "status": getattr(invitation, "status", "pending") or "pending"
            }
        except Exception as exc:
            logger.error(f"[CLERK] Failed to invite '{email_address}' to '{organization_id}': {exc}")
            raise

    def sync_organization_and_members(
        self,
        db: Session,
        organization_id: str,
        fallback_name: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Synchronizes an organization and its full member roster from Clerk into
        local PostgreSQL `organizations`, `organization_members`, and `users` tables.
        """
        logger.info(f"[CLERK SYNC] Starting sync for organization '{organization_id}'...")

        # 1. Fetch & Upsert Organization
        clerk_org = self.get_organization(organization_id)
        org_name = (clerk_org.get("name") if clerk_org else None) or fallback_name

        if not clerk_org and not fallback_name:
            logger.info(f"[CLERK SYNC] Organization '{organization_id}' not found in Clerk; skipping creation.")
            return {"organization_id": organization_id, "synced_members": 0, "status": "not_found"}

        org = db.query(Organization).filter(Organization.id == organization_id).first()
        if not org:
            org = Organization(
                id=organization_id,
                name=org_name or f"Organization {organization_id}"
            )
            db.add(org)
            db.commit()
            db.refresh(org)
        elif org_name and org.name != org_name:
            org.name = org_name
            org.updated_at = datetime.utcnow()
            db.commit()
            db.refresh(org)

        # 2. Fetch & Upsert Memberships and Users
        clerk_members = self.list_organization_members(organization_id)
        synced_user_ids: Set[str] = set()

        for member in clerk_members:
            clerk_user_id = member["user_id"]
            if not clerk_user_id:
                continue

            email = member.get("email")
            name = member.get("name")
            raw_role = member.get("role", "member")
            role = raw_role.split(":", 1)[1] if ":" in str(raw_role) else str(raw_role)

            # Upsert User
            user = db.query(User).filter(User.clerk_user_id == clerk_user_id).first()
            if not user:
                user = User(
                    clerk_user_id=clerk_user_id,
                    email=email,
                    name=name,
                    is_verified=True
                )
                db.add(user)
                try:
                    db.commit()
                    db.refresh(user)
                except Exception:
                    db.rollback()
                    user = db.query(User).filter(User.clerk_user_id == clerk_user_id).first()
                    if not user:
                        raise
            else:
                updated = False
                if email and user.email != email:
                    user.email = email
                    updated = True
                if name and user.name != name:
                    user.name = name
                    updated = True
                if updated:
                    user.updated_at = datetime.utcnow()
                    db.commit()
                    db.refresh(user)

            synced_user_ids.add(user.id)

            # Upsert OrganizationMember
            membership = db.query(OrganizationMember).filter(
                OrganizationMember.organization_id == organization_id,
                OrganizationMember.user_id == user.id
            ).first()

            if not membership:
                membership = OrganizationMember(
                    organization_id=organization_id,
                    user_id=user.id,
                    role=role or "member"
                )
                db.add(membership)
                try:
                    db.commit()
                except Exception:
                    db.rollback()
            elif membership.role != role:
                membership.role = role or "member"
                membership.updated_at = datetime.utcnow()
                db.commit()

        # 3. Prune Memberships Removed from Clerk
        if synced_user_ids:
            db.query(OrganizationMember).filter(
                OrganizationMember.organization_id == organization_id,
                ~OrganizationMember.user_id.in_(synced_user_ids)
            ).delete(synchronize_session=False)
            db.commit()

        logger.info(f"[CLERK SYNC] Completed sync for '{org.name}' ({organization_id}): {len(synced_user_ids)} members.")
        return {
            "organization_id": org.id,
            "organization_name": org.name,
            "slug": clerk_org.get("slug") if clerk_org else None,
            "members_synced_count": len(synced_user_ids)
        }

    def sync_user_organization_memberships(self, db: Session, user: User) -> List[OrganizationMember]:
        """
        Fetches all organizations the user belongs to from Clerk and syncs them into local tables.
        Returns the user's active memberships.
        """
        if not getattr(user, "clerk_user_id", None):
            return []

        try:
            res = self.client.users.get_organization_memberships(user_id=user.clerk_user_id)
            memberships_data = getattr(res, "data", []) or []
            created_memberships = []

            for item in memberships_data:
                org_info = getattr(item, "organization", None)
                if not org_info:
                    continue

                org_id = org_info.id
                org_name = org_info.name
                raw_role = getattr(item, "role", "member")
                role = raw_role.split(":", 1)[1] if ":" in str(raw_role) else str(raw_role)

                # Ensure organization exists
                org = db.query(Organization).filter(Organization.id == org_id).first()
                if not org:
                    org = Organization(id=org_id, name=org_name)
                    db.add(org)
                    try:
                        db.commit()
                        db.refresh(org)
                    except Exception:
                        db.rollback()
                        org = db.query(Organization).filter(Organization.id == org_id).first()
                elif org.name != org_name:
                    org.name = org_name
                    db.commit()

                # Ensure membership exists
                membership = db.query(OrganizationMember).filter(
                    OrganizationMember.organization_id == org_id,
                    OrganizationMember.user_id == user.id
                ).first()

                if not membership:
                    membership = OrganizationMember(
                        organization_id=org_id,
                        user_id=user.id,
                        role=role or "member"
                    )
                    db.add(membership)
                    try:
                        db.commit()
                        db.refresh(membership)
                    except Exception:
                        db.rollback()
                        membership = db.query(OrganizationMember).filter(
                            OrganizationMember.organization_id == org_id,
                            OrganizationMember.user_id == user.id
                        ).first()
                elif membership.role != role:
                    membership.role = role or "member"
                    db.commit()

                if membership:
                    created_memberships.append(membership)

            return created_memberships
        except Exception as exc:
            logger.warning(f"[CLERK SYNC] Failed to fetch organization memberships for user {user.clerk_user_id}: {exc}")
            return db.query(OrganizationMember).filter(OrganizationMember.user_id == user.id).all()

    def sync_all_organizations_and_members(self, db: Session) -> Dict[str, Any]:
        """
        Synchronizes all organizations and their members from Clerk into local tables.
        """
        try:
            res = self.client.organizations.list(limit=100)
            org_list = getattr(res, "data", []) or []
            results = []

            for org_item in org_list:
                sync_res = self.sync_organization_and_members(db, org_item.id)
                results.append(sync_res)

            return {
                "organizations_count": len(results),
                "total_memberships_synced": sum(r["members_synced_count"] for r in results),
                "organizations": results
            }
        except Exception as exc:
            logger.error(f"[CLERK SYNC ALL ERROR] Failed syncing all organizations: {exc}")
            raise


clerk_service = ClerkService()
