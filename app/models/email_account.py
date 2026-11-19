from datetime import datetime, timezone
from typing import Optional
from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class EmailAccountModel(Base):
    """SQLAlchemy model representing a configured inbound/outbound email account."""

    __tablename__ = "email_accounts"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True, index=True)
    company_id: Mapped[Optional[int]] = mapped_column(
        Integer,
        ForeignKey("companies.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    provider: Mapped[str] = mapped_column(String(50), default="gmail", nullable=False)
    account_name: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    password: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    enable_incoming: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    enable_outgoing: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    default_incoming: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    default_outgoing: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    smtp_host: Mapped[str] = mapped_column(String(255), default="smtp.gmail.com", nullable=False)
    smtp_port: Mapped[int] = mapped_column(Integer, default=587, nullable=False)
    smtp_encryption: Mapped[str] = mapped_column(String(20), default="tls", nullable=False)

    imap_host: Mapped[str] = mapped_column(String(255), default="imap.gmail.com", nullable=False)
    imap_port: Mapped[int] = mapped_column(Integer, default=993, nullable=False)

    status: Mapped[str] = mapped_column(String(50), default="connected", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    def __repr__(self) -> str:
        return f"<EmailAccount id={self.id} email={self.email} provider={self.provider}>"

