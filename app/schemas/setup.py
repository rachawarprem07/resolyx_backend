from typing import Optional
from pydantic import BaseModel, ConfigDict, Field

from app.schemas.auth import UserResponse


class SetupStatusResponse(BaseModel):
    """Schema indicating whether initial workspace onboarding is required."""

    is_setup_required: bool
    company_count: int
    admin_count: int
    message: str


class CompanySetupRequest(BaseModel):
    """Payload for creating company workspace and super administrator."""

    company_name: str = Field(..., min_length=2, max_length=255)
    currency: str = Field("USD", min_length=1, max_length=10)
    timezone: str = Field("UTC", min_length=1, max_length=50)
    full_name: str = Field(..., min_length=2, max_length=255)
    email: str = Field(..., min_length=3, max_length=255)
    password: str = Field(..., min_length=6, max_length=128)


class CompanyResponse(BaseModel):
    """Public details of the configured company."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    currency: str
    timezone: str


class UpdateCompanyRequest(BaseModel):
    """Payload for updating company details."""

    name: Optional[str] = Field(None, min_length=2, max_length=255)
    currency: Optional[str] = Field(None, min_length=1, max_length=10)
    timezone: Optional[str] = Field(None, min_length=1, max_length=50)


class SetupResponse(BaseModel):
    """Response returned upon successful workspace and super admin creation."""

    access_token: str
    token_type: str = "bearer"
    user: UserResponse
    company: CompanyResponse
    message: str

