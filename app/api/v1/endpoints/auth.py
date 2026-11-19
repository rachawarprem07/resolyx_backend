import secrets
import time
from typing import Any, Dict
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.v1.endpoints.setup import check_is_setup_required
from app.core.security import create_access_token, hash_password, verify_password
from app.db.session import get_db
from app.models.company import Company
from app.models.email_account import EmailAccountModel
from app.models.user import User
from app.schemas.auth import (
    CustomerSignupRequest,
    LoginRequest,
    OtpResponse,
    SendOtpRequest,
    TokenResponse,
    VerifyOtpRequest,
)
from app.schemas.setup import SetupStatusResponse
from app.services.email_service import send_otp_email

router = APIRouter()

# In-memory OTP storage with 10-minute validity
OTP_CACHE: Dict[str, Dict[str, Any]] = {}


@router.get(
    "/setup-status",
    response_model=SetupStatusResponse,
    status_code=status.HTTP_200_OK,
    summary="Check Setup Status",
)
def get_auth_setup_status(db: Session = Depends(get_db)) -> SetupStatusResponse:
    """Returns whether first-time workspace setup is needed."""
    is_required, company_count, admin_count = check_is_setup_required(db)
    message = (
        "Initial workspace setup is required. Please create a company and super admin."
        if is_required
        else "Workspace has already been initialized."
    )
    return SetupStatusResponse(
        is_setup_required=is_required,
        company_count=company_count,
        admin_count=admin_count,
        message=message,
    )


@router.post(
    "/login",
    response_model=TokenResponse,
    status_code=status.HTTP_200_OK,
    summary="User Login",
    description="Authenticates a user against PostgreSQL database using Argon2 password verification.",
)
def login(
    credentials: LoginRequest,
    db: Session = Depends(get_db),
) -> TokenResponse:
    """Authenticates email and password against PostgreSQL."""
    clean_email = credentials.email.strip().lower()

    # Query user from PostgreSQL
    user = db.scalar(select(User).where(User.email.ilike(clean_email)))

    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )

    # Verify password with Argon2
    if not verify_password(credentials.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User account is inactive",
        )

    # Generate JWT access token
    access_token = create_access_token(subject=user.email)

    return TokenResponse(
        access_token=access_token,
        token_type="bearer",
        user=user,
    )


@router.post(
    "/customer-signup",
    response_model=TokenResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Customer Self Signup",
    description="Registers an external customer, hashes their password, and issues JWT access token.",
)
def customer_signup(
    payload: CustomerSignupRequest,
    db: Session = Depends(get_db),
) -> TokenResponse:
    """Registers a new customer user and returns authenticated JWT token."""
    clean_email = payload.email.strip().lower()
    clean_name = payload.full_name.strip()

    # Check if active account with this email exists
    existing = db.scalar(select(User).where(User.email.ilike(clean_email)))
    if existing and existing.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"An account with email '{clean_email}' already exists. Please sign in instead.",
        )

    company = db.scalar(select(Company).order_by(Company.id.asc()))
    company_id = company.id if company else None

    if existing:
        existing.full_name = clean_name
        existing.hashed_password = hash_password(payload.password)
        existing.role = "customer"
        existing.is_active = True
        existing.invite_token = None
        existing.invite_expires_at = None
        db.commit()
        db.refresh(existing)
        target_user = existing
    else:
        target_user = User(
            company_id=company_id,
            email=clean_email,
            full_name=clean_name,
            role="customer",
            department="Customer",
            hashed_password=hash_password(payload.password),
            is_active=True,
            is_superuser=False,
        )
        db.add(target_user)
        db.commit()
        db.refresh(target_user)

    access_token = create_access_token(subject=target_user.email)

    return TokenResponse(
        access_token=access_token,
        token_type="bearer",
        user=target_user,
    )


@router.post(
    "/send-otp",
    response_model=OtpResponse,
    status_code=status.HTTP_200_OK,
    summary="Send Email Verification OTP",
    description="Generates a real 6-digit OTP and emails it via the configured default outgoing SMTP account.",
)
def send_otp(
    payload: SendOtpRequest,
    db: Session = Depends(get_db),
) -> OtpResponse:
    """Sends a verification code to customer email address via default outgoing SMTP account."""
    clean_email = payload.email.strip().lower()
    clean_name = (payload.full_name or "Customer").strip()

    # Generate 6-digit OTP code
    otp_code = f"{secrets.randbelow(900000) + 100000}"
    OTP_CACHE[clean_email] = {
        "code": otp_code,
        "expires_at": time.time() + 600,  # 10 minutes expiry
        "full_name": clean_name,
        "verified": False,
    }

    # Fetch company workspace name
    company = db.scalar(select(Company).order_by(Company.id.asc()))
    company_name = company.name if company else "Resolyx"

    # Fetch default outgoing SMTP email account
    smtp_account = db.scalar(
        select(EmailAccountModel)
        .where(EmailAccountModel.enable_outgoing == True)
        .order_by(EmailAccountModel.default_outgoing.desc(), EmailAccountModel.id.asc())
    )

    if not smtp_account or not smtp_account.password:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="No outgoing email account configured in Settings > Email Accounts.",
        )

    sent, detail = send_otp_email(
        to_email=clean_email,
        recipient_name=clean_name,
        otp_code=otp_code,
        company_name=company_name,
        smtp_host=smtp_account.smtp_host,
        smtp_port=smtp_account.smtp_port,
        smtp_user=smtp_account.email,
        smtp_password=smtp_account.password,
    )

    if not sent:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=detail or "Failed to deliver verification email.",
        )

    return OtpResponse(
        success=True,
        message=f"Verification code sent to {clean_email}.",
        email_sent=True,
        email_detail=detail,
    )


@router.post(
    "/verify-otp",
    response_model=OtpResponse,
    status_code=status.HTTP_200_OK,
    summary="Verify Email OTP",
)
def verify_otp(
    payload: VerifyOtpRequest,
) -> OtpResponse:
    """Verifies the customer OTP code."""
    clean_email = payload.email.strip().lower()
    clean_code = payload.otp_code.strip()

    entry = OTP_CACHE.get(clean_email)
    if not entry:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No verification code found for this email. Please request a new code.",
        )

    if time.time() > entry["expires_at"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Verification code has expired. Please request a new code.",
        )

    if entry["code"] != clean_code:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid verification code. Please check your email and try again.",
        )

    entry["verified"] = True
    return OtpResponse(
        success=True,
        message="Email successfully verified.",
        email_sent=False,
    )



