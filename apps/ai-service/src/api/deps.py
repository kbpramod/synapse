import os
import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from src.db.session import get_db
from src.models.user import User

security = HTTPBearer(auto_error=False)


def format_pem_key(key_str: str) -> str:
    """Format PEM key string if lines are flattened in environment variable."""
    if not key_str:
        return ""
    key_str = key_str.strip()
    if "-----BEGIN PUBLIC KEY-----" in key_str:
        # Restore newlines if passed as single line with escaped or whitespace newlines
        key_str = key_str.replace("\\n", "\n")
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
    and return/create local User entity.
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
        user = User(
            clerk_user_id=clerk_user_id,
            email=payload.get("email") or payload.get("primary_email_address")
        )
        db.add(user)
        db.commit()
        db.refresh(user)

    return user


# Alias require_auth dependency
require_auth = get_current_user
