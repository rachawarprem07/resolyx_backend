from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.security import create_access_token, hash_password
from app.db.session import get_db
from app.models.company import Company
from app.models.user import User
from app.schemas.auth import UserResponse
from app.schemas.setup import (
    CompanyResponse,
    CompanySetupRequest,
    UpdateCompanyRequest,
    SetupResponse,
    SetupStatusResponse,
)

router = APIRouter()


def check_is_setup_required(db: Session) -> tuple[bool, int, int]:
    """Check if first-time onboarding is needed (no company or no admin exists)."""
    admin_count = (
        db.scalar(
            select(func.count(User.id)).where(User.role.in_(["super_admin", "admin"]))
        )
        or 0
    )
    company_count = db.scalar(select(func.count(Company.id))) or 0
    is_required = admin_count == 0 or company_count == 0
    return is_required, company_count, admin_count


@router.get(
    "/status",
    response_model=SetupStatusResponse,
    status_code=status.HTTP_200_OK,
    summary="Check Setup Status",
    description="Returns whether first-time workspace & super admin setup is required.",
)
def get_setup_status(db: Session = Depends(get_db)) -> SetupStatusResponse:
    """Returns true if first-time company & super admin onboarding is needed."""
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
    "",
    response_model=SetupResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Initial Workspace Setup",
    description="Creates company workspace and initial Super Admin on first run.",
)
def setup_workspace(
    payload: CompanySetupRequest,
    db: Session = Depends(get_db),
) -> SetupResponse:
    """Creates initial company and super administrator."""
    is_required, _, _ = check_is_setup_required(db)

    if not is_required:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Setup has already been completed. Please log in with existing credentials.",
        )

    clean_email = str(payload.email).strip().lower()

    # Check if a user with this email already exists
    existing_user = db.scalar(select(User).where(User.email.ilike(clean_email)))
    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"A user with email '{clean_email}' already exists.",
        )

    # 1. Create Company
    company = Company(
        name=payload.company_name.strip(),
        currency=payload.currency.strip().upper(),
        timezone=payload.timezone.strip(),
    )
    db.add(company)
    db.flush()

    # 2. Create Super Admin User
    hashed_pwd = hash_password(payload.password)
    user = User(
        email=clean_email,
        hashed_password=hashed_pwd,
        full_name=payload.full_name.strip(),
        role="super_admin",
        is_active=True,
        is_superuser=True,
        company_id=company.id,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    db.refresh(company)

    # 3. Generate JWT access token for immediate seamless login
    access_token = create_access_token(subject=user.email)

    return SetupResponse(
        access_token=access_token,
        token_type="bearer",
        user=UserResponse.model_validate(user),
        company=CompanyResponse.model_validate(company),
        message="Workspace and Super Admin successfully initialized.",
    )


@router.get(
    "/company",
    response_model=CompanyResponse,
    status_code=status.HTTP_200_OK,
    summary="Get Company Details",
    description="Returns public company workspace details.",
)
def get_company(db: Session = Depends(get_db)) -> CompanyResponse:
    """Returns the primary company workspace details."""
    company = db.scalar(select(Company).order_by(Company.id.desc()).limit(1))
    if not company:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Company workspace not initialized yet.",
        )
    return CompanyResponse.model_validate(company)


@router.patch(
    "/company",
    response_model=CompanyResponse,
    status_code=status.HTTP_200_OK,
    summary="Update Company Details",
    description="Updates the company name, currency, or timezone.",
)
def update_company(
    payload: UpdateCompanyRequest,
    db: Session = Depends(get_db),
) -> CompanyResponse:
    """Updates the company workspace name or configuration."""
    company = db.scalar(select(Company).order_by(Company.id.desc()).limit(1))
    if not company:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Company workspace not found.",
        )
    if payload.name is not None and payload.name.strip():
        company.name = payload.name.strip()
    if payload.currency is not None and payload.currency.strip():
        company.currency = payload.currency.strip().upper()
    if payload.timezone is not None and payload.timezone.strip():
        company.timezone = payload.timezone.strip()

    db.commit()
    db.refresh(company)
    return CompanyResponse.model_validate(company)


