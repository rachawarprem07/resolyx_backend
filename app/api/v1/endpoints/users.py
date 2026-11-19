import secrets
from datetime import datetime, timedelta, timezone
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import create_access_token, hash_password
from app.db.session import get_db
from app.models.company import Company
from app.models.email_account import EmailAccountModel
from app.models.user import User
from app.schemas.auth import UserResponse
from app.schemas.user import (
    AcceptInviteRequest,
    AcceptInviteResponse,
    InviteValidationResponse,
    UserDetailResponse,
    UserInviteRequest,
    UserInviteResponse,
    UserUpdateRequest,
)
from app.services.email_service import send_invitation_email

router = APIRouter()

BASE_APP_URL = "http://127.0.0.1:5173"


def get_role_redirect_url(role: str) -> str:
    """Returns the destination portal URL based on user role."""
    normalized = role.lower().strip()
    if normalized == "customer":
        return "/customer"
    elif normalized in ("admin", "super_admin", "administrator"):
        return "/admin"
    elif normalized in ("team_lead", "lead", "supervisor", "support_agent", "agent"):
        return "/agent"
    return "/admin"


@router.get(
    "",
    response_model=List[UserDetailResponse],
    status_code=status.HTTP_200_OK,
    summary="List All Users",
)
def list_users(
    db: Session = Depends(get_db),
) -> List[UserDetailResponse]:
    """Retrieves all users across the company workspace."""
    users = db.scalars(select(User).order_by(User.created_at.desc())).all()
    results: List[UserDetailResponse] = []

    for u in users:
        is_invited = bool(u.invite_token and not u.is_active)
        status_label = "invited" if is_invited else ("active" if u.is_active else "inactive")
        invite_url = f"{BASE_APP_URL}/accept-invite?token={u.invite_token}" if u.invite_token else None

        results.append(
            UserDetailResponse(
                id=u.id,
                email=u.email,
                full_name=u.full_name,
                role=u.role,
                department=u.department,
                is_active=u.is_active,
                status=status_label,
                created_at=u.created_at,
                invite_token=u.invite_token,
                invite_url=invite_url,
            )
        )

    return results


@router.post(
    "/invite",
    response_model=UserInviteResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Invite New Agent / Team Member",
)
def invite_user(
    payload: UserInviteRequest,
    db: Session = Depends(get_db),
) -> UserInviteResponse:
    """Generates an invitation token, creates or updates the pending user, and triggers email."""
    clean_email = payload.email.strip().lower()
    clean_name = payload.full_name.strip()
    r_lower = payload.role.strip().lower()
    if r_lower in ("admin", "super_admin", "administrator"):
        clean_role = "admin"
    elif r_lower in ("team_lead", "team lead", "lead", "supervisor"):
        clean_role = "team_lead"
    elif r_lower == "customer":
        clean_role = "customer"
    else:
        clean_role = "support_agent"
    clean_dept = payload.department.strip() if payload.department else None

    # Get active company name
    company = db.scalar(select(Company).order_by(Company.id.asc()))
    company_name = company.name if company else "Resolva"
    company_id = company.id if company else None

    # Check if user with this email already exists
    existing = db.scalar(select(User).where(User.email.ilike(clean_email)))
    if existing:
        if existing.is_active:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"An active account with email '{clean_email}' already exists.",
            )
        # Resend invite to existing pending user
        token = secrets.token_urlsafe(32)
        existing.invite_token = token
        existing.invite_expires_at = datetime.now(timezone.utc) + timedelta(days=7)
        existing.full_name = clean_name
        existing.role = clean_role
        existing.department = clean_dept
        db.commit()
        db.refresh(existing)
        target_user = existing
    else:
        # Create new pending user
        token = secrets.token_urlsafe(32)
        # Random placeholder hash so column constraint is satisfied until user sets password
        dummy_hash = hash_password(secrets.token_hex(16))
        target_user = User(
            company_id=company_id,
            email=clean_email,
            full_name=clean_name,
            role=clean_role,
            department=clean_dept,
            hashed_password=dummy_hash,
            is_active=False,
            is_superuser=False,
            invite_token=token,
            invite_expires_at=datetime.now(timezone.utc) + timedelta(days=7),
        )
        db.add(target_user)
        db.commit()
        db.refresh(target_user)

    invite_url = f"{BASE_APP_URL}/accept-invite?token={target_user.invite_token}"

    # Look up configured outgoing SMTP email account
    smtp_account = db.scalar(
        select(EmailAccountModel)
        .where(EmailAccountModel.enable_outgoing == True)
        .order_by(EmailAccountModel.default_outgoing.desc(), EmailAccountModel.id.asc())
    )

    if smtp_account and smtp_account.password:
        email_sent, email_detail = send_invitation_email(
            to_email=target_user.email,
            recipient_name=clean_name,
            role=clean_role,
            company_name=company_name,
            invite_url=invite_url,
            smtp_host=smtp_account.smtp_host,
            smtp_port=smtp_account.smtp_port,
            smtp_user=smtp_account.email,
            smtp_password=smtp_account.password,
        )
    elif smtp_account and not smtp_account.password:
        email_sent = False
        email_detail = f"Account '{smtp_account.email}' is missing a password. Please edit it in Settings > Email Accounts and enter your 16-character Google App Password."
    else:
        email_sent = False
        email_detail = "No outgoing email account configured in Settings > Email Accounts."

    return UserInviteResponse(
        id=target_user.id,
        email=target_user.email,
        full_name=target_user.full_name,
        role=target_user.role,
        department=target_user.department,
        status="invited",
        invite_token=target_user.invite_token,
        invite_url=invite_url,
        email_sent=email_sent,
        email_detail=email_detail,
        message=f"Invitation generated for {clean_email}.",
    )


