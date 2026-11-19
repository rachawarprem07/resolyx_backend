from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import JSON, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.team import Team
from app.models.user import User


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# Statuses that count as "closed" (not part of an agent's open workload).
CLOSED_TICKET_STATUSES = frozenset({"Resolved", "Closed"})

# assignment_source values
ASSIGNMENT_SOURCE_MANUAL = "manual"
ASSIGNMENT_SOURCE_RULE = "rule"


class Ticket(Base):
    """A helpdesk ticket.

    Customer details are stored on the ticket (as the existing frontend ticket
    shape does) rather than in a separate customer table. ``meta`` keeps
    presentation-only data such as the conversation thread and AI suggestion.
    """

    __tablename__ = "tickets"
    __table_args__ = (
        Index("ix_tickets_company_status", "company_id", "status"),
        Index("ix_tickets_agent_status", "assigned_agent_id", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    ticket_number: Mapped[Optional[str]] = mapped_column(String(32), unique=True, index=True, nullable=True)
    company_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("companies.id", ondelete="CASCADE"), nullable=True, index=True
    )

    subject: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(40), default="Open", nullable=False)
    priority: Mapped[str] = mapped_column(String(20), default="Medium", nullable=False)
    category: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    subcategory: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    ticket_type: Mapped[Optional[str]] = mapped_column(String(60), nullable=True)
    source: Mapped[Optional[str]] = mapped_column(String(60), nullable=True)

    customer_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    customer_email: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, index=True)
    customer_company: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    customer_type: Mapped[Optional[str]] = mapped_column(String(60), nullable=True)

    tags: Mapped[List[str]] = mapped_column(JSON, default=list, nullable=False)
    sla_status: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    due_date: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    assigned_agent_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    assigned_team_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("teams.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # "manual" (set by a person), "rule" (set by the assignment engine) or NULL
    assignment_source: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    assigned_by_rule_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("assignment_rules.id", ondelete="SET NULL"), nullable=True
    )

    created_by_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    meta: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False
    )

    assigned_agent: Mapped[Optional[User]] = relationship(foreign_keys=[assigned_agent_id], lazy="joined")
    assigned_team: Mapped[Optional[Team]] = relationship(foreign_keys=[assigned_team_id], lazy="joined")

    @property
    def is_open(self) -> bool:
        return self.status not in CLOSED_TICKET_STATUSES

    def __repr__(self) -> str:
        return f"<Ticket id={self.id} number={self.ticket_number} status={self.status}>"
