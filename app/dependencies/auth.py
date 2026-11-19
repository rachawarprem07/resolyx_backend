"""Authentication & authorization dependencies.

Tokens are issued by ``/auth/login`` with the user's email as the JWT subject.
These dependencies verify the bearer token, load the user, and enforce roles.
"""

from typing import Optional

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.exceptions import ForbiddenException, UnauthorizedException
from app.core.security import decode_access_token
from app.db.session import get_db
from app.models.user import User

ADMIN_ROLES = frozenset({"admin", "super_admin", "administrator"})
STAFF_ROLES = frozenset(
    {"admin", "super_admin", "administrator", "team_lead", "support_agent", "agent"}
)

_bearer = HTTPBearer(auto_error=False)


def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer),
    db: Session = Depends(get_db),
) -> User:
    """Resolves the authenticated user from the ``Authorization: Bearer`` header."""
    if credentials is None or not credentials.credentials:
        raise UnauthorizedException("Authentication required.")

    payload = decode_access_token(credentials.credentials)
    subject = payload.get("sub") if payload else None
    if not subject:
        raise UnauthorizedException("Invalid or expired access token.")

    user = db.scalar(select(User).where(User.email.ilike(str(subject).strip())))
    if user is None or not user.is_active:
        raise UnauthorizedException("User account not found or inactive.")
    return user


def is_admin(user: User) -> bool:
    return bool(user.is_superuser) or (user.role or "").lower() in ADMIN_ROLES


def is_staff(user: User) -> bool:
    return is_admin(user) or (user.role or "").lower() in STAFF_ROLES


def require_admin(user: User = Depends(get_current_user)) -> User:
    """Allows only helpdesk administrators."""
    if not is_admin(user):
        raise ForbiddenException("Administrator privileges are required.")
    return user


def require_staff(user: User = Depends(get_current_user)) -> User:
    """Allows administrators, team leads and support agents."""
    if not is_staff(user):
        raise ForbiddenException("Support staff privileges are required.")
    return user