@router.get(
    "/invite/{token}",
    response_model=InviteValidationResponse,
    status_code=status.HTTP_200_OK,
    summary="Validate Invitation Token",
)
def validate_invite_token(
    token: str,
    db: Session = Depends(get_db),
) -> InviteValidationResponse:
    """Validates an invitation link and returns user & company details for password setup."""
    user = db.scalar(select(User).where(User.invite_token == token))

    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Invitation link is invalid or has already been used.",
        )

    # Check expiration
    if user.invite_expires_at and user.invite_expires_at < datetime.now(timezone.utc):
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="This invitation link has expired. Please ask your administrator to resend it.",
        )

    company = db.scalar(select(Company).where(Company.id == user.company_id)) if user.company_id else None
    company_name = company.name if company else "Resolva"

    return InviteValidationResponse(
        email=user.email,
        full_name=user.full_name,
        role=user.role,
        company_name=company_name,
        is_valid=True,
    )


@router.post(
    "/accept-invite",
    response_model=AcceptInviteResponse,
    status_code=status.HTTP_200_OK,
    summary="Set Password & Accept Invitation",
)
def accept_invite(
    payload: AcceptInviteRequest,
    db: Session = Depends(get_db),
) -> AcceptInviteResponse:
    """Sets the user password, marks account active, invalidates token, and logs user in."""
    user = db.scalar(select(User).where(User.invite_token == payload.token))

    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Invitation token is invalid or has already been used.",
        )

    if user.invite_expires_at and user.invite_expires_at < datetime.now(timezone.utc):
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="Invitation token has expired.",
        )

    # Hash new password with bcrypt
    user.hashed_password = hash_password(payload.password)
    if payload.full_name and payload.full_name.strip():
        user.full_name = payload.full_name.strip()

    # Activate account & invalidate invitation token
    user.is_active = True
    user.invite_token = None
    user.invite_expires_at = None

    db.commit()
    db.refresh(user)

    # Issue JWT access token
    access_token = create_access_token(subject=user.email)
    redirect_url = get_role_redirect_url(user.role)

    return AcceptInviteResponse(
        access_token=access_token,
        token_type="bearer",
        user=user,
        redirect_url=redirect_url,
    )


