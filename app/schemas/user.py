from datetime import datetime
from typing import Optional
from pydantic import BaseModel, ConfigDict, Field

from app.schemas.auth import UserResponse


class UserInviteRequest(BaseModel):
    """Payload to invite a new team member/agent."""

    email: str = Field(..., min_length=3, max_length=255)
    full_name: str = Field(..., min_length=2, max_length=255)
    role: str = Field("agent", min_length=2, max_length=50)
    department: Optional[str] = Field(None, max_length=100)


class UserInviteResponse(BaseModel):
    """Result of an invitation."""

    id: int
    email: str
    full_name: Optional[str]
    role: str
    department: Optional[str]
    status: str
    invite_token: str
    invite_url: str
    email_sent: bool
    email_detail: Optional[str] = None
    message: str


class InviteValidationResponse(BaseModel):
    """Public details returned when an invited agent visits their setup link."""

    email: str
    full_name: Optional[str]
    role: str
    company_name: str
    is_valid: bool


class AcceptInviteRequest(BaseModel):
    """Payload when setting up password to accept an invitation."""

    token: str = Field(..., min_length=10)
    password: str = Field(..., min_length=6, max_length=128)
    full_name: Optional[str] = Field(None, max_length=255)


class AcceptInviteResponse(BaseModel):
    """Response returned upon password setup."""

    access_token: str
    token_type: str = "bearer"
    user: UserResponse
    redirect_url: str


class UserDetailResponse(BaseModel):
    """Full user profile for users list."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    email: str
    full_name: Optional[str] = None
    role: str
    department: Optional[str] = None
    is_active: bool
    status: str
    created_at: datetime
    invite_token: Optional[str] = None
    invite_url: Optional[str] = None


class UserUpdateRequest(BaseModel):
    """Payload to update an existing user's profile and role."""

    full_name: Optional[str] = Field(None, max_length=255)
    role: Optional[str] = Field(None, max_length=50)
    department: Optional[str] = Field(None, max_length=100)
    status: Optional[str] = Field(None, max_length=20)
    is_active: Optional[bool] = None


