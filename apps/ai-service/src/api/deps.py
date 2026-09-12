import os
import logging
import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

try:
    from src.db.session import get_db
    from models.user import User
    from models.organization import Organization
    from models.organization_member import OrganizationMember
except ImportError:
    from db.session import get_db
    from models.user import User
    from models.organization import Organization
    from models.organization_member import OrganizationMember

logger = logging.getLogger(__name__)
security = HTTPBearer(auto_error=False)


def format_pem_key(key_str: str) -> str:
    """Format PEM key string if lines are flattened or unformatted in environment variable."""
    if not key_str:
        return ""
    key_str = key_str.strip().strip('"\'')
    if "\\n" in key_str:
        key_str = key_str.replace("\\n", "\n")
    if "-----BEGIN PUBLIC KEY-----" not in key_str:
        # Wrap raw base64 key
        key_str = f"-----BEGIN PUBLIC KEY-----\n{key_str}\n-----END PUBLIC KEY-----"
    return key_str


def decode_clerk_token(token: str) -> dict:
    """Verify and decode Clerk JWT session token."""
    clerk_jwt_key = format_pem_key(os.getenv("CLERK_JWT_KEY", ""))

    if clerk_jwt_key:
        try:
            payload = jwt.decode(
                token,
                clerk_jwt_key,
                algorithms=["RS256"],
                options={"verify_aud": False}
            )
            return payload
        except jwt.PyJWTError as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=f"Invalid or expired session token: {str(exc)}",
                headers={"WWW-Authenticate": "Bearer"},
            )

    # Fallback to unverified decode if CLERK_JWT_KEY is not set (e.g. initial dev testing)
    try:
        payload = jwt.decode(token, options={"verify_signature": False})
        return payload
    except jwt.PyJWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Malformed session token",
            headers={"WWW-Authenticate": "Bearer"},
        )


async def get_current_user(
    request: Request,
    auth_credentials: HTTPAuthorizationCredentials | None = Depends(security),
    db: Session = Depends(get_db)
) -> User:
    """
    FastAPI dependency to extract Clerk token, verify authentication,
    ensure and enforce active organization membership, and auto-provision records.
    """
    token = None

    # 1. Extract token from Authorization header
    if auth_credentials:
        token = auth_credentials.credentials
    
    # 2. Fallback to __session cookie if header is absent
    if not token:
        token = request.cookies.get("__session")

    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication token missing",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # 3. Decode & verify JWT
    payload = decode_clerk_token(token)
    clerk_user_id = payload.get("sub")

    if not clerk_user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token payload: missing sub/user_id",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # 4. Fetch or Auto-Provision User in Tzylo database
    user = db.query(User).filter(User.clerk_user_id == clerk_user_id).first()

    if not user:
        name = payload.get("name")
        if not name:
            fname = payload.get("first_name", "") or ""
            lname = payload.get("last_name", "") or ""
            name = f"{fname} {lname}".strip() or None

        user = User(
            clerk_user_id=clerk_user_id,
            email=payload.get("email") or payload.get("primary_email_address"),
            name=name
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

    # 5. Extract and Enforce Active Organization (Required for all members)
    clerk_org_id = (
        payload.get("org_id")
        or request.headers.get("x-organization-id")
        or request.headers.get("x-clerk-org-id")
    )

    # Fallback to existing membership in DB if token claim isn't present
    if not clerk_org_id:
        existing_membership = db.query(OrganizationMember).filter(
            OrganizationMember.user_id == user.id
        ).first()
        if existing_membership:
            clerk_org_id = existing_membership.organization_id

    # Strictly require organization
    if not clerk_org_id:
        logger.warning(f"[AUTH] User '{clerk_user_id}' attempted request without active organization.")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Organization is required for all members. Please select or join an active organization.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # 6. Auto-provision Organization if not already in DB
    org = db.query(Organization).filter(Organization.id == clerk_org_id).first()
    if not org:
        org_name = (
            payload.get("org_name")
            or payload.get("org_slug")
            or request.headers.get("x-organization-name")
            or f"Organization {clerk_org_id}"
        )
        org = Organization(
            id=clerk_org_id,
            name=org_name
        )
        db.add(org)
        try:
            db.commit()
            db.refresh(org)
        except Exception:
            db.rollback()
            org = db.query(Organization).filter(Organization.id == clerk_org_id).first()

    # 7. Auto-provision / Ensure OrganizationMember relationship
    membership = db.query(OrganizationMember).filter(
        OrganizationMember.organization_id == clerk_org_id,
        OrganizationMember.user_id == user.id
    ).first()

    raw_role = payload.get("org_role", "member")
    role = raw_role.split(":", 1)[1] if ":" in str(raw_role) else str(raw_role)

    if not membership:
        membership = OrganizationMember(
            organization_id=clerk_org_id,
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
                OrganizationMember.organization_id == clerk_org_id,
                OrganizationMember.user_id == user.id
            ).first()

    # Attach organization context directly onto user instance
    user.organization_id = clerk_org_id
    user.current_organization = org
    user.organization_role = getattr(membership, "role", "member")

    return user


async def get_optional_user(
    request: Request,
    auth_credentials: HTTPAuthorizationCredentials | None = Depends(security),
    db: Session = Depends(get_db)
) -> User | None:
    """FastAPI dependency to optionally authenticate user if token is present."""
    token = None
    if auth_credentials:
        token = auth_credentials.credentials
    if not token:
        token = request.cookies.get("__session")
    if not token:
        return None

    try:
        return await get_current_user(request=request, auth_credentials=auth_credentials, db=db)
    except HTTPException:
        # For optional auth endpoints, invalid/missing org falls back gracefully
        try:
            payload = decode_clerk_token(token)
            clerk_user_id = payload.get("sub")
            if not clerk_user_id:
                return None
            return db.query(User).filter(User.clerk_user_id == clerk_user_id).first()
        except Exception:
            return None


async def get_current_organization(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
) -> Organization:
    """Dependency that returns the verified active Organization for the authenticated user."""
    org = getattr(user, "current_organization", None)
    if not org and getattr(user, "organization_id", None):
        org = db.query(Organization).filter(Organization.id == user.organization_id).first()
    if not org:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Active organization not found."
        )
    return org


# Dependency aliases
require_auth = get_current_user
optional_auth = get_optional_user
require_org = get_current_organization
