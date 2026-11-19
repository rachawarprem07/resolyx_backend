import smtplib
from datetime import datetime, timezone
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.company import Company
from app.models.email_account import EmailAccountModel

router = APIRouter()


class EmailAccountPayload(BaseModel):
    id: Optional[str] = None
    provider: str = Field("gmail", max_length=50)
    account_name: str = Field(..., min_length=1, max_length=255)
    email: str = Field(..., min_length=3, max_length=255)
    password: Optional[str] = None
    enable_incoming: bool = True
    enable_outgoing: bool = True
    default_incoming: bool = False
    default_outgoing: bool = False
    smtp_host: str = Field("smtp.gmail.com", max_length=255)
    smtp_port: int = 587
    smtp_encryption: str = Field("tls", max_length=20)
    imap_host: str = Field("imap.gmail.com", max_length=255)
    imap_port: int = 993


class EmailAccountResponse(BaseModel):
    id: int
    provider: str
    account_name: str
    email: str
    enable_incoming: bool
    enable_outgoing: bool
    default_incoming: bool
    default_outgoing: bool
    smtp_host: str
    smtp_port: int
    smtp_encryption: str
    imap_host: str
    imap_port: int
    status: str
    created_at: datetime
    updated_at: datetime


class TestConnectionPayload(BaseModel):
    email: str
    password: str
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587


@router.get(
    "",
    response_model=List[EmailAccountResponse],
    status_code=status.HTTP_200_OK,
    summary="List Configured Email Accounts",
)
def list_email_accounts(db: Session = Depends(get_db)) -> List[EmailAccountResponse]:
    """Retrieves all configured email accounts from PostgreSQL."""
    accounts = db.scalars(select(EmailAccountModel).order_by(EmailAccountModel.id.asc())).all()
    return [
        EmailAccountResponse(
            id=a.id,
            provider=a.provider,
            account_name=a.account_name,
            email=a.email,
            enable_incoming=a.enable_incoming,
            enable_outgoing=a.enable_outgoing,
            default_incoming=a.default_incoming,
            default_outgoing=a.default_outgoing,
            smtp_host=a.smtp_host,
            smtp_port=a.smtp_port,
            smtp_encryption=a.smtp_encryption,
            imap_host=a.imap_host,
            imap_port=a.imap_port,
            status=a.status,
            created_at=a.created_at,
            updated_at=a.updated_at,
        )
        for a in accounts
    ]


@router.post(
    "",
    response_model=EmailAccountResponse,
    status_code=status.HTTP_200_OK,
    summary="Configure / Save Email Account",
)
def upsert_email_account(
    payload: EmailAccountPayload,
    db: Session = Depends(get_db),
) -> EmailAccountResponse:
    """Saves or updates an email account (e.g. Gmail) in PostgreSQL."""
    clean_email = payload.email.strip().lower()
    clean_name = payload.account_name.strip()
    clean_pw = payload.password.replace(" ", "").strip() if payload.password else None

    company = db.scalar(select(Company).order_by(Company.id.asc()))
    company_id = company.id if company else None

    # Check if this account already exists
    existing = db.scalar(select(EmailAccountModel).where(EmailAccountModel.email == clean_email))

    # If marked as default, unmark other accounts
    if payload.default_incoming:
        all_accs = db.scalars(select(EmailAccountModel)).all()
        for a in all_accs:
            a.default_incoming = False

    if payload.default_outgoing:
        all_accs = db.scalars(select(EmailAccountModel)).all()
        for a in all_accs:
            a.default_outgoing = False

    if existing:
        existing.account_name = clean_name
        existing.provider = payload.provider
        if clean_pw:
            existing.password = clean_pw
        existing.enable_incoming = payload.enable_incoming
        existing.enable_outgoing = payload.enable_outgoing
        existing.default_incoming = payload.default_incoming
        existing.default_outgoing = payload.default_outgoing
        existing.smtp_host = payload.smtp_host
        existing.smtp_port = payload.smtp_port
        existing.smtp_encryption = payload.smtp_encryption
        existing.imap_host = payload.imap_host
        existing.imap_port = payload.imap_port
        existing.status = "connected"
        existing.updated_at = datetime.now(timezone.utc)
        target = existing
    else:
        # If this is the very first email account, make it default
        count = len(db.scalars(select(EmailAccountModel)).all())
        is_first = (count == 0)

        target = EmailAccountModel(
            company_id=company_id,
            provider=payload.provider,
            account_name=clean_name,
            email=clean_email,
            password=clean_pw,
            enable_incoming=payload.enable_incoming,
            enable_outgoing=payload.enable_outgoing,
            default_incoming=True if is_first else payload.default_incoming,
            default_outgoing=True if is_first else payload.default_outgoing,
            smtp_host=payload.smtp_host,
            smtp_port=payload.smtp_port,
            smtp_encryption=payload.smtp_encryption,
            imap_host=payload.imap_host,
            imap_port=payload.imap_port,
            status="connected",
        )
        db.add(target)

    db.commit()
    db.refresh(target)

    return EmailAccountResponse(
        id=target.id,
        provider=target.provider,
        account_name=target.account_name,
        email=target.email,
        enable_incoming=target.enable_incoming,
        enable_outgoing=target.enable_outgoing,
        default_incoming=target.default_incoming,
        default_outgoing=target.default_outgoing,
        smtp_host=target.smtp_host,
        smtp_port=target.smtp_port,
        smtp_encryption=target.smtp_encryption,
        imap_host=target.imap_host,
        imap_port=target.imap_port,
        status=target.status,
        created_at=target.created_at,
        updated_at=target.updated_at,
    )


