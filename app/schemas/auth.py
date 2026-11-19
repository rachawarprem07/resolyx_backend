from typing import Optional
from pydantic import BaseModel, ConfigDict


class LoginRequest(BaseModel):
    """Schema for user authentication request."""

    email: str
    password: str


class CustomerSignupRequest(BaseModel):
    """Schema for external customer self-registration."""

    email: str
    password: str
    full_name: str
    company_name: Optional[str] = None
    phone: Optional[str] = None


class SendOtpRequest(BaseModel):
    """Schema to request an OTP verification code sent to customer email."""

    email: str
    full_name: Optional[str] = None


class VerifyOtpRequest(BaseModel):
    """Schema to verify an email OTP code."""

    email: str
    otp_code: str


class OtpResponse(BaseModel):
    """Schema for OTP send/verify status."""

    success: bool
    message: str
    email_sent: bool
    email_detail: Optional[str] = None


class UserResponse(BaseModel):
    """Schema for public user profile response."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    email: str
    role: str
    full_name: Optional[str] = None
    is_active: bool
    company_id: Optional[int] = None


class TokenResponse(BaseModel):
    """Schema for JWT authentication response."""

    access_token: str
    token_type: str = "bearer"
    user: UserResponse

