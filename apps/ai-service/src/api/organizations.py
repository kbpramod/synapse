import logging
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, status

from sqlalchemy.orm import Session

try:
    from api.deps import require_auth
    from db.session import get_db
    from models.user import User
    from schemas.organization import (
        CurrentOrganizationResponse,
        OrganizationMemberResponse,
        OrganizationMembersListResponse,
        InviteMemberRequest,
        InviteMemberResponse
    )
    from services.clerk_service import clerk_service
except ImportError:
    from src.api.deps import require_auth
    from src.db.session import get_db
    from src.models.user import User
    from src.schemas.organization import (
        CurrentOrganizationResponse,
        OrganizationMemberResponse,
        OrganizationMembersListResponse,
        InviteMemberRequest,
        InviteMemberResponse
    )
    from src.services.clerk_service import clerk_service

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Organizations"])


@router.get(
    "/current",
    response_model=Optional[CurrentOrganizationResponse],
    summary="Get current active organization details"
)
async def get_current_organization(user: User = Depends(require_auth)):
    """
    Returns the active organization metadata and user's role for the current session.
    Returns null if no active organization is selected.
    """
    org_id = getattr(user, "organization_id", None)
    if not org_id:
        return None

    # 1. Attempt to fetch fresh metadata from Clerk
    clerk_org = clerk_service.get_organization(org_id)
    org_name = (
        clerk_org.get("name") if clerk_org else None
    ) or (
        user.current_organization.name if getattr(user, "current_organization", None) else None
    ) or f"Organization {org_id}"

    org_slug = (
        clerk_org.get("slug") if clerk_org else None
    ) or getattr(user, "organization_slug", None)

    role = getattr(user, "organization_role", "member") or "member"

    return CurrentOrganizationResponse(
        id=org_id,
        name=org_name,
        slug=org_slug,
        role=role
    )


@router.get(
    "/current/members",
    response_model=OrganizationMembersListResponse,
    summary="List members in current organization from Clerk"
)
async def get_current_organization_members(
    user: User = Depends(require_auth),
    db: Session = Depends(get_db)
):
    """
    Fetches the organization membership roster directly from Clerk Backend API,
    synchronizes records into local DB tables, and returns the member roster.
    Returns empty list if no active organization is selected.
    """
    org_id = getattr(user, "organization_id", None)
    if not org_id:
        return OrganizationMembersListResponse(members=[])

    try:
        # Sync Clerk organization and members into local DB
        try:
            clerk_service.sync_organization_and_members(db, org_id)
        except Exception as sync_err:
            logger.warning(f"[API ORG MEMBERS] Background DB sync warning for {org_id}: {sync_err}")

        raw_members = clerk_service.list_organization_members(org_id)
        members = [
            OrganizationMemberResponse(
                user_id=m["user_id"],
                name=m.get("name"),
                email=m.get("email"),
                role=m.get("role", "org:member"),
                image_url=m.get("image_url")
            )
            for m in raw_members
        ]
        return OrganizationMembersListResponse(members=members)
    except Exception as exc:
        logger.error(f"[API ORG MEMBERS ERROR] Failed to fetch members for {org_id}: {exc}")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to retrieve organization members from Clerk: {str(exc)}"
        )


@router.post(
    "/current/sync",
    summary="Sync current organization and members from Clerk into local database"
)
async def sync_current_organization_endpoint(
    user: User = Depends(require_auth),
    db: Session = Depends(get_db)
):
    """
    Explicitly synchronizes current active organization and all members from Clerk into
    local PostgreSQL `organizations`, `organization_members`, and `users` tables.
    """
    org_id = getattr(user, "organization_id", None)
    if not org_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Active organization is required to perform sync."
        )

    try:
        result = clerk_service.sync_organization_and_members(db, org_id)
        return {
            "success": True,
            "message": f"Successfully synced organization '{result['organization_name']}' and {result['members_synced_count']} members.",
            "data": result
        }
    except Exception as exc:
        logger.error(f"[API ORG SYNC ERROR] Failed syncing organization {org_id}: {exc}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to sync organization with Clerk: {str(exc)}"
        )


@router.post(
    "/sync-all",
    summary="Sync all organizations and members from Clerk into local database"
)
async def sync_all_organizations_endpoint(
    user: User = Depends(require_auth),
    db: Session = Depends(get_db)
):
    """
    Synchronizes all organizations and memberships across Clerk into local PostgreSQL tables.
    Requires admin privileges.
    """
    user_role = str(getattr(user, "organization_role", "member") or "member").lower()
    if user_role not in ["admin", "org:admin", "owner", "org:owner"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin privileges required to trigger global organization synchronization."
        )

    try:
        result = clerk_service.sync_all_organizations_and_members(db)
        return {
            "success": True,
            "message": f"Successfully synced {result['organizations_count']} organizations and {result['total_memberships_synced']} total memberships.",
            "data": result
        }
    except Exception as exc:
        logger.error(f"[API SYNC ALL ERROR] Failed global Clerk sync: {exc}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to perform global organization sync: {str(exc)}"
        )


@router.post(
    "/current/members/invite",
    response_model=InviteMemberResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Invite a new member to current organization"
)
async def invite_organization_member(
    payload: InviteMemberRequest,
    user: User = Depends(require_auth)
):
    """
    Sends an invitation to join the active organization via Clerk.
    Requires Admin role ('admin' or 'org:admin').
    """
    org_id = getattr(user, "organization_id", None)
    if not org_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Active organization is required."
        )

    user_role = str(getattr(user, "organization_role", "member") or "member").lower()
    if user_role not in ["admin", "org:admin", "owner", "org:owner"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin privileges required to invite members."
        )

    try:
        invitation = clerk_service.invite_member(
            organization_id=org_id,
            email_address=payload.email,
            role=payload.role,
            inviter_user_id=getattr(user, "clerk_user_id", None)
        )
        return InviteMemberResponse(
            id=invitation["id"],
            email=invitation["email"],
            role=invitation["role"],
            status=invitation.get("status", "pending")
        )
    except Exception as exc:
        logger.error(f"[API ORG INVITE ERROR] Failed to invite {payload.email} to {org_id}: {exc}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to create Clerk invitation: {str(exc)}"
        )