@router.delete(
    "/{account_id}",
    status_code=status.HTTP_200_OK,
    summary="Delete Email Account",
)
def delete_email_account(
    account_id: int,
    db: Session = Depends(get_db),
):
    """Removes a configured email account."""
    acc = db.scalar(select(EmailAccountModel).where(EmailAccountModel.id == account_id))
    if not acc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Email account not found.")

    db.delete(acc)
    db.commit()
    return {"message": "Email account removed successfully."}


@router.post(
    "/test",
    status_code=status.HTTP_200_OK,
    summary="Test SMTP Relay Handshake",
)
def test_smtp_relay(payload: TestConnectionPayload):
    """Attempts an SMTP handshake with the provided credentials."""
    clean_pw = payload.password.replace(" ", "").strip()
    clean_user = payload.email.strip()

    try:
        if payload.smtp_port == 465:
            with smtplib.SMTP_SSL(payload.smtp_host, payload.smtp_port, timeout=10) as server:
                server.login(clean_user, clean_pw)
        else:
            with smtplib.SMTP(payload.smtp_host, payload.smtp_port, timeout=10) as server:
                server.ehlo()
                server.starttls()
                server.ehlo()
                server.login(clean_user, clean_pw)
        return {"success": True, "message": "SMTP handshake verified successfully!"}
    except smtplib.SMTPAuthenticationError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Authentication failed. For Gmail, please generate and use a 16-character App Password "
                "from your Google Account Security settings (myaccount.google.com/apppasswords)."
            ),
        )
    except smtplib.SMTPServerDisconnected:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Gmail server closed connection unexpectedly. If 2-Step Verification is enabled, "
                "you must use a 16-character Google App Password (myaccount.google.com/apppasswords)."
            ),
        )
    except Exception as exc:
        err_str = str(exc)
        if "Connection unexpectedly closed" in err_str or "Broken pipe" in err_str:
            detail = (
                "Gmail connection closed. Please generate a 16-character Google App Password "
                "at myaccount.google.com/apppasswords."
            )
        else:
            detail = f"SMTP handshake failed: {err_str}"
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=detail,
        )


@router.post(
    "/{account_id}/test",
    status_code=status.HTTP_200_OK,
    summary="Test Saved Email Account Connection",
)
def test_saved_email_account(
    account_id: int,
    db: Session = Depends(get_db),
):
    """Attempts an SMTP handshake using credentials already stored for an account in PostgreSQL."""
    acc = db.scalar(select(EmailAccountModel).where(EmailAccountModel.id == account_id))
    if not acc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Email account not found.")

    if not acc.password:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No password configured for this email account.",
        )

    clean_pw = acc.password.replace(" ", "").strip()
    clean_user = acc.email.strip()

    try:
        if acc.smtp_port == 465:
            with smtplib.SMTP_SSL(acc.smtp_host, acc.smtp_port, timeout=10) as server:
                server.login(clean_user, clean_pw)
        else:
            with smtplib.SMTP(acc.smtp_host, acc.smtp_port, timeout=10) as server:
                server.ehlo()
                server.starttls()
                server.ehlo()
                server.login(clean_user, clean_pw)
        acc.status = "connected"
        acc.updated_at = datetime.now(timezone.utc)
        db.commit()
        return {"success": True, "message": f"SMTP handshake with {acc.email} verified successfully!"}
    except smtplib.SMTPAuthenticationError:
        acc.status = "auth_error"
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Authentication failed. For Gmail, please generate and use a 16-character App Password "
                "from your Google Account Security settings (myaccount.google.com/apppasswords)."
            ),
        )
    except smtplib.SMTPServerDisconnected:
        acc.status = "error"
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Gmail server closed connection unexpectedly. If 2-Step Verification is enabled, "
                "you must use a 16-character Google App Password (myaccount.google.com/apppasswords)."
            ),
        )
    except Exception as exc:
        acc.status = "error"
        db.commit()
        err_str = str(exc)
        if "Connection unexpectedly closed" in err_str:
            detail = (
                "Gmail connection closed. Please generate a 16-character Google App Password "
                "at myaccount.google.com/apppasswords."
            )
        else:
            detail = f"SMTP handshake failed: {err_str}"
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=detail,
        )