@router.post(
    "/{user_id}/resend-invite",
    response_model=UserInviteResponse,
    status_code=status.HTTP_200_OK,
    summary="Resend Invitation Link",
)
def resend_invite(
    user_id: int,
    db: Session = Depends(get_db),
) -> UserInviteResponse:
    """Regenerates a fresh token and invite link for a pending user."""
    user = db.scalar(select(User).where(User.id == user_id))
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found.")

    token = secrets.token_urlsafe(32)
    user.invite_token = token
    user.invite_expires_at = datetime.now(timezone.utc) + timedelta(days=7)
    db.commit()
    db.refresh(user)

    company = db.scalar(select(Company).where(Company.id == user.company_id)) if user.company_id else None
    company_name = company.name if company else "Resolva"
    invite_url = f"{BASE_APP_URL}/accept-invite?token={token}"

    smtp_account = db.scalar(
        select(EmailAccountModel)
        .where(EmailAccountModel.enable_outgoing == True)
        .order_by(EmailAccountModel.default_outgoing.desc(), EmailAccountModel.id.asc())
    )

    if smtp_account and smtp_account.password:
        email_sent, email_detail = send_invitation_email(
            to_email=user.email,
            recipient_name=user.full_name or "Team Member",
            role=user.role,
            company_name=company_name,
            invite_url=invite_url,
            smtp_host=smtp_account.smtp_host,
            smtp_port=smtp_account.smtp_port,
            smtp_user=smtp_account.email,
            smtp_password=smtp_account.password,
        )
    else:
        email_sent = False
        email_detail = "No outgoing email account configured in Settings > Email Accounts."

    return UserInviteResponse(
        id=user.id,
        email=user.email,
        full_name=user.full_name,
        role=user.role,
        department=user.department,
        status="invited",
        invite_token=token,
        invite_url=invite_url,
        email_sent=email_sent,
        email_detail=email_detail,
        message=f"Fresh invitation sent to {user.email}.",
    )


@router.patch(
    "/{user_id}",
    response_model=UserDetailResponse,
    status_code=status.HTTP_200_OK,
    summary="Update User Profile & Role",
)
def update_user(
    user_id: int,
    payload: UserUpdateRequest,
    db: Session = Depends(get_db),
) -> UserDetailResponse:
    """Updates user information, such as department, full name, role, and active status."""
    user = db.scalar(select(User).where(User.id == user_id))
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found.")

    if payload.full_name is not None:
        user.full_name = payload.full_name.strip()
    if payload.department is not None:
        user.department = payload.department.strip()
    if payload.role is not None:
        r_lower = payload.role.strip().lower()
        if r_lower in ("admin", "super_admin", "administrator"):
            user.role = "admin"
        elif r_lower in ("team_lead", "team lead", "lead", "supervisor"):
            user.role = "team_lead"
        elif r_lower == "customer":
            user.role = "customer"
        else:
            user.role = "support_agent"
    if payload.is_active is not None:
        user.is_active = payload.is_active
    elif payload.status is not None:
        if payload.status == "inactive":
            user.is_active = False
        elif payload.status == "active":
            user.is_active = True

    db.commit()
    db.refresh(user)

    is_invited = bool(user.invite_token and not user.is_active)
    status_label = "invited" if is_invited else ("active" if user.is_active else "inactive")
    invite_url = f"{BASE_APP_URL}/accept-invite?token={user.invite_token}" if user.invite_token else None

    return UserDetailResponse(
        id=user.id,
        email=user.email,
        full_name=user.full_name,
        role=user.role,
        department=user.department,
        is_active=user.is_active,
        status=status_label,
        invite_token=user.invite_token,
        invite_url=invite_url,
        created_at=user.created_at,
    )


@router.delete(
    "/{user_id}",
    status_code=status.HTTP_200_OK,
    summary="Delete User or Revoke Invitation",
)
def delete_user(
    user_id: int,
    db: Session = Depends(get_db),
):
    """Deletes a user or revokes their pending invitation."""
    user = db.scalar(select(User).where(User.id == user_id))
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found.")

    db.delete(user)
    db.commit()
    return {"message": f"User {user.email} removed successfully."}

